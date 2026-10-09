"""Team status: one model (for --json), one ~80-column text rendering, and the done-check."""
from __future__ import annotations

import shutil
import time

from . import board, state, tasks, verify
from .team import Team

WIDTH = 80


def model(team: Team, now: float | None = None) -> dict:
    """Everything `status` shows, as plain data."""
    now = time.time() if now is None else now
    agents = []
    seen = set()
    for a in state.all_agents(team):
        seen.add(str(a["role"]).lower())
        agents.append({**a, "alive": state.is_alive(a, now), "dead": state.is_dead(a, now),
                       "for_s": round(now - float(a["since"]), 1) if a.get("since") else None})
    for r in team.roles():                       # known roles without a state file (plugin not loaded?)
        if r.lower() not in seen and r.lower() != "lead":
            agents.append({**state.DEFAULTS, "role": r, "state": None, "alive": None, "dead": False,
                           "for_s": None, "plugin_version": None})
    ts = tasks.list_tasks(team)
    last = verify.last_by_task(team)
    for t in ts:
        v = last.get(t["id"])
        t["verify"] = None if v is None else {k: v.get(k) for k in ("exit", "cmd", "t", "by", "duration_s")}
    return {
        "team": str(team.root), "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "agents": agents, "tasks": ts, "files": board.files(team),
        "decisions": board.current_decisions(team), "done_check": done_check(team),
    }


def done_check(team: Team) -> list[str]:
    """Violations of the definition of done; [] means done."""
    out = []
    ts = tasks.list_tasks(team)
    fs = [f for f in board.files(team) if f["owner"]]
    for t in ts:
        if t["status"] != "done":
            who = f", {t['owner']}" if t.get("owner") else ""
            why = f": {t['reason']}" if t.get("reason") else ""
            out.append(f"task {t['id']} '{_clip(t['title'], 40)}' is {t['status']}{who}{why}")
    for f in fs:
        if f["ok"]:
            continue
        stale = [a["role"] for a in f["approvals"] if a["status"] == "STALE"]
        hint = f" (STALE approval by {', '.join(stale)}: file changed since)" if stale else ""
        if f["hash"] == "missing":
            hint = " (file is missing)"
        out.append(f"file {f['file']} (owner {f['owner']}) has no OK approval from a non-owner{hint} - "
                   f"ask a reviewer: hermes-crew board approve {f['file']} \"<what was checked>\"")
    for tid, v in sorted(verify.last_by_task(team).items()):
        if v.get("exit") != 0:
            out.append(f"task {tid}: last verify failed (exit {v.get('exit')}): {_clip(str(v.get('cmd')), 40)}")
    if not ts and not fs:
        out.append("nothing recorded: no tasks and no owned files - done cannot be shown "
                   "(add tasks: hermes-crew task add, own deliverables: hermes-crew board own)")
    return out


# ---------------------------------------------------------------- rendering
def _clip(s: str, n: int) -> str:
    s = str(s or "").replace("\n", " ")
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def human_age(s: float | None) -> str:
    if s is None:
        return "-"
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    return f"{s // 86400}d"


def human_tokens(n) -> str:
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def _agent_line(a: dict, w: int) -> str:
    st = a["state"] or "?"
    note = ""
    if a["state"] is None:
        st, note = "unknown", "no state file (plugin not loaded?)"
    elif a.get("dead"):
        st, note = "DEAD", f"was {a['state']}; pid/heartbeat gone"
    elif a["state"] == "blocked":
        note = f"waits for human: {a.get('blocked_kind') or '?'}"
    if a.get("last_error") and not note:
        note = f"err: {a['last_error']}"
    tok = f"{human_tokens(a.get('tokens_in'))}/{human_tokens(a.get('tokens_out'))}"
    head = f"  {_clip(a['role'], 12):<12} {st:<8} {human_age(a.get('for_s')):>5}  {_clip(a.get('task') or '-', 5):<5} {tok:>13}  "
    return head + _clip(note, max(0, w - len(head)))


def render(m: dict, width: int | None = None) -> str:
    w = width or min(WIDTH, shutil.get_terminal_size((WIDTH, 24)).columns) or WIDTH
    L = []
    title = f"Hermes Crew · {m['team']}"
    clock = m["time"][11:19]
    L.append(_clip(title, w - len(clock) - 1).ljust(w - len(clock)) + clock)
    # agents
    L.append(f"{'AGENTS':<14} {'state':<8} {'for':>5}  {'task':<5} {'tokens in/out':>13}  note")
    if not m["agents"]:
        L.append("  (no agents yet - spawn teammates with hermes-team-spawn)")
    for a in m["agents"]:
        L.append(_agent_line(a, w))
    # tasks
    ts = m["tasks"]
    counts = {}
    for t in ts:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    summary = " · ".join(f"{counts[s]} {s.replace('_', ' ')}" for s in tasks.STATUSES if s in counts)
    L.append(f"TASKS ({summary})" if ts else "TASKS (none - hermes-crew task add \"<title>\")")
    order = {s: i for i, s in enumerate(("blocked", "orphaned", "in_progress", "claimed", "open", "done"))}
    for t in sorted(ts, key=lambda t: (order.get(t["status"], 9), t["priority"], int(t["id"][1:]))):
        if t["status"] == "done" and len(ts) > 12:
            continue
        extra = ""
        if t["status"] == "blocked" and t.get("reason"):
            extra = f"! {t['reason']}"
        elif t["status"] == "orphaned":
            extra = f"! was {t.get('prev_owner') or '?'}"
        elif t["waiting_on"] and t["status"] != "done":
            extra = f"waits {','.join(t['waiting_on'])}"
        if t.get("verify"):
            v = t["verify"]
            extra = (extra + " " if extra else "") + ("verify ok" if v["exit"] == 0 else f"VERIFY FAIL({v['exit']})")
        head = f"  {t['id']:<4} {t['status'].replace('_', ' '):<11} {_clip(t.get('owner') or '-', 10):<10} P{t['priority']} "
        room = w - len(head)
        if extra:
            tw = max(10, room - len(extra) - 2)
            L.append(head + f"{_clip(t['title'], tw):<{tw}}  " + _clip(extra, max(0, w - len(head) - tw - 2)))
        else:
            L.append(head + _clip(t["title"], room))
    if len(ts) > 12 and counts.get("done"):
        L.append(f"  (+{counts['done']} done not shown)")
    # files
    fs = m["files"]
    if fs:
        L.append("FILES (owner · approvals)")
        for f in fs:
            aps = ", ".join(f"{a['status']} {a['role']}" for a in f["approvals"]) or "no approval"
            mark = "ok" if f["ok"] and f["owner"] else "!!"
            head = f"  {mark} {_clip(f['file'], 34):<34} {_clip(f['owner'] or '(none)', 10):<10} "
            L.append(head + _clip(aps, max(0, w - len(head))))
    # decisions
    ds = m["decisions"]
    if ds:
        L.append("DECISIONS")
        for d in ds:
            L.append(_clip(f"  #{d['n']} [{d['role']}] {d['text']}", w))
    dc = m["done_check"]
    L.append("DONE: yes - every task done, every owned file approved, verifies pass" if not dc
             else f"NOT DONE: {len(dc)} open point(s) - see: hermes-crew status --done-check")
    return "\n".join(line.rstrip()[:w] if len(line) > w else line.rstrip() for line in L)


def render_done_check(violations: list[str]) -> str:
    if not violations:
        return "DONE: every task is done, every owned file has an OK approval from a non-owner, all verifies pass."
    return "NOT DONE - missing:\n" + "\n".join(f"  - {v}" for v in violations)

