"""Task registry: `.team/tasks.jsonl`, event-sourced (current state = fold of all lines).

Every line: {"ts","t","by","op","id",...}. All check-then-append happens under the `tasks` lock.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import events, mailbox, store
from .team import SYSTEM, Team, is_lead, same_role

STATUSES = ("open", "claimed", "in_progress", "blocked", "done", "orphaned")
UNFINISHED = ("open", "claimed", "in_progress", "blocked", "orphaned")
EDITABLE = ("title", "desc", "priority", "deps")
ID_RE = re.compile(r"^[Tt]?(\d+)$")


class TaskError(Exception):
    """A refused task operation; str() is the message for the agent (what to do next)."""


def norm_id(task_id: str) -> str:
    m = ID_RE.match(str(task_id).strip())
    if not m:
        raise TaskError(f"REFUSED: '{task_id}' is not a task id (expected T<number>, e.g. T3)")
    return f"T{int(m.group(1))}"


def _num(task_id: str) -> int:
    return int(task_id[1:])


# ---------------------------------------------------------------- fold
def _apply(tasks: dict[str, dict], ln: dict) -> None:
    op, tid = ln.get("op"), ln.get("id")
    if op == "add":
        tasks[tid] = {
            "id": tid, "title": ln.get("title", ""), "deps": list(ln.get("deps") or []),
            "priority": int(ln.get("priority") or 3), "owner": ln.get("owner"), "desc": ln.get("desc"),
            "status": "claimed" if ln.get("owner") else "open", "created_by": ln.get("by"),
            "created": ln.get("t"), "updated": ln.get("t"), "reason": None, "handoff": None,
            "prev_owner": None,
        }
        return
    t = tasks.get(tid)
    if t is None:
        return
    t["updated"] = ln.get("t")
    if op == "claim":
        t.update(owner=ln.get("by"), status="claimed", reason=None)
    elif op == "assign":
        t["owner"] = ln.get("owner")
        if t["status"] in ("open", "orphaned"):
            t["status"] = "claimed"
        t["reason"] = None
    elif op == "start":
        t.update(status="in_progress", reason=None)
    elif op == "block":
        t.update(status="blocked", reason=ln.get("reason"))
    elif op == "unblock":
        t.update(status="in_progress", reason=None)
    elif op == "done":
        t.update(status="done", handoff=ln.get("handoff"), reason=None)
    elif op == "reopen":
        t.update(status="claimed" if t["owner"] else "open", reason=ln.get("reason"))
    elif op == "orphan":
        t.update(status="orphaned", prev_owner=t["owner"], owner=None, reason=ln.get("reason"))
    elif op == "edit":
        for k, v in (ln.get("fields") or {}).items():
            if k in EDITABLE:
                t[k] = v


def fold(lines: list[dict]) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    for ln in lines:
        _apply(tasks, ln)
    for t in tasks.values():
        waiting = [d for d in t["deps"] if tasks.get(d, {}).get("status") != "done"]
        t["waiting_on"] = waiting
        t["ready"] = not waiting
    return tasks


def _load(team: Team) -> tuple[list[dict], dict[str, dict]]:
    lines = store.read_jsonl(team.path("tasks.jsonl"))
    return lines, fold(lines)


def list_tasks(team: Team) -> list[dict]:
    """All tasks in id order. Keys: id title deps priority owner desc status ready waiting_on
    created_by created updated reason handoff prev_owner."""
    return sorted(_load(team)[1].values(), key=lambda t: _num(t["id"]))


def get(team: Team, task_id: str) -> dict | None:
    return _load(team)[1].get(norm_id(task_id))


def history(team: Team, task_id: str) -> list[dict]:
    tid = norm_id(task_id)
    return [ln for ln in store.read_jsonl(team.path("tasks.jsonl")) if ln.get("id") == tid]


# ---------------------------------------------------------------- write helpers
def _append(team: Team, by: str, op: str, tid: str, **fields) -> dict:
    ts = time.time()
    ln = {"ts": round(ts, 3), "t": store.now_iso(ts), "by": by, "op": op, "id": tid, **fields}
    store.append_jsonl(team.path("tasks.jsonl"), ln)
    return ln


def _need(tasks: dict[str, dict], task_id: str) -> dict:
    tid = norm_id(task_id)
    t = tasks.get(tid)
    if t is None:
        raise TaskError(f"REFUSED: no task {tid} - see: hermes-crew task list")
    return t


def _desc(tasks: dict[str, dict], tid: str) -> str:
    t = tasks.get(tid)
    if not t:
        return f"{tid} (missing)"
    return f"{tid} ({t['status']}{', ' + t['owner'] if t.get('owner') else ''})"


def _must_be_owner_or_lead(t: dict, by: str, what: str) -> None:
    if is_lead(by) or by == SYSTEM or same_role(t.get("owner"), by):
        return
    if not t.get("owner"):
        raise TaskError(f"REFUSED: {t['id']} has no owner - claim it first: hermes-crew task claim {t['id']}")
    raise TaskError(f"REFUSED: {t['id']} is owned by {t['owner']} - only {t['owner']} or lead can {what} it; "
                    f"ask {t['owner']}: hermes-crew send {t['owner']} \"...\"")


def _check_deps(tasks: dict[str, dict], tid: str, deps: list[str]) -> list[str]:
    deps = [norm_id(d) for d in deps]
    unknown = [d for d in deps if d not in tasks]
    if unknown:
        raise TaskError(f"REFUSED: unknown dependency {', '.join(unknown)} - see: hermes-crew task list")
    if tid in deps:
        raise TaskError(f"REFUSED: {tid} cannot depend on itself")
    # cycle: does any dep (transitively) depend on tid?
    graph = {k: list(v["deps"]) for k, v in tasks.items()}
    graph[tid] = deps
    path = _find_cycle(graph, tid)
    if path:
        raise TaskError(f"REFUSED: dependency cycle {' -> '.join(path)} - remove one of these deps")
    seen = []
    for d in deps:
        if d not in seen:
            seen.append(d)
    return seen


def _find_cycle(graph: dict[str, list[str]], start: str) -> list[str] | None:
    stack = [(start, [start])]
    visited = set()
    while stack:
        node, path = stack.pop()
        for d in graph.get(node, []):
            if d == start:
                return path + [d]
            if d not in visited:
                visited.add(d)
                stack.append((d, path + [d]))
    return None


def _emit(team: Team, ln: dict) -> None:
    extra = {k: v for k, v in ln.items() if k not in ("ts", "t", "by", "op", "id")}
    events.emit(team, ln["by"], f"task.{ln['op']}", id=ln["id"], **extra)


# ---------------------------------------------------------------- operations
def add(team: Team, by: str, title: str, deps: list[str] | None = None, priority: int = 3,
        owner: str | None = None, desc: str | None = None) -> dict:
    """Create a task; returns it. Rejects unknown deps and cycles."""
    title = (title or "").strip()
    if not title:
        raise TaskError("REFUSED: a task needs a title: hermes-crew task add \"<title>\"")
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        raise TaskError(f"REFUSED: --priority must be a number (1 = highest), got {priority!r}") from None
    if priority < 1:
        raise TaskError("REFUSED: --priority must be >= 1 (1 = highest)")
    with team.lock("tasks"):
        _, tasks = _load(team)
        tid = f"T{max((_num(k) for k in tasks), default=0) + 1}"
        deps = _check_deps(tasks, tid, deps or [])
        fields = {"title": title, "deps": deps, "priority": priority}
        if owner:
            fields["owner"] = owner
        if desc:
            fields["desc"] = desc
        ln = _append(team, by, "add", tid, **fields)
        tasks = fold(store.read_jsonl(team.path("tasks.jsonl")))
    _emit(team, ln)
    return tasks[tid]


def claim(team: Team, by: str, task_id: str) -> dict:
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        tid = t["id"]
        if same_role(t.get("owner"), by) and t["status"] != "done":
            return t
        if t["status"] not in ("open", "orphaned"):
            if t["status"] == "done":
                raise TaskError(f"REFUSED: {tid} is already done - if it needs more work: "
                                f"hermes-crew task reopen {tid} \"<reason>\"")
            raise TaskError(f"REFUSED: {tid} is {t['status']} by {t['owner']} - ask {t['owner']} or lead")
        if not t["ready"]:
            waiting = ", ".join(_desc(tasks, d) for d in t["waiting_on"])
            raise TaskError(f"REFUSED: {tid} is not ready - waiting for {waiting}. "
                            f"Pick a ready task (hermes-crew task list) or wait for the unblock message")
        ln = _append(team, by, "claim", tid)
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[tid]
    _emit(team, ln)
    return t


def assign(team: Team, by: str, task_id: str, owner: str) -> dict:
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        if t["status"] == "done":
            raise TaskError(f"REFUSED: {t['id']} is done - reopen it first: hermes-crew task reopen {t['id']} \"<reason>\"")
        if t.get("owner") and not (is_lead(by) or by == SYSTEM or same_role(t["owner"], by)):
            raise TaskError(f"REFUSED: {t['id']} is owned by {t['owner']} - only {t['owner']} or lead can reassign it")
        ln = _append(team, by, "assign", t["id"], owner=owner)
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    if not same_role(owner, by):
        mailbox.send(team, SYSTEM, owner, f"{by} assigned you {t['id']} '{t['title']}'"
                     + (f". Start with: hermes-crew task start {t['id']}" if t["ready"] else
                        f". It waits for {', '.join(t['waiting_on'])}; you get a message when it is unblocked"))
    return t


def start(team: Team, by: str, task_id: str) -> dict:
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        _must_be_owner_or_lead(t, by, "start")
        if not t.get("owner"):
            raise TaskError(f"REFUSED: {t['id']} has no owner - assign it first: hermes-crew task assign {t['id']} <Role>")
        if t["status"] == "in_progress":
            return t
        if t["status"] not in ("claimed", "blocked"):
            raise TaskError(f"REFUSED: {t['id']} is {t['status']} - only a claimed task can be started")
        if not t["ready"]:
            waiting = ", ".join(_desc(tasks, d) for d in t["waiting_on"])
            raise TaskError(f"REFUSED: {t['id']} is not ready - waiting for {waiting}")
        ln = _append(team, by, "start", t["id"])
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    return t


def block(team: Team, by: str, task_id: str, reason: str) -> dict:
    if not (reason or "").strip():
        raise TaskError("REFUSED: say why it is blocked: hermes-crew task block <id> \"<reason>\"")
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        _must_be_owner_or_lead(t, by, "block")
        if t["status"] not in ("claimed", "in_progress", "blocked"):
            raise TaskError(f"REFUSED: {t['id']} is {t['status']} - only a claimed or started task can be blocked")
        ln = _append(team, by, "block", t["id"], reason=reason.strip())
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    return t


def unblock(team: Team, by: str, task_id: str) -> dict:
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        _must_be_owner_or_lead(t, by, "unblock")
        if t["status"] != "blocked":
            raise TaskError(f"REFUSED: {t['id']} is {t['status']}, not blocked")
        ln = _append(team, by, "unblock", t["id"])
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    return t


def done(team: Team, by: str, task_id: str, handoff: str | Path | None = None) -> dict:
    """Mark done. `handoff` = a file whose text is copied to .team/handoff/<id>.md.
    Newly ready dependents: owner (or lead if unowned) gets a message from hermes-crew."""
    if handoff is not None and not Path(handoff).is_file():
        raise TaskError(f"REFUSED: handoff file {handoff} does not exist - write it first, then rerun")
    with team.lock("tasks"):
        _, before = _load(team)
        t = _need(before, task_id)
        tid = t["id"]
        _must_be_owner_or_lead(t, by, "finish")
        if t["status"] == "done":
            raise TaskError(f"REFUSED: {tid} is already done")
        if t["status"] in ("open", "orphaned"):
            raise TaskError(f"REFUSED: {tid} is {t['status']} - claim it first: hermes-crew task claim {tid}")
        fields = {}
        if handoff is not None:
            dst = team.path("handoff", f"{tid}.md")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if Path(handoff).resolve() != dst.resolve():
                shutil.copyfile(handoff, dst)
            fields["handoff"] = f".team/handoff/{tid}.md"
        ln = _append(team, by, "done", tid, **fields)
        after = fold(store.read_jsonl(team.path("tasks.jsonl")))
    _emit(team, ln)
    _notify_unblocked(team, before, after)
    return after[tid]


def _notify_unblocked(team: Team, before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    sent = []
    for tid in sorted(after, key=_num):
        a, b = after[tid], before.get(tid)
        if not a["deps"] or a["status"] == "done" or not a["ready"] or (b and b["ready"]):
            continue
        text = f"{tid} '{a['title']}' is unblocked (deps done: {', '.join(a['deps'])})"
        if a.get("owner"):
            mailbox.send(team, SYSTEM, a["owner"], f"{text}. Start it: hermes-crew task start {tid}")
        else:
            mailbox.send(team, SYSTEM, "lead", f"{text} - it has no owner: hermes-crew task assign {tid} <Role>")
        sent.append(tid)
    return sent


def reopen(team: Team, by: str, task_id: str, reason: str) -> dict:
    if not (reason or "").strip():
        raise TaskError("REFUSED: say what is wrong: hermes-crew task reopen <id> \"<reason>\"")
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        if t["status"] != "done":
            raise TaskError(f"REFUSED: {t['id']} is {t['status']}, not done - nothing to reopen")
        ln = _append(team, by, "reopen", t["id"], reason=reason.strip())
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    if t.get("owner") and not same_role(t["owner"], by):
        mailbox.send(team, SYSTEM, t["owner"], f"{by} reopened {t['id']} '{t['title']}': {reason.strip()}")
    return t


def orphan(team: Team, by: str, task_id: str, reason: str) -> dict | None:
    """Mark an unfinished, owned task orphaned (its owner died). No-op (None) if done/unowned."""
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        if not (is_lead(by) or by == SYSTEM):
            raise TaskError(f"REFUSED: only lead can orphan {t['id']}")
        if t["status"] in ("done", "orphaned", "open") or not t.get("owner"):
            return None
        ln = _append(team, by, "orphan", t["id"], reason=reason)
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    return t


def orphan_tasks_of(team: Team, by: str, role: str, reason: str) -> list[str]:
    """Orphan every unfinished task owned by `role`; returns their ids."""
    out = []
    for t in list_tasks(team):
        if same_role(t.get("owner"), role) and t["status"] in ("claimed", "in_progress", "blocked"):
            if orphan(team, by, t["id"], reason):
                out.append(t["id"])
    return out


def edit(team: Team, by: str, task_id: str, **fields) -> dict:
    bad = [k for k in fields if k not in EDITABLE]
    if bad:
        raise TaskError(f"REFUSED: cannot edit {', '.join(bad)} (editable: {', '.join(EDITABLE)})")
    with team.lock("tasks"):
        _, tasks = _load(team)
        t = _need(tasks, task_id)
        if not (is_lead(by) or same_role(t.get("owner"), by) or same_role(t.get("created_by"), by)):
            raise TaskError(f"REFUSED: only lead, the owner or the creator can edit {t['id']}")
        if "deps" in fields:
            fields["deps"] = _check_deps(tasks, t["id"], list(fields["deps"] or []))
        if "priority" in fields:
            fields["priority"] = int(fields["priority"])
        ln = _append(team, by, "edit", t["id"], fields=fields)
        t = fold(store.read_jsonl(team.path("tasks.jsonl")))[t["id"]]
    _emit(team, ln)
    return t


def unfinished(team: Team) -> list[dict]:
    return [t for t in list_tasks(team) if t["status"] in UNFINISHED]


def mine(team: Team, role: str) -> list[dict]:
    return [t for t in list_tasks(team) if same_role(t.get("owner"), role)]
