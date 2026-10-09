"""Plugin adapter: registration, state tracking, usage, and mailbox delivery timing.

Hooks are fired in the order observed against real Hermes 0.21.5 (spike_events.log):
on_session_start (first turn only) → pre_llm_call → [pre_tool_call]* → post_llm_call →
on_session_end; clarify → on_human_input_request … on_human_input_resolved; Ctrl+C →
on_session_end(completed=False, interrupted=True, turn_exit_reason="interrupted_by_user").
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from plugin_fake import FakeCtx, load_plugin

TURN = "20261009_150607_da1d4b:20261009_150607_da1d4b:{}"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def P():
    return load_plugin()


@pytest.fixture
def crew(tmp_path, monkeypatch, P):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("HERMES_TEAM_DIR", str(root))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Coder")
    ctx = FakeCtx()
    plugin = P.register(ctx, start_threads=False)
    clock = Clock()
    plugin.clock = clock
    plugin.started = clock.t
    return plugin, ctx, clock


def rec(plugin):
    return json.loads((plugin.team.dir / "agents" / f"{plugin.role}.json").read_text())


def evs(plugin, kind=None):
    p = plugin.team.dir / "events.jsonl"
    out = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
    return [e for e in out if kind is None or e["kind"] == kind]


def send(P, plugin, sender, body):
    return P.mailbox.send(plugin.team, sender, plugin.role, body)


def first_turn(ctx, n="1c940174"):
    ctx.fire("on_session_start", session_id="20261009_150607_da1d4b", model="m", platform="cli")
    ctx.fire("pre_llm_call", turn_id=TURN.format(n), session_id="s", is_first_turn=True)


def end_turn(ctx, n="1c940174", **kw):
    ctx.fire("post_llm_call", turn_id=TURN.format(n))
    args = dict(completed=True, interrupted=False, failed=False,
                turn_exit_reason="text_response(finish_reason=stop)", turn_id=TURN.format(n))
    args.update(kw)
    ctx.fire("on_session_end", **args)


def turn(ctx, n):
    ctx.fire("pre_llm_call", turn_id=TURN.format(n))
    end_turn(ctx, n)


# ---------------------------------------------------------------- registration
def test_inert_without_env(P, monkeypatch, tmp_path):
    ctx = FakeCtx()
    assert P.register(ctx, start_threads=False) is None
    monkeypatch.setenv("HERMES_TEAM_DIR", str(tmp_path))
    assert P.register(ctx, start_threads=False) is None
    monkeypatch.delenv("HERMES_TEAM_DIR")
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Coder")
    assert P.register(ctx, start_threads=False) is None
    assert ctx.hooks == {}
    assert not (tmp_path / ".team").exists()


def test_register_writes_starting_and_hooks(crew, P):
    plugin, ctx, _ = crew
    r = rec(plugin)
    assert r["state"] == "starting" and r["pid"] == os.getpid()
    assert r["plugin_version"] == "3.0.0" and r["heartbeat"]
    assert set(ctx.hooks) == set(P.CrewPlugin.HOOKS)
    assert (plugin.team.dir / ".gitignore").read_text() == "*\n"
    assert plugin.threads == []


def test_register_never_raises(P, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Coder")
    monkeypatch.setattr(P.CrewPlugin, "register_hooks", lambda self: 1 / 0)
    assert P.register(FakeCtx(), start_threads=False) is None


# ---------------------------------------------------------------- state transitions
def test_turn_lifecycle_like_spike(crew):
    plugin, ctx, _ = crew
    ctx.fire("on_session_start", session_id="s1", model="m1", platform="cli")
    assert rec(plugin)["state"] == "idle"
    assert ctx.fire("pre_llm_call", turn_id=TURN.format("a")) is None   # must not inject context
    r = rec(plugin)
    assert r["state"] == "busy" and r["turn_id"] == TURN.format("a")
    assert ctx.fire("pre_tool_call", tool_name="terminal", args={"command": "sleep 12 && echo SLEPT"},
                    task_id="", session_id="s1") is None
    end_turn(ctx, "a")
    r = rec(plugin)
    assert r["state"] == "idle" and r["turns"] == 1 and r["last_error"] is None
    states = [(e["from"], e["to"]) for e in evs(plugin, "state")]
    assert states == [(None, "starting"), ("starting", "idle"), ("idle", "busy"), ("busy", "idle")]


def test_session_start_after_turn_does_not_reset(crew):
    plugin, ctx, _ = crew
    ctx.fire("pre_llm_call", turn_id="t")      # resumed session: no on_session_start
    ctx.fire("on_session_start", session_id="s", model="m")
    assert rec(plugin)["state"] == "busy"


def test_human_input_blocks_and_resolves(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    ctx.fire("pre_tool_call", tool_name="clarify", args={"questions": []})
    ctx.fire("on_human_input_request", kind="clarify", request_id="r1", session_id="s",
             session_key="k", platform="cli", prompt="Which color?")
    r = rec(plugin)
    assert r["state"] == "blocked" and r["blocked_kind"] == "clarify"
    ctx.fire("on_human_input_resolved", kind="clarify", request_id="r1", outcome="submitted")
    r = rec(plugin)
    assert r["state"] == "busy" and r["blocked_kind"] is None
    end_turn(ctx)
    assert rec(plugin)["state"] == "idle"


def test_interrupted_turn(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    ctx.fire("on_session_end", completed=False, interrupted=True, failed=False,
             turn_exit_reason="interrupted_by_user", turn_id=TURN.format("1c940174"))
    r = rec(plugin)
    assert r["state"] == "idle" and r["last_error"] is None
    [e] = evs(plugin, "turn.interrupted")
    assert e["reason"] == "interrupted_by_user"


def test_failed_turn_sets_last_error_and_event(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    ctx.fire("api_request_error", status_code=500, retryable=False, reason="server_error",
             error={"type": "APIError", "message": "upstream exploded\nbadly"}, turn_id="t")
    assert rec(plugin)["last_error"] == "HTTP 500 APIError: upstream exploded badly"
    assert rec(plugin)["state"] == "busy"
    end_turn(ctx, completed=False, failed=True, turn_exit_reason="api_error")
    r = rec(plugin)
    assert r["state"] == "idle" and r["last_error"] == "HTTP 500 APIError: upstream exploded badly"
    [e] = evs(plugin, "error")
    assert e["reason"] == "api_error"
    turn(ctx, "next")
    assert rec(plugin)["last_error"] is None


def test_failed_turn_without_api_error(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx, completed=False, failed=True, turn_exit_reason="max_iterations")
    assert rec(plugin)["last_error"] == "max_iterations"


def test_usage_tokens_accumulate(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    # real Hermes: CanonicalUsage asdict + prompt_tokens (= input + cache) + total_tokens
    ctx.fire("post_api_request", usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 900,
                                        "cache_write_tokens": 0, "reasoning_tokens": 0, "request_count": 1,
                                        "prompt_tokens": 1000, "total_tokens": 1020}, model="m")
    ctx.fire("post_api_request", usage={"input_tokens": 5, "output_tokens": 7})
    ctx.fire("post_api_request", usage={"prompt_tokens": 3, "completion_tokens": 4})
    ctx.fire("post_api_request", usage=None)
    ctx.fire("post_api_request", usage={"input_tokens": "garbage"})
    r = rec(plugin)
    assert r["tokens_in"] == 1008 and r["tokens_out"] == 31
    assert len(evs(plugin, "usage")) == 3


def test_finalize_marks_exited(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    ctx.fire("on_session_finalize", session_id="s", platform="cli", reason="shutdown")
    assert rec(plugin)["state"] == "exited"
    assert len(evs(plugin, "agent.exit")) == 1
    assert plugin.stop.is_set()
    hb = rec(plugin)["heartbeat"]
    plugin.heartbeat_tick()
    assert rec(plugin)["heartbeat"] == hb
    ctx.fire("on_session_end", completed=False, interrupted=True)   # CLI exit-handler shape
    assert rec(plugin)["state"] == "exited"


def test_new_session_boundary_is_not_an_exit(crew):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    ctx.fire("on_session_finalize", session_id="s", platform="cli", reason="session_boundary")
    assert rec(plugin)["state"] == "idle"
    ctx.fire("on_session_reset", session_id="s2", platform="cli", reason="new_session")
    assert rec(plugin)["state"] == "idle"


def test_reset_after_exit_revives(crew):
    plugin, ctx, _ = crew
    ctx.fire("on_session_finalize", session_id="s", platform="cli")
    ctx.fire("on_session_reset", session_id="s2", platform="cli")
    assert rec(plugin)["state"] == "idle"


def test_heartbeat_tick(crew):
    plugin, _, _ = crew
    p = plugin.team.dir / "agents" / "Coder.json"
    data = json.loads(p.read_text())
    data["heartbeat"] = 1.0
    data["pid"] = 1
    p.write_text(json.dumps(data))
    plugin.heartbeat_tick()
    r = rec(plugin)
    assert r["heartbeat"] > 1.0 and r["pid"] == os.getpid()


def test_hooks_never_raise(crew, P, monkeypatch):
    plugin, ctx, _ = crew

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(P.state, "update", boom)
    monkeypatch.setattr(P.state, "read", boom)
    monkeypatch.setattr(P.state, "add_tokens", boom)
    monkeypatch.setattr(P.events, "emit", boom)
    for name in P.CrewPlugin.HOOKS:
        assert ctx.fire(name, tool_name="write_file", args={"path": "x"}, usage={"input_tokens": 1},
                        kind="clarify", failed=True, interrupted=True, result="{}") is None, name
    assert plugin.mail_tick() == 0
    plugin.heartbeat_tick()


# ---------------------------------------------------------------- delivery
def test_no_delivery_while_starting_until_first_turn(crew, P):
    plugin, ctx, clock = crew
    send(P, plugin, "lead", "early")
    assert plugin.mail_tick() == 0                     # starting
    ctx.fire("on_session_start", session_id="s", model="m")
    clock.t += 1
    assert plugin.mail_tick() == 0                     # idle, but the brief turn has not begun
    ctx.fire("pre_llm_call", turn_id="brief")
    assert plugin.mail_tick() == 0                     # busy
    end_turn(ctx, "brief")
    assert plugin.mail_tick() == 1
    assert ctx.injected and "early" in ctx.injected[0]


def test_startup_grace_30s_without_any_turn(crew, P):
    plugin, ctx, clock = crew
    send(P, plugin, "lead", "hello")
    clock.t += 29
    assert plugin.mail_tick() == 0
    assert rec(plugin)["state"] == "starting"
    clock.t += 2
    assert plugin.mail_tick() == 1                     # resumed session, no turn: idle after grace
    assert rec(plugin)["state"] == "idle"


def test_startup_grace_after_session_start_without_turn(crew, P):
    plugin, ctx, clock = crew
    ctx.fire("on_session_start", session_id="s", model="m")
    send(P, plugin, "lead", "hello")
    assert plugin.mail_tick() == 0
    clock.t += 31
    assert plugin.mail_tick() == 1


@pytest.mark.parametrize("phase", ["busy", "blocked", "exited"])
def test_never_delivers_unless_idle(crew, P, phase):
    plugin, ctx, clock = crew
    first_turn(ctx)
    if phase == "blocked":
        ctx.fire("on_human_input_request", kind="approval")
    if phase == "exited":
        end_turn(ctx)
        ctx.fire("on_session_finalize", session_id="s", platform="cli")
    send(P, plugin, "Tester", "msg")
    for _ in range(5):
        clock.t += 60
        assert plugin.mail_tick() == 0
    assert ctx.injected == []
    assert len(P.mailbox.pending(plugin.team, "Coder")) == 1


def test_single_message_format(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    m = send(P, plugin, "Tester", "please run 'make test' && report $(date)")
    assert plugin.mail_tick() == 1
    [text] = ctx.injected
    short = m["id"].rsplit("-", 1)[-1]
    assert text.startswith(f"[from Tester sent {m['sent_at'][11:19]} · #{short}] "
                           "please run 'make test' && report $(date)")
    assert text.endswith('(reply: hermes-crew send Tester "...")')
    [e] = evs(plugin, "msg.delivered")
    assert e["id"] == m["id"] and e["role"] == "Coder"
    assert P.mailbox.pending(plugin.team, "Coder") == []


def test_batches_all_pending_into_one_injection(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    ids = [send(P, plugin, s, f"body {i}")["id"] for i, s in enumerate(["Tester", "lead", "Tester"])]
    assert plugin.mail_tick() == 0                     # still busy
    end_turn(ctx)
    assert plugin.mail_tick() == 3
    [text] = ctx.injected
    assert text.startswith("You have 3 new team messages:\n\n")
    assert text.index("body 0") < text.index("body 1") < text.index("body 2")
    assert "(reply: hermes-crew send" in text.splitlines()[-1]
    assert [e["id"] for e in evs(plugin, "msg.delivered")] == ids


def test_multiline_body_preserved(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    send(P, plugin, "lead", "line one\nline two\n\n  indented")
    plugin.mail_tick()
    assert "line one\nline two\n\n  indented" in ctx.injected[0]


def test_unclaim_when_inject_refused_then_retry(crew, P):
    plugin, ctx, clock = crew
    first_turn(ctx)
    end_turn(ctx)
    m = send(P, plugin, "Tester", "retry me")
    ctx.accept = False
    assert plugin.mail_tick() == 0
    assert [x["id"] for x in P.mailbox.pending(plugin.team, "Coder")] == [m["id"]]
    assert evs(plugin, "msg.delivered") == []
    ctx.accept = True
    clock.t += 1
    assert plugin.mail_tick() == 1
    assert len(ctx.injected) == 1 and "retry me" in ctx.injected[0]
    clock.t += 1
    assert plugin.mail_tick() == 0                     # never twice


def test_unclaim_when_inject_raises(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    send(P, plugin, "Tester", "x")
    ctx.raise_on_inject = True
    assert plugin.mail_tick() == 0
    assert len(P.mailbox.pending(plugin.team, "Coder")) == 1


def test_no_second_inject_before_injected_turn_starts(crew, P):
    plugin, ctx, clock = crew
    first_turn(ctx)
    end_turn(ctx)
    send(P, plugin, "Tester", "one")
    assert plugin.mail_tick() == 1
    send(P, plugin, "Tester", "two")
    clock.t += 1
    assert plugin.mail_tick() == 0                     # injected turn not started yet
    ctx.fire("pre_llm_call", turn_id="injected")
    end_turn(ctx, "injected")
    clock.t += 1
    assert plugin.mail_tick() == 1
    assert "two" in ctx.injected[1]


def test_inject_settle_timeout(crew, P):
    plugin, ctx, clock = crew
    first_turn(ctx)
    end_turn(ctx)
    send(P, plugin, "Tester", "one")
    plugin.mail_tick()
    send(P, plugin, "Tester", "two")
    clock.t += 31                                      # injected turn never showed up
    assert plugin.mail_tick() == 1


def test_delivery_race_two_ticks_claim_once(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    for i in range(20):
        send(P, plugin, "Tester", f"m{i}")
    results = []
    threads = [threading.Thread(target=lambda: results.append(plugin.mail_tick())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sum(results) == 20 and len(ctx.injected) == 1


def test_role_mailbox_case_insensitive(crew, P):
    plugin, ctx, _ = crew
    first_turn(ctx)
    end_turn(ctx)
    P.mailbox.send(plugin.team, "lead", "coder", "lower-case address")
    assert plugin.mail_tick() == 1


# ---------------------------------------------------------------- threads
def test_threads_run_ticks_and_stop(P, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "lead")
    monkeypatch.setattr(P, "HEARTBEAT_S", 0.01)
    monkeypatch.setattr(P, "MAIL_S", 0.01)
    monkeypatch.setattr(P, "WATCHDOG_S", 0.01)
    monkeypatch.setattr(P.atexit, "register", lambda *a, **k: None)
    hits = {"hb": threading.Event(), "wd": threading.Event()}
    monkeypatch.setattr(P.CrewPlugin, "heartbeat_tick", lambda self: hits["hb"].set())
    monkeypatch.setattr(P.CrewPlugin, "watchdog_tick", lambda self: hits["wd"].set())
    plugin = P.register(FakeCtx(), start_threads=True)
    try:
        names = sorted(t.name for t in plugin.threads)
        assert names == ["hermes-crew-heartbeat", "hermes-crew-mail", "hermes-crew-watchdog"]
        assert all(t.daemon for t in plugin.threads)
        assert hits["hb"].wait(5) and hits["wd"].wait(5)
    finally:
        plugin.stop.set()
        for t in plugin.threads:
            t.join(5)
    assert not any(t.is_alive() for t in plugin.threads)


def test_teammate_has_no_watchdog_thread(P, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Coder")
    monkeypatch.setattr(P.atexit, "register", lambda *a, **k: None)
    plugin = P.register(FakeCtx(), start_threads=True)
    try:
        assert sorted(t.name for t in plugin.threads) == ["hermes-crew-heartbeat", "hermes-crew-mail"]
    finally:
        plugin.stop.set()
