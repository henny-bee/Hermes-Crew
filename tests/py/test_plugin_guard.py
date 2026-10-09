"""Plugin adapter: pre_tool_call ownership guard (with the real board) and the lead watchdog tick."""
from __future__ import annotations

import json
import os
import time

import pytest

from plugin_fake import FakeCtx, load_plugin


@pytest.fixture
def P():
    return load_plugin()


def make(P, monkeypatch, root, role):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(root))
    monkeypatch.setenv("HERMES_TEAM_ROLE", role)
    monkeypatch.chdir(root)
    ctx = FakeCtx()
    return P.register(ctx, start_threads=False), ctx


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print(1)\n")
    return root


def board_lines(plugin):
    p = plugin.team.dir / "board.log"
    return [line.split("\t") for line in p.read_text().splitlines()] if p.exists() else []


def evs(plugin, kind):
    p = plugin.team.dir / "events.jsonl"
    return [e for e in map(json.loads, p.read_text().splitlines()) if e["kind"] == kind]


def call(ctx, tool, **args):
    return ctx.fire("pre_tool_call", tool_name=tool, args=args, task_id="", session_id="s",
                    tool_call_id="c1", turn_id="t1")


# ---------------------------------------------------------------- guard
def test_write_to_file_owned_by_other_is_blocked(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", "src/app.py")
    res = call(ctx, "write_file", path="src/app.py", content="hi")
    assert res == {"action": "block", "message":
                   'hermes-crew: src/app.py is owned by Planner. Send the owner the exact change: '
                   'hermes-crew send Planner "..." (or ask lead to transfer it).'}
    assert evs(plugin, "board.blocked")[0]["tool"] == "write_file"


def test_auto_own_unowned_file(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    assert call(ctx, "write_file", path="src/new.py", content="x") is None
    [line] = board_lines(plugin)
    assert line[1:] == ["Coder", "OWN", "src/new.py"]
    assert evs(plugin, "board.own")[0]["file"] == "src/new.py"
    # second write: already mine, no duplicate OWN line
    assert call(ctx, "patch", path="src/new.py", old_string="x", new_string="y") is None
    assert len(board_lines(plugin)) == 1


def test_auto_own_disabled_by_env(P, monkeypatch, proj):
    monkeypatch.setenv("HERMES_CREW_AUTO_OWN", "0")
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    assert call(ctx, "write_file", path="src/new.py", content="x") is None
    assert board_lines(plugin) == []


def test_team_dir_and_outside_paths_not_owned(P, monkeypatch, proj, tmp_path):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    assert call(ctx, "write_file", path=".team/handoff/T1.md", content="x") is None
    assert call(ctx, "write_file", path=str(tmp_path / "elsewhere.txt"), content="x") is None
    assert board_lines(plugin) == []


def test_owner_cache_sees_new_board_lines(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    assert call(ctx, "terminal", command="echo x > src/app.py") is None
    P.board.own(plugin.team, "Tester", "src/app.py")
    assert call(ctx, "terminal", command="echo x > src/app.py")["action"] == "block"
    P.board.transfer(plugin.team, "lead", "src/app.py", "Coder")
    assert call(ctx, "terminal", command="echo x > src/app.py") is None


def test_claim_race_lost_blocks(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    real_own = P.board.own

    def racing_own(team, role, *paths):
        real_own(team, "Tester", *paths)          # Tester wins between check and claim
        return real_own(team, role, *paths)
    monkeypatch.setattr(P.board, "own", racing_own)
    res = call(ctx, "write_file", path="src/new.py", content="x")
    assert res["action"] == "block" and "owned by Tester" in res["message"]


def test_terminal_cwd_learned_from_results(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", "src/app.py")
    assert call(ctx, "terminal", command="rm app.py") is None          # cwd = proj
    ctx.fire("post_tool_call", tool_name="terminal", args={"command": "cd src"},
             result=json.dumps({"output": "", "exit_code": 0, "cwd": str(proj / "src")}))
    assert plugin.cwd == str(proj / "src")
    assert call(ctx, "terminal", command="rm app.py")["action"] == "block"
    assert call(ctx, "write_file", path="app.py", content="x")["action"] == "block"
    ctx.fire("post_tool_call", tool_name="terminal", result="not json")
    ctx.fire("post_tool_call", tool_name="read_file", result=json.dumps({"cwd": "/"}))
    assert plugin.cwd == str(proj / "src")


def test_guard_errors_allow(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", "src/app.py")
    monkeypatch.setattr(P, "_board_owners", lambda team: 1 / 0)
    assert call(ctx, "write_file", path="src/app.py", content="x") is None


def test_non_file_tools_skip_board(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    monkeypatch.setattr(P, "_board_owners", lambda team: pytest.fail("board read"))
    assert call(ctx, "read_file", path="src/app.py") is None
    assert call(ctx, "web_search", query="x") is None


def test_guard_latency_with_real_board(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", *[f"src/m{i}.py" for i in range(300)])
    t0 = time.perf_counter()
    for i in range(50):
        call(ctx, "terminal", command=f"sed -i s/a/b/ src/m{i}.py && echo ok > out{i}.log")
    assert (time.perf_counter() - t0) / 50 < 0.05


# ---------------------------------------------------------------- watchdog tick (lead)
def write_agent(team, role, **fields):
    rec = {"role": role, "state": "idle", "since": time.time(), "pid": os.getpid(),
           "heartbeat": time.time(), "started": time.time() - 100, "turn_id": None,
           "last_error": None, "blocked_kind": None}
    rec.update(fields)
    (team.dir / "agents").mkdir(parents=True, exist_ok=True)
    (team.dir / "agents" / f"{role}.json").write_text(json.dumps(rec))


def test_watchdog_dead_agent_orphans_and_notifies_lead(P, monkeypatch, proj):
    calls = []
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    plugin.tmux_runner = calls.append
    monkeypatch.setenv("TMUX", "/tmp/fake,1,0")
    monkeypatch.setenv("TMUX_PANE", "%7")
    team = plugin.team
    t1 = P.tasks.add(team, "lead", "build api", owner="Coder")
    P.tasks.add(team, "lead", "write docs")
    write_agent(team, "Coder", state="busy", pid=2 ** 22 + 4321, heartbeat=time.time() - 300)
    new = plugin.watchdog_tick()
    assert [e.kind for e in new] == ["dead"]
    tasks = {t["id"]: t for t in P.tasks.list_tasks(team)}
    assert tasks[t1["id"]]["status"] == "orphaned"
    assert tasks["T2"]["status"] == "open"
    [msg] = P.mailbox.pending(team, "lead")
    assert msg["from"] == "hermes-crew" and "Coder is gone" in msg["body"]
    assert evs(plugin, "escalation")[0]["esc"] == "dead"
    assert calls and calls[0][:5] == ["display-message", "-d", "8000", "-t", "%7"]
    assert calls[0][-1].startswith("hermes-crew: Coder is gone")
    # dedup: nothing new on the next tick
    assert plugin.watchdog_tick() == []
    assert len(P.mailbox.pending(team, "lead")) == 1


def test_watchdog_no_tmux_outside_tmux(P, monkeypatch, proj):
    calls = []
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    plugin.tmux_runner = calls.append
    write_agent(plugin.team, "Coder", state="blocked", blocked_kind="approval", since=time.time() - 200)
    [e] = plugin.watchdog_tick()
    assert e.kind == "blocked" and calls == []
    assert "Coder waits for your input in its pane: approval" in P.mailbox.pending(plugin.team, "lead")[0]["body"]


def test_watchdog_expires_messages(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    team = plugin.team
    write_agent(team, "Tester", state="busy")
    P.mailbox.send(team, "Coder", "Tester", "please review", ttl_s=1)
    P.mailbox.send(team, "lead", "Tester", "lead's own", ttl_s=1)
    new = plugin.watchdog_tick(now=time.time() + 10)
    assert [e.kind for e in new] == ["expired"]
    assert "from Coder to Tester expired" in new[0].text
    bodies = [m["body"] for m in P.mailbox.pending(team, "lead")]
    assert any("expired" in b and "lead's own" in b for b in bodies)      # mailbox told the sender
    assert any("from Coder to Tester expired" in b for b in bodies)        # escalation


def test_watchdog_stall_once_per_episode(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    team = plugin.team
    P.tasks.add(team, "lead", "build api", owner="Coder")
    long_ago = time.time() - 500
    write_agent(team, "lead", since=long_ago)
    write_agent(team, "Coder", since=long_ago)
    [e] = plugin.watchdog_tick()
    assert e.kind == "stall" and "T1 'build api' claimed — owner Coder" in e.text
    assert plugin.watchdog_tick() == []
    write_agent(team, "Coder", since=time.time() - 200)        # Coder worked, idle again → new episode
    assert [x.kind for x in plugin.watchdog_tick()] == ["stall"]


def test_watchdog_stall_on_unapproved_files(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    team = plugin.team
    P.board.own(team, "Coder", "src/app.py")
    write_agent(team, "lead", since=time.time() - 500)
    write_agent(team, "Coder", since=time.time() - 500)
    [e] = plugin.watchdog_tick()
    assert "src/app.py (owner Coder) has no current approval" in e.text
    P.board.approve(team, "Tester", "src/app.py", P.board.sha12(proj / "src/app.py"), "ran the tests, all 12 pass")
    write_agent(team, "Coder", since=time.time() - 400)
    assert plugin.watchdog_tick() == []


def test_watchdog_tick_never_raises(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "lead")
    monkeypatch.setattr(P.mailbox, "expire", lambda *a, **k: 1 / 0)
    assert plugin.watchdog_tick() == []
