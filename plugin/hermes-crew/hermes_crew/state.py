"""`.team/agents/<Role>.json`: the live state of each agent, written by its plugin."""
from __future__ import annotations

import os
import time

from . import __version__, events, store
from .team import Team

STATES = ("starting", "idle", "busy", "blocked", "exited")
HEARTBEAT_MAX_AGE = 60.0

DEFAULTS: dict = {
    "role": "", "pid": None, "pane": None, "model": None, "session": None,
    "state": "starting", "since": None, "turn_id": None, "task": None, "blocked_kind": None,
    "last_error": None, "tokens_in": 0, "tokens_out": 0, "turns": 0,
    "heartbeat": None, "started": None, "plugin_version": __version__,
}


def path(team: Team, role: str):
    return team.path("agents", f"{role}.json")


def read(team: Team, role: str) -> dict | None:
    """State of `role` (exact file name first, then case-insensitive match), or None."""
    rec = store.read_json(path(team, role))
    if rec is None:
        d = team.path("agents")
        if d.is_dir():
            for f in d.glob("*.json"):
                if f.stem.lower() == role.lower():
                    rec = store.read_json(f)
                    break
    if not isinstance(rec, dict):
        return None
    return {**DEFAULTS, **rec}


def all_agents(team: Team) -> list[dict]:
    """All agent records, sorted with lead first, then by role."""
    d = team.path("agents")
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            rec = store.read_json(f)
            if isinstance(rec, dict):
                out.append({**DEFAULTS, "role": f.stem, **rec})
    out.sort(key=lambda r: (str(r["role"]).lower() != "lead", str(r["role"]).lower()))
    return out


def update(team: Team, role: str, **changes) -> dict:
    """Merge `changes` into the agent's record (atomic replace, under lock `agent-<role>`).

    - `since` is set to now when `state` changes, and a `state` event is emitted (from/to/turn_id).
    - leaving `blocked` clears `blocked_kind`.
    - unknown keys are stored as-is. Returns the new record.
    """
    bad = changes.get("state")
    if bad is not None and bad not in STATES:
        raise ValueError(f"unknown state {bad!r} (one of {', '.join(STATES)})")
    now = time.time()
    with team.lock(f"agent-{role}"):
        old = read(team, role)
        rec = dict(old) if old else {**DEFAULTS, "role": role, "started": now, "since": now,
                                      "pid": os.getpid()}
        rec["role"] = old["role"] if old and old.get("role") else role
        prev = rec.get("state") if old else None
        rec.update(changes)
        new = rec.get("state")
        changed = new != prev
        if changed:
            rec["since"] = now
            if new != "blocked" and "blocked_kind" not in changes:
                rec["blocked_kind"] = None
        store.write_json_atomic(path(team, rec["role"]), rec)
    if changed:
        events.emit(team, rec["role"], "state", **{"from": prev, "to": new, "turn_id": rec.get("turn_id")})
    return rec


def set_state(team: Team, role: str, new_state: str, **fields) -> dict:
    return update(team, role, state=new_state, **fields)


def heartbeat(team: Team, role: str, pid: int | None = None) -> dict:
    return update(team, role, heartbeat=time.time(), pid=pid if pid is not None else os.getpid())


def add_tokens(team: Team, role: str, tokens_in: int = 0, tokens_out: int = 0) -> dict:
    """Atomically add usage (read-modify-write under the agent lock)."""
    with team.lock(f"agent-{role}-usage"):
        rec = read(team, role) or {}
        return update(team, role,
                      tokens_in=int(rec.get("tokens_in") or 0) + int(tokens_in or 0),
                      tokens_out=int(rec.get("tokens_out") or 0) + int(tokens_out or 0))


def pid_exists(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_alive(rec: dict | None, now: float | None = None) -> bool:
    """pid exists and heartbeat younger than 60 s."""
    if not rec:
        return False
    now = time.time() if now is None else now
    hb = rec.get("heartbeat")
    try:
        fresh = hb is not None and now - float(hb) < HEARTBEAT_MAX_AGE
    except (TypeError, ValueError):
        fresh = False
    return fresh and pid_exists(rec.get("pid"))


def is_dead(rec: dict | None, now: float | None = None) -> bool:
    """Not alive and did not exit cleanly (state != exited)."""
    return bool(rec) and rec.get("state") != "exited" and not is_alive(rec, now)
