"""Hermes Crew — Hermes plugin adapter.

``register(ctx)`` wires Hermes hooks to the team state in ``<team dir>/.team/``:

* state tracking   on_session_start / pre_llm_call / on_session_end / on_human_input_* /
                   on_session_finalize / on_session_reset → ``agents/<Role>.json``
* usage            post_api_request (tokens) · api_request_error (last_error)
* heartbeat        daemon thread, every 15 s
* mail delivery    daemon thread, every 1 s — injects pending mailbox messages as ONE turn, and
                   only while this agent is idle (``inject_message`` interrupts a busy turn)
* ownership guard  pre_tool_call (see hermes_crew/guard.py)
* watchdog         lead only, daemon thread, every 20 s (see hermes_crew/watchdog.py)

Inert unless both HERMES_TEAM_DIR and HERMES_TEAM_ROLE are set. No callback ever raises: a
raising ``pre_tool_call`` would block the tool, so every hook is wrapped and logs instead.
Thread loops call ``*_tick()`` methods that tests drive directly.
"""
from __future__ import annotations

import atexit
import functools
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from .hermes_crew import __version__, board, events, guard, mailbox, state, tasks, watchdog
from .hermes_crew.team import SYSTEM, Team, same_role

log = logging.getLogger("hermes_crew")

HEARTBEAT_S = 15.0
MAIL_S = 1.0
WATCHDOG_S = 20.0
STARTUP_GRACE_S = 30.0       # deliver mail only after the first turn (the brief) started, or after this
INJECT_SETTLE_S = 30.0       # after an inject, wait for its turn to start before injecting again
ERROR_MAX = 200


