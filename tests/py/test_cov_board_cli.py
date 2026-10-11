"""Coverage for board.py (CLI front-end, pane_idle, status text) and cli.py edge paths."""

from __future__ import annotations

import json
import os
import time

import pytest
from hermes_crew import board, cli, mailbox, state
from hermes_crew import team as team_mod


# ---------------------------------------------------------------- board.main
@pytest.fixture
def bmain(team, capsys, monkeypatch):
    monkeypatch.setattr(team_mod, "my_pane", lambda: None)
    monkeypatch.chdir(team.root)

    def run(role, *argv, **kw):
        rc = board.main(list(argv), team=team, role=role, **kw)
        out = capsys.readouterr()
        return rc, out.out, out.err

    return run


def test_board_usage_errors(bmain):
    for argv in (
        [],
        ["bogus"],
        ["decide"],
        ["decide", "--replaces"],
        ["own"],
        ["transfer", "a"],
        ["transfer", "", "Dev"],
        ["transfer", "a", "bad name"],
        ["release"],
        ["release", ""],
    ):
        rc, _, err = bmain("Dev", *argv)
        assert rc == 2 and "usage:" in err, argv


def test_board_decide_and_replace(bmain):
    rc, out, _ = bmain("lead", "decide", "use", "tabs")
    assert rc == 0 and "decision #1 recorded" in out
    rc, out, _ = bmain("lead", "decide", "--replaces", "#1", "spaces")
    assert rc == 0 and "(replaces #1)" in out
    rc, _, err = bmain("lead", "decide", "--replaces", "1", "again")
    assert rc == 1 and "already replaced" in err
    rc, _, err = bmain("lead", "decide", "--replaces", "x", "again")
    assert rc == 1 and "no such decision" in err
    rc, _, err = bmain("lead", "decide", "--replaces", "9", "again")
    assert rc == 1 and "no such decision" in err


def test_board_own_transfer_release_flow(bmain, team):
    (team.root / "a.py").write_text("x")
    rc, out, _ = bmain("Dev", "own", "a.py")
    assert rc == 0 and "Dev owns a.py" in out
    rc, _, err = bmain("QA", "own", "a.py")
    assert rc == 1 and "owned by Dev" in err
    rc, _, err = bmain("QA", "transfer", "a.py", "QA")
    assert rc == 1 and "only Dev or lead" in err
    rc, _, err = bmain("QA", "release", "a.py")
    assert rc == 1 and "only Dev or lead" in err
    rc, out, _ = bmain("Dev", "transfer", "a.py", "QA")
    assert rc == 0 and "Dev -> QA" in out
    rc, _, err = bmain("Dev", "transfer", "none.py", "QA")
    assert rc == 1 and "has no owner" in err
    rc, out, _ = bmain("lead", "release", "a.py")
    assert rc == 0 and "released (was QA)" in out
    rc, out, _ = bmain("lead", "release", "a.py")
    assert rc == 0 and "has no owner" in out


def test_board_approve_rules(bmain, team):
    f = team.root / "a.py"
    f.write_text("x")
    bmain("Dev", "own", "a.py")
    rc, _, err = bmain("Dev", "approve", str(f), "I checked this thoroughly and it is fine")
    assert rc == 1 and "approval must come from someone else" in err
    rc, _, err = bmain("QA", "approve", str(team.root / "missing.py"), "x" * 30)
    assert rc == 2 and "usage:" in err
    rc, _, err = bmain("QA", "approve")
    assert rc == 2
    rc, _, err = bmain("QA", "approve", str(f), "short")
    assert rc == 1 and "at least a sentence" in err
    rc, out, _ = bmain("QA", "approve", str(f), "read the file; ran its tests; all green")
    assert rc == 0 and out.startswith("approved a.py @ ")
    rc, out, _ = bmain("QA", "status")
    assert "approved by QA" in out and "OK" in out
    f.write_text("changed")
    rc, out, _ = bmain("QA", "status")
    assert "STALE" in out and "no valid approval" in out


def test_board_status_empty_and_unowned(bmain, team):
    _, out, _ = bmain("Dev", "status")
    assert "(board is empty)" in out
    f = team.root / "b.py"
    f.write_text("x")
    bmain("Dev", "own", "b.py")
    bmain("Dev", "release", "b.py")
    _rc, out, _ = bmain("Dev", "status")
    assert "(none)" in out and "!! no owner" in out
    assert not (team.dir / "board.log").read_text().endswith("\n\n")


