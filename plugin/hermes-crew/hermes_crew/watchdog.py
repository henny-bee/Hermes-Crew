"""Lead-side watchdog: escalation, stall and orphan detection (pure functions + dedup store).

``check()`` looks at a snapshot (agent records, tasks, unapproved files, expired messages) and
returns ``Escalation`` objects; it performs no I/O except the optional ``alive`` probe it is
given. ``Dedup`` remembers which escalation keys were already sent (``.team/escalations.json``).

Rules (thresholds are keyword arguments so tests can vary them):
  * blocked  — a teammate waits on a human (clarify/approval/sudo) for > ``blocked_after`` s.
  * dead     — a teammate's process is gone (pid missing or heartbeat stale) while its state is
               not ``exited``; its claimed/in-progress/blocked tasks are to be orphaned.
  * failed   — a teammate's last turn failed (state idle + ``last_error``), once per turn.
  * expired  — a message expired undelivered (only when the sender was not the lead: the
               mailbox already tells the sender).
  * stall    — every live teammate has been idle ≥ ``stall_after`` s, the lead is idle too, and
               work is outstanding (tasks not done, or owned files without an approval). One
               escalation per stall episode (an episode ends when anybody changes state).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

BLOCKED_AFTER = 180.0
STALL_AFTER = 120.0
OPEN_STATUSES = ("open", "claimed", "in_progress", "blocked", "orphaned")
ORPHANABLE = ("claimed", "in_progress", "blocked")
SYSTEM_ROLES = ("lead", "user", "hermes-crew")
KEEP_KEYS = 2000


@dataclass(frozen=True)
class Escalation:
    key: str                         # dedup key (stable for one occurrence)
    kind: str                        # blocked | dead | failed | expired | stall
    text: str                        # message for the lead
    role: str | None = None          # teammate concerned (if any)
    orphan_tasks: tuple[str, ...] = field(default=())


def _num(v, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ago(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def _is_teammate(rec: Mapping) -> bool:
    return str(rec.get("role") or "").lower() not in SYSTEM_ROLES


def default_alive(rec: Mapping, now: float) -> bool:
    """pid exists and heartbeat younger than 60 s (same rule as state.is_alive)."""
    from . import state
    return state.is_alive(dict(rec), now)


def task_label(t: Mapping) -> str:
    return f"{t.get('id')} '{t.get('title', '')}'"


def check(now: float, agents: Sequence[Mapping], tasks: Sequence[Mapping] = (), *,
          unapproved: Sequence[tuple[str, str]] = (), expired: Sequence[Mapping] = (),
          alive: Callable[[Mapping, float], bool] = default_alive,
          lead: str = "lead", blocked_after: float = BLOCKED_AFTER,
          stall_after: float = STALL_AFTER) -> list[Escalation]:
    """All escalations that currently apply (not deduplicated)."""
    out: list[Escalation] = []
    mates = [a for a in agents if _is_teammate(a)]
    live = []
    for a in mates:
        role = str(a.get("role"))
        st = a.get("state")
        since = _num(a.get("since"), now) or now
        if st == "exited":
            continue
        if not alive(a, now):
            out.append(_dead(a, tasks))
            continue
        live.append(a)
        if st == "blocked" and now - since > blocked_after:
            kind = a.get("blocked_kind") or "input"
            out.append(Escalation(f"blocked:{role}:{since}", "blocked",
                                  f"{role} waits for your input in its pane: {kind} "
                                  f"(blocked for {_ago(now - since)}).", role))
        if st == "idle" and a.get("last_error"):
            err = str(a["last_error"])
            out.append(Escalation(f"failed:{role}:{a.get('turn_id')}:{err[:80]}", "failed",
                                  f"{role}'s last turn failed: {err}. It is idle now — send it a "
                                  f"message to retry, or reassign its task.", role))
    for m in expired:
        sender = str(m.get("from") or "")
        if sender.lower() in ("lead", "user", "hermes-crew") or sender.lower() == lead.lower():
            continue
        out.append(Escalation(f"expired:{m.get('id')}", "expired",
                              f"Message #{str(m.get('id', '')).rsplit('-', 1)[-1]} from {sender} to "
                              f"{m.get('to')} expired undelivered ({m.get('to')} never became idle): "
                              f"{_clip(str(m.get('body', '')), 120)}", str(m.get("to") or "") or None))
    stall = _stall(now, agents, live, tasks, unapproved, lead, stall_after)
    if stall:
        out.append(stall)
    return out


def _dead(a: Mapping, tasks: Iterable[Mapping]) -> Escalation:
    role = str(a.get("role"))
    mine = [t for t in tasks if str(t.get("owner") or "").lower() == role.lower()
            and t.get("status") in ORPHANABLE]
    ids = tuple(str(t.get("id")) for t in mine)
    text = f"{role} is gone (pid {a.get('pid')} not running, last state {a.get('state')})."
    if ids:
        text += (" Its tasks were orphaned: " + ", ".join(task_label(t) for t in mine)
                 + ". Respawn the role or reassign them (hermes-crew task assign <id> <Role>).")
    else:
        text += " It owned no open tasks."
    return Escalation(f"dead:{role}:{a.get('started')}:{a.get('pid')}", "dead", text, role, ids)


def _stall(now: float, agents: Sequence[Mapping], live: Sequence[Mapping],
           tasks: Sequence[Mapping], unapproved: Sequence[tuple[str, str]], lead: str,
           stall_after: float) -> Escalation | None:
    if not live:
        return None
    lead_rec = next((a for a in agents if str(a.get("role") or "").lower() == lead.lower()), None)
    if lead_rec is not None and lead_rec.get("state") != "idle":
        return None
    for a in live:
        if a.get("state") != "idle" or now - (_num(a.get("since"), now) or now) < stall_after:
            return None
    open_tasks = [t for t in tasks if t.get("status") in OPEN_STATUSES]
    if not open_tasks and not unapproved:
        return None
    lines = [f"STALL: all teammates have been idle for ≥ {_ago(stall_after)} and the lead is idle, "
             "but work is outstanding:"]
    for t in open_tasks:
        owner = t.get("owner") or "nobody"
        lines.append(f"- {task_label(t)} {t.get('status')} — owner {owner}")
    for f, owner in unapproved:
        lines.append(f"- {f} (owner {owner}) has no current approval from a non-owner")
    lines.append("Nudge the owners (hermes-crew send <Role> ...), reassign, or run "
                 "hermes-crew status --done-check.")
    episode = max([_num(a.get("since"), 0) or 0 for a in live]
                  + ([_num(lead_rec.get("since"), 0) or 0] if lead_rec else []))
    return Escalation(f"stall:{episode}", "stall", "\n".join(lines))


def _clip(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- dedup

def dedup(escalations: Iterable[Escalation], seen: Iterable[str]) -> tuple[list[Escalation], list[str]]:
    """(escalations whose key is new, updated key list). Pure."""
    keys = list(seen)
    known = set(keys)
    new = []
    for e in escalations:
        if e.key not in known:
            known.add(e.key)
            keys.append(e.key)
            new.append(e)
    return new, keys[-KEEP_KEYS:]


def filter_new(team, escalations: Sequence[Escalation]) -> list[Escalation]:
    """Dedup against ``.team/escalations.json`` under the ``escalations`` lock (records them)."""
    from . import store
    if not escalations:
        return []
    path = team.path("escalations.json")
    with team.lock("escalations"):
        data = store.read_json(path, {}) or {}
        seen = data.get("keys", []) if isinstance(data, dict) else []
        new, keys = dedup(escalations, seen if isinstance(seen, list) else [])
        if new:
            store.write_json_atomic(path, {"keys": keys, "updated": time.time()})
    return new
