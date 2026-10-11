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
    return ctx.fire(
        "pre_tool_call", tool_name=tool, args=args, task_id="", session_id="s", tool_call_id="c1", turn_id="t1"
    )


# ---------------------------------------------------------------- guard
def test_write_to_file_owned_by_other_is_blocked(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", "src/app.py")
    res = call(ctx, "write_file", path="src/app.py", content="hi")
    assert res == {
        "action": "block",
        "message": "hermes-crew: src/app.py is owned by Planner. Send the owner the exact change: "
        'hermes-crew send Planner "..." (or ask lead to transfer it).',
    }
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
    _plugin, ctx = make(P, monkeypatch, proj, "Coder")
    real_own = P.board.own

    def racing_own(team, role, *paths):
        real_own(team, "Tester", *paths)  # Tester wins between check and claim
        return real_own(team, role, *paths)

    monkeypatch.setattr(P.board, "own", racing_own)
    res = call(ctx, "write_file", path="src/new.py", content="x")
    assert res["action"] == "block" and "owned by Tester" in res["message"]


def test_terminal_cwd_learned_from_results(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Planner", "src/app.py")
    assert call(ctx, "terminal", command="rm app.py") is None  # cwd = proj
    ctx.fire(
        "post_tool_call",
        tool_name="terminal",
        args={"command": "cd src"},
        result=json.dumps({"output": "", "exit_code": 0, "cwd": str(proj / "src")}),
    )
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
    _plugin, ctx = make(P, monkeypatch, proj, "Coder")
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
    rec = {
        "role": role,
        "state": "idle",
        "since": time.time(),
        "pid": os.getpid(),
        "heartbeat": time.time(),
        "started": time.time() - 100,
        "turn_id": None,
        "last_error": None,
        "blocked_kind": None,
    }
    rec.update(fields)
    (team.dir / "agents").mkdir(parents=True, exist_ok=True)
    (team.dir / "agents" / f"{role}.json").write_text(json.dumps(rec))


def test_watchdog_dead_agent_orphans_and_notifies_lead(P, monkeypatch, proj):
    calls = []
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
    plugin.tmux_runner = calls.append
    monkeypatch.setenv("TMUX", "/tmp/fake,1,0")
    monkeypatch.setenv("TMUX_PANE", "%7")
    team = plugin.team
    t1 = P.tasks.add(team, "lead", "build api", owner="Coder")
    P.tasks.add(team, "lead", "write docs")
    write_agent(team, "Coder", state="busy", pid=2**22 + 4321, heartbeat=time.time() - 300)
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
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
    plugin.tmux_runner = calls.append
    write_agent(plugin.team, "Coder", state="blocked", blocked_kind="approval", since=time.time() - 200)
    [e] = plugin.watchdog_tick()
    assert e.kind == "blocked" and calls == []
    assert "Coder waits for your input in its pane: approval" in P.mailbox.pending(plugin.team, "lead")[0]["body"]


def test_watchdog_expires_messages(P, monkeypatch, proj):
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
    team = plugin.team
    write_agent(team, "Tester", state="busy")
    P.mailbox.send(team, "Coder", "Tester", "please review", ttl_s=1)
    P.mailbox.send(team, "lead", "Tester", "lead's own", ttl_s=1)
    new = plugin.watchdog_tick(now=time.time() + 10)
    assert [e.kind for e in new] == ["expired"]
    assert "from Coder to Tester expired" in new[0].text
    bodies = [m["body"] for m in P.mailbox.pending(team, "lead")]
    assert any("expired" in b and "lead's own" in b for b in bodies)  # mailbox told the sender
    assert any("from Coder to Tester expired" in b for b in bodies)  # escalation


def test_watchdog_stall_once_per_episode(P, monkeypatch, proj):
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
    team = plugin.team
    P.tasks.add(team, "lead", "build api", owner="Coder")
    long_ago = time.time() - 500
    write_agent(team, "lead", since=long_ago)
    write_agent(team, "Coder", since=long_ago)
    [e] = plugin.watchdog_tick()
    assert e.kind == "stall" and "T1 'build api' claimed — owner Coder" in e.text
    assert plugin.watchdog_tick() == []
    write_agent(team, "Coder", since=time.time() - 200)  # Coder worked, idle again → new episode
    assert [x.kind for x in plugin.watchdog_tick()] == ["stall"]


def test_watchdog_stall_on_unapproved_files(P, monkeypatch, proj):
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
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
    plugin, _ctx = make(P, monkeypatch, proj, "lead")
    monkeypatch.setattr(P.mailbox, "expire", lambda *a, **k: 1 / 0)
    assert plugin.watchdog_tick() == []


# ---------------------------------------------------------------- allow-by-uncertainty (board.unparsed)
UNPARSED = [
    "x=$(rm src/app.py)",
    "python -c \"open('src/app.py','w')\"",
    "echo $X > $TARGET",
    "cat `ls` > $OUT",
    'tee "$f"',
    'cp src/app.py "$DST"',
    "mv $A $B",
]
CLASSIFIED_OR_HARMLESS = [
    "ls -la",
    "grep '>' src/app.py",
    "make 2>&1 | tee build.log",
    "echo hi > /tmp/out.txt",
    "sed -n 1p f",
    "echo $HOME | tee /tmp/log",  # the dynamic word belongs to echo, not to tee
]


@pytest.mark.parametrize("cmd", UNPARSED)
def test_unparsed_writeish_command_is_logged(P, monkeypatch, proj, cmd):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Writer", "README.md")  # with or without owners
    assert call(ctx, "terminal", command=cmd) is None  # still allowed
    [e] = evs(plugin, "board.unparsed")
    assert e["command"] == cmd and e["role"] == "Coder" and e["suppressed"] == 0


@pytest.mark.parametrize("cmd", CLASSIFIED_OR_HARMLESS)
def test_unparsed_not_logged_for_classified_or_harmless(P, monkeypatch, proj, cmd):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    assert call(ctx, "terminal", command=cmd) is None
    assert evs(plugin, "board.unparsed") == []


def test_unparsed_not_logged_when_blocked_and_clipped(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    P.board.own(plugin.team, "Backend", "src/app.py")
    assert call(ctx, "terminal", command="echo x > src/app.py; y=$(date)")["action"] == "block"
    assert evs(plugin, "board.unparsed") == []
    call(ctx, "terminal", command="x=$(true) " + "a" * 1000)
    assert len(evs(plugin, "board.unparsed")[0]["command"]) == P.UNPARSED_CLIP


def test_unparsed_rate_limited(P, monkeypatch, proj):
    plugin, ctx = make(P, monkeypatch, proj, "Coder")
    now = [1000.0]
    plugin.clock = lambda: now[0]
    for _ in range(3):
        call(ctx, "terminal", command="x=$(rm a)")
    assert len(evs(plugin, "board.unparsed")) == 1
    now[0] += P.UNPARSED_EVERY_S
    call(ctx, "terminal", command="python3 -c 'pass'")
    es = evs(plugin, "board.unparsed")
    assert len(es) == 2 and es[1]["suppressed"] == 2 and es[1]["command"] == "python3 -c 'pass'"


def test_unparsed_shown_by_log_kind(P, monkeypatch, proj, capsys):
    from hermes_crew import cli

    _plugin, ctx = make(P, monkeypatch, proj, "Coder")
    call(ctx, "terminal", command="x=$(rm src/app.py)")
    capsys.readouterr()
    assert cli.main(["log", "--kind", "board.unparsed"]) == 0
    out = capsys.readouterr().out
    assert "board.unparsed" in out and "Coder" in out and "x=$(rm" in out