def test_board_ignored_approval_when_approver_becomes_owner(bmain, team):
    f = team.root / "c.py"
    f.write_text("x")
    bmain("Dev", "own", "c.py")
    bmain("QA", "approve", str(f), "reviewed content and verified behaviour")
    bmain("Dev", "transfer", "c.py", "QA")
    _, out, _ = bmain("QA", "status")
    assert "ignored (approver owns the file now)" in out


def test_board_main_resolves_team_and_role_from_env(team, monkeypatch, capsys):
    monkeypatch.setattr(team_mod, "my_pane", lambda: None)
    monkeypatch.setenv("HERMES_TEAM_DIR", str(team.root))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Dev")
    assert board.main(["decide", "env", "works"]) == 0
    assert "decision #1" in capsys.readouterr().out
    assert board.main(["status"]) == 0


def test_inside_and_rel(team):
    assert board.inside(team, team.root / "src" / "a.py")
    assert not board.inside(team, team.dir / "board.log")
    assert not board.inside(team, team.dir)
    assert not board.inside(team, team.root)
    assert not board.inside(team, team.root.parent / "other")
    assert board.clean("a\tb\nc") == "a b c"


# ---------------------------------------------------------------- pane_idle
def _tmux_script(monkeypatch, display, line):
    def fake(*args, **_k):
        return display if args[0] == "display" else line

    monkeypatch.setattr(team_mod, "tmux", fake)


def test_pane_idle_true_on_empty_prompt(monkeypatch):
    _tmux_script(monkeypatch, "0 2 0", "❯")  # "❯ " is 2 chars wide
    assert board.pane_idle("%1") is True


def test_pane_idle_with_prefix_and_custom_symbol(monkeypatch):
    _tmux_script(monkeypatch, "0 9 0", "hermes $")
    assert board.pane_idle("%1", symbol="$") is True
    monkeypatch.setenv("HERMES_TEAM_PROMPT_SYMBOL", "$")
    assert board.pane_idle("%1") is True


@pytest.mark.parametrize(
    "display,line",
    [
        (None, "❯"),  # tmux unavailable
        ("garbage", "❯"),  # unparsable cursor info
        ("0 2 1", "❯"),  # pane in copy mode
        ("0 5 0", "❯"),  # cursor not right after the prompt: text typed
        ("0 2 0", "no prompt here"),
        ("0 2 0", None),  # capture failed
        ("0 2 0", "❯ /steer"),
        ("0 2 0", "❯ [1] running"),
    ],
)
def test_pane_idle_false_cases(monkeypatch, display, line):
    _tmux_script(monkeypatch, display, line)
    assert board.pane_idle("%1") is False


def test_pane_idle_busy_markers(monkeypatch):
    _tmux_script(monkeypatch, "0 2 0", "❯ Processing command")
    assert board.pane_idle("%1") is False
    _tmux_script(monkeypatch, "0 8 0", "❯ Allow once")
    assert board.pane_idle("%1") is False


def test_status_text_lists_tmux_panes(team, monkeypatch):
    state.update(team, "Dev", state="busy", pid=os.getpid(), heartbeat=time.time())

    def fake(*args, **_k):
        if args[0] == "display" and "#{session_name}:#{window_index}" in args:
            return "s:1"
        if args[0] == "list-panes":
            return "%1 Dev opus\n%2 Ghost\n%3"
        return None

    monkeypatch.setattr(team_mod, "tmux", fake)
    out = board.status_text(team, pane="%1")
    assert "Dev" in out and "busy" in out and "model=opus" in out
    assert "Ghost" in out and "model=default" in out and "?" in out


# ---------------------------------------------------------------- cli
@pytest.fixture
def as_role(team, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(team.root))
    monkeypatch.chdir(team.root)
    monkeypatch.setattr(team_mod, "my_pane", lambda: None)

    def run(role, *argv):
        monkeypatch.setenv("HERMES_TEAM_ROLE", role)
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:
            rc = e.code
        out = capsys.readouterr()
        return rc, out.out, out.err

    return run


def test_send_all_and_error_paths(team, as_role):
    for r in ("Dev", "QA"):
        state.update(team, r, state="idle")
    rc, out, _ = as_role("Dev", "send", "all", "hello", "team")
    assert rc == 0 and "for QA" in out and "for lead" in out and "for Dev" not in out
    rc, out, _ = as_role("lead", "send", "all", "x")
    assert rc == 0 and "for Dev" in out and "for lead" not in out
    rc, _, err = as_role("lead", "send", "bad name!", "x")
    assert rc == 1 and "not a role name" in err
    rc, _, err = as_role("lead", "send", "user", "x")
    assert rc == 1 and "cannot receive" in err
    rc, _, err = as_role("lead", "send", "Dev", "   ")
    assert rc == 1 and "empty message" in err