def _safe(default: Any = None) -> Callable:
    """Decorator: never let an exception escape a hook/tick; log it and return ``default``."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception:  # noqa: BLE001
                log.exception("hermes-crew: %s failed", fn.__name__)
                return default
        return wrapper
    return deco


def _short(text: Any, n: int = ERROR_MAX) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class CrewPlugin:
    """One agent's view of the team. Hook methods are named after the Hermes hooks."""

    HOOKS = ("on_session_start", "pre_llm_call", "on_session_end", "on_session_finalize",
             "on_session_reset", "on_human_input_request", "on_human_input_resolved",
             "post_api_request", "api_request_error", "pre_tool_call", "post_tool_call")

    def __init__(self, ctx: Any, team: Team, role: str, *, clock: Callable[[], float] = time.time,
                 tmux_runner: Callable[[list[str]], Any] | None = None):
        self.ctx = ctx
        self.team = team
        self.role = role
        self.clock = clock
        self.is_lead = role.lower() == "lead"
        self.auto_own = os.environ.get("HERMES_CREW_AUTO_OWN", "1") != "0"
        self.lock = threading.RLock()
        self.state = "starting"
        self.turn_seen = False             # first pre_llm_call (the --query-file brief) happened
        self.started = clock()
        self.injected_at: float | None = None   # inject accepted, its turn not started yet
        self.cwd: str | None = None        # terminal session cwd, learned from terminal results
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.tmux_runner = tmux_runner or _run_tmux
        self._owners_key: tuple | None = None
        self._owners: dict[str, str] = {}

    # ------------------------------------------------------------------ state
    def _set(self, new_state: str | None = None, **fields: Any) -> None:
        """Update the in-memory state (authoritative for delivery) and the on-disk record."""
        with self.lock:
            if new_state:
                self.state = new_state
        try:
            changes = dict(fields)
            if new_state:
                changes["state"] = new_state
            state.update(self.team, self.role, **changes)
        except Exception:  # noqa: BLE001
            log.exception("hermes-crew: writing agent state failed")

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            events.emit(self.team, self.role, kind, **fields)
        except Exception:  # noqa: BLE001
            log.exception("hermes-crew: event %s failed", kind)

    def start_record(self) -> None:
        now = self.clock()
        self._set("starting", pid=os.getpid(), pane=os.environ.get("TMUX_PANE"),
                  session=os.environ.get("HERMES_CREW_SESSION"), started=now, heartbeat=now,
                  plugin_version=__version__, turn_id=None, blocked_kind=None, last_error=None)

    # ------------------------------------------------------------------ hooks
    @_safe()
    def on_session_start(self, **kw: Any) -> None:
        fields = {k: kw[k] for k in ("model",) if kw.get(k)}
        if kw.get("session_id"):
            fields["session_id"] = kw["session_id"]
        if self.state == "starting":
            self._set("idle", **fields)
        elif fields:
            self._set(None, **fields)

    @_safe()
    def pre_llm_call(self, **kw: Any) -> None:
        # NOTE: must return None — pre_llm_call return values are injected into the user message.
        with self.lock:
            self.turn_seen = True
            self.injected_at = None
        fields: dict[str, Any] = {"turn_id": kw.get("turn_id")}
        if kw.get("model"):
            fields["model"] = kw["model"]
        self._set("busy", **fields)
        return None

    @_safe()
    def on_session_end(self, **kw: Any) -> None:
        failed = bool(kw.get("failed"))
        interrupted = bool(kw.get("interrupted"))
        rec = state.read(self.team, self.role) or {}
        fields: dict[str, Any] = {"turns": _int(rec.get("turns")) + 1, "blocked_kind": None}
        reason = kw.get("turn_exit_reason") or kw.get("reason")
        if failed:
            err = _short(rec.get("last_error") or reason or "turn failed")
            fields["last_error"] = err
            self._emit("error", turn_id=kw.get("turn_id"), reason=_short(reason or ""), error=err)
        else:
            fields["last_error"] = None
            if interrupted:
                self._emit("turn.interrupted", turn_id=kw.get("turn_id"), reason=_short(reason or ""))
        if self.state != "exited":
            self._set("idle", **fields)

    @_safe()
    def on_human_input_request(self, **kw: Any) -> None:
        self._set("blocked", blocked_kind=str(kw.get("kind") or "input"))

    @_safe()
    def on_human_input_resolved(self, **kw: Any) -> None:
        if self.state == "blocked":
            self._set("busy", blocked_kind=None)

    @_safe()
    def on_session_finalize(self, **kw: Any) -> None:
        # /new finalizes the old session (reason "session_boundary") but the process lives on;
        # on_session_reset follows. Everything else (quit, one-shot end) is a real exit.
        if kw.get("reason") == "session_boundary":
            return
        self.mark_exited(str(kw.get("reason") or "finalize"))

    @_safe()
    def on_session_reset(self, **kw: Any) -> None:
        if self.state in ("exited", "starting", "busy"):
            self._set("idle", turn_id=None)

    @_safe()
    def post_api_request(self, **kw: Any) -> None:
        usage = kw.get("usage")
        if not isinstance(usage, dict):
            return
        t_in = usage.get("prompt_tokens")
        if t_in is None:
            t_in = usage.get("input_tokens")
        t_out = usage.get("output_tokens")
        if t_out is None:
            t_out = usage.get("completion_tokens")
        t_in, t_out = _int(t_in), _int(t_out)
        if t_in or t_out:
            state.add_tokens(self.team, self.role, t_in, t_out)
            self._emit("usage", tokens_in=t_in, tokens_out=t_out, model=kw.get("model"))

    @_safe()
    def api_request_error(self, **kw: Any) -> None:
        err = kw.get("error")
        if isinstance(err, dict):
            text = f"{err.get('type') or 'error'}: {err.get('message') or ''}"
        else:
            text = str(err or kw.get("reason") or "API request failed")
        if kw.get("status_code"):
            text = f"HTTP {kw['status_code']} {text}"
        self._set(None, last_error=_short(text))

    @_safe()
    def post_tool_call(self, **kw: Any) -> None:
        if kw.get("tool_name") not in guard.TERMINAL_TOOLS:
            return
        result = kw.get("result")
        if isinstance(result, str) and '"cwd"' in result:
            try:
                data = json.loads(result)
            except ValueError:
                return
            cwd = data.get("cwd") if isinstance(data, dict) else None
            if isinstance(cwd, str) and os.path.isabs(cwd):
                self.cwd = cwd

    def pre_tool_call(self, **kw: Any) -> dict | None:
        try:
            return self._guard(kw.get("tool_name"), kw.get("args"))
        except Exception:  # noqa: BLE001 - a raising pre_tool_call would block the tool
            log.exception("hermes-crew: guard failed (allowing the tool call)")
            return None

    # ------------------------------------------------------------------ guard
    def _base_cwd(self) -> str:
        return self.cwd or os.environ.get("TERMINAL_CWD") or os.getcwd()

    def owners(self) -> dict[str, str]:
        """Current owner per team-relative file, cached on board.log's (mtime, size)."""
        path = self.team.path("board.log")
        try:
            st = os.stat(path)
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        if key != self._owners_key:
            self._owners = _board_owners(self.team)
            self._owners_key = key
        return self._owners

    def _guard(self, tool: Any, args: Any) -> dict | None:
        if tool not in guard.FILE_TOOLS and tool not in guard.TERMINAL_TOOLS:
            return None
        owners = self.owners()
        d = guard.evaluate(str(tool), args if isinstance(args, dict) else {},
                           team_dir=str(self.team.root), role=self.role, owners=owners,
                           cwd=self._base_cwd(), auto_own=self.auto_own)
        if d.blocked:
            self._emit("board.blocked", tool=tool, message=d.message)
            return d.hook_result()
        for rel in d.claim:
            owner = _board_claim(self.team, self.role, rel)
            if owner and not same_role(owner, self.role):
                msg = guard.block_message(rel, owner)
                self._emit("board.blocked", tool=tool, message=msg)
                return {"action": "block", "message": msg}
        return None

    # ------------------------------------------------------------------ ticks
    def heartbeat_tick(self) -> None:
        if self.state == "exited":
            return
        try:
            state.heartbeat(self.team, self.role, os.getpid())
        except Exception:  # noqa: BLE001
            log.exception("hermes-crew: heartbeat failed")

    def deliverable(self, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        if self.state != "idle":
            return False
        if not self.turn_seen and now - self.started < STARTUP_GRACE_S:
            return False
        if self.injected_at is not None and now - self.injected_at < INJECT_SETTLE_S:
            return False
        return True

    @_safe(default=0)
    def mail_tick(self, now: float | None = None) -> int:
        """Deliver all pending messages as one injected turn if idle. Returns #delivered."""
        now = self.clock() if now is None else now
        if self.state == "starting" and now - self.started >= STARTUP_GRACE_S:
            # resumed session without a first query: no on_session_start/pre_llm_call will come
            self._set("idle")
        if not self.deliverable(now) or not mailbox.pending(self.team, self.role):
            return 0
        with self.lock:
            if not self.deliverable(now):
                return 0
            msgs = mailbox.claim_all(self.team, self.role)
            if not msgs:
                return 0
            ok = False
            try:
                ok = bool(self.ctx.inject_message(mailbox.render(msgs)))
            except Exception:  # noqa: BLE001
                log.exception("hermes-crew: inject_message failed")
            if not ok:
                for m in msgs:
                    mailbox.unclaim(self.team, self.role, m["id"])
                return 0
            self.injected_at = now
        mailbox.mark_delivered(self.team, self.role, msgs)
        return len(msgs)

    @_safe(default=[])
    def watchdog_tick(self, now: float | None = None) -> list:
        """Lead only: expire mail, detect escalations, orphan dead agents' tasks, notify lead."""
        now = self.clock() if now is None else now
        expired = mailbox.expire(self.team, now)
        agents = state.all_agents(self.team)
        task_list = _task_list(self.team)
        escs = watchdog.check(now, agents, task_list, unapproved=_unapproved(self.team),
                              expired=expired, lead=self.role)
        new = watchdog.filter_new(self.team, escs)
        for e in new:
            for tid in e.orphan_tasks:
                _orphan(self.team, tid, f"{e.role} died")
            mailbox.send(self.team, SYSTEM, self.role, e.text)
            events.emit(self.team, SYSTEM, "escalation", esc=e.kind, key=e.key, about=e.role,
                        text=e.text[:500])
            self._display(e.text)
        return new

    def _display(self, text: str) -> None:
        if not os.environ.get("TMUX"):
            return
        first = text.splitlines()[0] if text else ""
        args = ["display-message", "-d", "8000"]
        if os.environ.get("TMUX_PANE"):
            args += ["-t", os.environ["TMUX_PANE"]]
        args.append(("hermes-crew: " + first).replace("#", "##")[:200])
        try:
            self.tmux_runner(args)
        except Exception:  # noqa: BLE001
            log.debug("hermes-crew: tmux display-message failed", exc_info=True)

    def mark_exited(self, reason: str = "exit") -> None:
        if self.state == "exited":
            return
        self.stop.set()
        self._set("exited", turn_id=None, blocked_kind=None)
        self._emit("agent.exit", reason=reason)

    # ------------------------------------------------------------------ wiring
    def register_hooks(self) -> None:
        for name in self.HOOKS:
            self.ctx.register_hook(name, getattr(self, name))

    def start_threads(self) -> None:
        loops = [("hermes-crew-heartbeat", HEARTBEAT_S, self.heartbeat_tick),
                 ("hermes-crew-mail", MAIL_S, self.mail_tick)]
        if self.is_lead:
            loops.append(("hermes-crew-watchdog", WATCHDOG_S, self.watchdog_tick))
        for name, every, tick in loops:
            t = threading.Thread(target=self._loop, args=(every, tick), name=name, daemon=True)
            t.start()
            self.threads.append(t)

    def _loop(self, every: float, tick: Callable[[], Any]) -> None:
        while not self.stop.wait(every):
            try:
                tick()
            except Exception:  # noqa: BLE001
                log.exception("hermes-crew: %s tick failed", threading.current_thread().name)


# ---------------------------------------------------------------------- core shims

def _board_owners(team: Team) -> dict[str, str]:
    return dict(board.owners(team))


def _board_claim(team: Team, role: str, rel: str) -> str | None:
    """Auto-own ``rel`` for ``role`` (board OWN line + board.own event). Returns the owner
    afterwards: ``role`` on success, the other role if it got there first."""
    try:
        board.own(team, role, rel)
        return role
    except board.BoardError:
        return board.owner_of(team, rel)


def _task_list(team: Team) -> list[dict]:
    return tasks.list_tasks(team)


def _unapproved(team: Team) -> list[tuple[str, str]]:
    """Owned files without a current (non-stale) approval from a non-owner."""
    return [(f["file"], f["owner"]) for f in board.files(team) if f.get("owner") and not f.get("ok")]


def _orphan(team: Team, task_id: str, reason: str) -> None:
    try:
        tasks.orphan(team, SYSTEM, task_id, reason)
    except Exception:  # noqa: BLE001
        log.exception("hermes-crew: orphaning %s failed", task_id)


def _run_tmux(args: list[str]) -> None:
    if shutil.which("tmux"):
        subprocess.run(["tmux", *args], capture_output=True, timeout=3, check=False)


# ---------------------------------------------------------------------- entry point

def register(ctx: Any, *, start_threads: bool = True) -> CrewPlugin | None:
    """Hermes entry point. Returns the plugin object (Hermes ignores it; tests use it)."""
    team_dir = (os.environ.get("HERMES_TEAM_DIR") or "").strip()
    role = (os.environ.get("HERMES_TEAM_ROLE") or "").strip()
    if not team_dir or not role:
        return None
    try:
        team = Team(team_dir).ensure()
        plugin = CrewPlugin(ctx, team, role)
        plugin.start_record()
        plugin.register_hooks()
        if start_threads:
            plugin.start_threads()
            atexit.register(_safe()(plugin.mark_exited), "process exit")
        return plugin
    except Exception:  # noqa: BLE001 - never break Hermes startup
        log.exception("hermes-crew: register failed; plugin inactive")
        return None
