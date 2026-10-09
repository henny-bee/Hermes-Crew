"""`.team/events.jsonl`: append-only log of everything that happens in the team."""
from __future__ import annotations

import time

from . import store
from .team import Team

KINDS = ("msg.queued", "msg.delivered", "msg.expired", "state", "task.", "verify", "board.",
         "board.blocked", "error", "usage", "escalation", "agent.spawn", "agent.exit", "turn.interrupted")


def emit(team: Team, role: str, kind: str, **fields) -> dict:
    """Append one event `{"ts","t","role","kind",...fields}` under the events lock; returns it."""
    ts = time.time()
    ev = {"ts": round(ts, 3), "t": store.now_iso(ts), "role": role, "kind": kind}
    for k, v in fields.items():
        if k not in ev:
            ev[k] = v
    with team.lock("events"):
        store.append_jsonl(team.path("events.jsonl"), ev)
    return ev


def read(team: Team, n: int | None = None, kind: str | None = None,
         role: str | None = None, since: float | None = None) -> list[dict]:
    """Events oldest-first, filtered by kind prefix / role (case-insensitive) / ts, last `n` kept."""
    evs = store.read_jsonl(team.path("events.jsonl"))
    if kind:
        evs = [e for e in evs if str(e.get("kind", "")).startswith(kind)]
    if role:
        evs = [e for e in evs if str(e.get("role", "")).lower() == role.lower()]
    if since is not None:
        evs = [e for e in evs if float(e.get("ts") or 0) >= since]
    if n is not None:
        evs = evs[-n:] if n > 0 else []
    return evs


def format_event(e: dict) -> str:
    """One human-readable line: `HH:MM:SS role kind key=value ...`."""
    t = str(e.get("t", ""))[11:19] or "?"
    extra = " ".join(f"{k}={_short(v)}" for k, v in e.items() if k not in ("ts", "t", "role", "kind"))
    return f"{t} {e.get('role', '?'):<12} {e.get('kind', '?'):<14} {extra}".rstrip()


def _short(v, n: int = 60) -> str:
    s = v if isinstance(v, str) else store.dumps(v)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