def test_send_all_with_nobody_else(as_role):
    rc, _, err = as_role("lead", "send", "all", "x")
    assert rc == 1 and "no teammates" in err


def test_inbox_all_shows_delivered_history(team, as_role):
    state.update(team, "Dev", state="idle")
    mailbox.send(team, "lead", "Dev", "first\nline2")
    mailbox.claim_all(team, "Dev")
    _, out, _ = as_role("Dev", "inbox", "--all")
    assert "delivered to Dev" in out and "first" in out and "no pending" in out
    _rc, out, _ = as_role("QA", "inbox", "--all")
    assert "nothing delivered to QA yet" in out


def test_status_json_and_done_check(team, as_role):
    rc, out, _ = as_role("lead", "status", "--json")
    assert rc == 0 and isinstance(json.loads(out), dict)
    rc, out, _ = as_role("lead", "status", "--done-check", "--json")
    d = json.loads(out)
    assert rc == (0 if d["done"] else 1) and "missing" in d
    as_role("lead", "task", "add", "unfinished")
    rc, out, _ = as_role("lead", "status", "--done-check")
    assert rc == 1 and out.strip()
    rc, out, _ = as_role("lead", "status")
    assert rc == 0 and out.strip()


def test_log_variants(team, as_role):
    _, out, _ = as_role("lead", "log")
    assert "no events yet" in out
    as_role("lead", "task", "add", "t1")
    _, out, _ = as_role("lead", "log", "--kind", "nothing.such")
    assert "no events matching" in out
    _, out, _ = as_role("lead", "log", "--kind", "task", "--json")
    lines = [json.loads(x) for x in out.splitlines()]
    assert lines and all(e["kind"].startswith("task") for e in lines)
    _rc, out, _ = as_role("lead", "log", "--role", "LEAD", "-n", "1")
    assert len(out.strip().splitlines()) == 1


def test_watch_stops_on_ctrl_c(team, as_role, monkeypatch):
    calls = []

    def sleep(_s):
        calls.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    rc, out, _ = as_role("lead", "watch", "--interval", "0.5")
    assert rc == 0 and calls and "Ctrl+C" in out


def test_task_add_owner_rules_and_notifications(team, as_role):
    state.update(team, "Dev", state="idle")
    rc, out, _ = as_role("lead", "task", "add", "build", "it", "--owner", "dev", "--priority", "1", "--desc", "details")
    assert rc == 0 and "owner Dev" in out and "priority 1" in out
    notice = mailbox.pending(team, "Dev")
    assert len(notice) == 1 and "assigned you T1" in notice[0]["body"] and "task start T1" in notice[0]["body"]
    rc, out, _ = as_role("lead", "task", "add", "later", "--deps", "T1", "--owner", "Future")
    assert rc == 0 and "deps T1 - not ready yet" in out
    assert "waits for T1" in mailbox.pending(team, "Future")[0]["body"]
    rc, _, err = as_role("lead", "task", "add", "x", "--owner", "user")
    assert rc == 1 and "cannot own tasks" in err
    rc, _, err = as_role("lead", "task", "add", "x", "--owner", "bad name")
    assert rc == 1 and "cannot own tasks" in err
    rc, out, _ = as_role("lead", "task", "add", "mine", "--owner", "lead")
    assert rc == 0 and mailbox.pending(team, "lead") == []


def test_task_list_variants(team, as_role):
    _, out, _ = as_role("Dev", "task", "list")
    assert "no tasks" in out and "task add" in out
    _, out, _ = as_role("Dev", "task", "list", "--mine")
    assert "owned by Dev" in out
    as_role("lead", "task", "add", "a", "--owner", "Dev")
    as_role("lead", "task", "add", "b", "--deps", "T1", "--owner", "Dev")
    _, out, _ = as_role("Dev", "task", "list", "--mine")
    assert "T1" in out and "(waits T1)" in out
    _rc, out, _ = as_role("Dev", "task", "list", "--json")
    assert [t["id"] for t in json.loads(out)] == ["T1", "T2"]


def test_task_lifecycle_through_cli(team, as_role, tmp_path):
    as_role("lead", "task", "add", "first", "--owner", "Dev")
    as_role("lead", "task", "add", "second", "--deps", "T1", "--owner", "QA")
    rc, out, _ = as_role("Dev", "task", "claim", "T1")
    assert rc == 0 and "Dev owns T1" in out
    rc, out, _ = as_role("Dev", "task", "start", "T1")
    assert "in progress" in out
    rc, out, _ = as_role("Dev", "task", "block", "T1", "need", "input")
    assert "blocked: need input" in out and "lead was told" in out
    assert any("blocked T1" in m["body"] for m in mailbox.pending(team, "lead"))
    rc, out, _ = as_role("lead", "task", "block", "T1", "again")
    assert rc == 1 or "lead was told" not in out
    rc, out, _ = as_role("Dev", "task", "unblock", "T1")
    assert "unblocked" in out
    note = tmp_path / "note.md"
    note.write_text("handoff body\nline 2\n")
    rc, out, _ = as_role("Dev", "task", "done", "T1", "--handoff", str(note))
    assert rc == 0 and "T1 done (handoff:" in out and "T2 is now ready" in out
    assert "no verify record for T1" in out
    rc, out, _ = as_role("Dev", "verify", "T1", "--", "true")
    assert rc == 0
    rc, out, _ = as_role("lead", "task", "show", "T1")
    assert "handoff note" in out and "handoff body" in out and "verify" in out and "history:" in out
    rc, out, _ = as_role("lead", "task", "reopen", "T1", "found", "a", "bug")
    assert rc == 0 and "reopened" in out
    rc, out, _ = as_role("lead", "task", "assign", "T1", "qa")
    assert "assigned to QA" in out
    rc, _, err = as_role("lead", "task", "assign", "T1", "user")
    assert rc == 1 and "cannot own tasks" in err
    rc, _, err = as_role("lead", "task", "show", "T99")
    assert rc == 1 and "no task T99" in err
    rc, _, err = as_role("Dev", "task", "claim", "T99")
    assert rc == 1


def test_task_done_with_note_and_missing_handoff_file(team, as_role):
    as_role("lead", "task", "add", "x", "--owner", "Dev")
    as_role("Dev", "task", "claim", "T1")
    as_role("Dev", "task", "start", "T1")
    rc, out, _ = as_role("Dev", "task", "done", "T1", "--note", "short note")
    assert rc == 0 and "handoff:" in out
    rc, out, _ = as_role("lead", "task", "show", "T1")
    assert "short note" in out


def test_verify_usage_and_unknown_task(team, as_role):
    as_role("lead", "task", "add", "x")
    rc, _, err = as_role("lead", "verify", "T1", "--")
    assert rc == 2 and "usage:" in err
    rc, _, err = as_role("lead", "verify", "T9", "--", "true")
    assert rc == 1 and "no task T9" in err
    rc, out, err = as_role("lead", "verify", "T1", "echo", "hi")  # command without `--`
    assert rc == 0 and "hi" in out and "recorded" in err
    rc, out, err = as_role("lead", "verify", "--timeout", "5", "T1", "--", "bash", "-c", "exit 3")
    assert rc == 3
    rc, out, err = as_role("lead", "verify", "T1", "--timeout", "5", "--", "false")
    assert rc == 1


def test_main_without_command_prints_help(as_role):
    rc, _, err = as_role("lead")
    assert rc == 2 and "usage" in err.lower()
    rc, _out, _ = as_role("lead", "--version")
    assert rc == 0 or rc is None


def test_main_board_passthrough_and_doctor(team, as_role):
    rc, out, _ = as_role("lead", "board", "decide", "via", "cli")
    assert rc == 0 and "decision #1" in out
    rc, out, _ = as_role("lead", "doctor", "--json")
    assert rc in (0, 1) and isinstance(json.loads(out), (dict, list))


def test_main_survives_broken_pipe(as_role, monkeypatch):
    def boom(a):
        raise BrokenPipeError

    monkeypatch.setattr(cli, "cmd_inbox", boom)
    parser_fn = cli.build_parser
    monkeypatch.setattr(cli, "build_parser", lambda: _patched(parser_fn(), boom))
    rc, _, _ = as_role("lead", "inbox")
    assert rc == 1


def _patched(parser, fn):
    for action in parser._subparsers._group_actions:
        for sp in action.choices.values():
            if sp.get_default("fn") is not None and sp.prog.endswith("inbox"):
                sp.set_defaults(fn=fn)
    return parser


def test_deps_parsing_and_task_line():
    assert cli._deps("T1, T2 T3,,") == ["T1", "T2", "T3"] and cli._deps(None) == []
    line = cli._task_line(
        {
            "id": "T1",
            "status": "blocked",
            "owner": None,
            "priority": 2,
            "title": "x" * 80,
            "waiting_on": ["T0"],
            "reason": "why",
        }
    )
    assert "(waits T0)" in line and "! why" in line and "…" in line
    done = cli._task_line(
        {
            "id": "T1",
            "status": "done",
            "owner": "Dev",
            "priority": 2,
            "title": "t",
            "waiting_on": ["T0"],
            "reason": "old",
        }
    )
    assert "waits" not in done and "old" not in done
