from __future__ import annotations

import contextlib
import json
import subprocess
import sys

import pytest
from conftest import REPO
from hermes_crew import __version__, cli, mailbox, state, tasks


@pytest.fixture
def as_role(team, monkeypatch, capsys):
    """as_role(role, *argv) -> (rc, stdout, stderr), running `hermes-crew argv` in-process."""
    monkeypatch.setenv("HERMES_TEAM_DIR", str(team.root))
    monkeypatch.chdir(team.root)

    def run(role, *argv):
        monkeypatch.setenv("HERMES_TEAM_ROLE", role)
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:
            rc = e.code
        out = capsys.readouterr()
        return rc, out.out, out.err

    return run


def test_send_and_inbox(team, as_role):
    state.update(team, "Engineer", state="idle")
    rc, out, _ = as_role("lead", "send", "engineer", "build", "it")
    assert rc == 0 and out.startswith("queued #") and "for Engineer" in out
    rc, _, err = as_role("lead", "send", "Nobody", "x")
    assert rc == 1 and "REFUSED: no teammate 'Nobody'" in err and "Engineer" in err
    rc, _, err = as_role("Engineer", "send", "Engineer", "x")
    assert rc == 1 and "you are Engineer" in err
    rc, out, _ = as_role("Engineer", "inbox")
    assert "1 pending for Engineer" in out and "from lead:" in out and "build it" in out
    mailbox.claim_all(team, "Engineer")
    rc, out, _ = as_role("Engineer", "inbox", "--all")
    assert "delivered to Engineer" in out and "no pending messages" in out


def test_send_all(team, as_role):
    for r in ("Planner", "Engineer"):
        state.update(team, r, state="idle")
    rc, out, _ = as_role("Planner", "send", "all", "heads up")
    assert rc == 0
    assert sorted(line.split(" for ")[1].split()[0] for line in out.splitlines()) == ["Engineer", "lead"]


def test_task_flow(team, as_role):
    assert as_role("lead", "task", "add", "Write parser", "--priority", "1")[0] == 0
    rc, out, _ = as_role("lead", "task", "add", "Docs", "--deps", "T1", "--owner", "Planner")
    assert rc == 0 and "added T2 'Docs' (priority 3, deps T1 - not ready yet, owner Planner)" in out
    assert "you get a message when it is unblocked" in mailbox.pending(team, "Planner")[0]["body"]
    rc, _, err = as_role("Eng", "task", "claim", "T2")
    assert rc == 1 and err.strip() == "REFUSED: T2 is claimed by Planner - ask Planner or lead"
    assert as_role("Eng", "task", "claim", "T1")[0] == 0
    assert as_role("Eng", "task", "start", "T1")[0] == 0
    rc, out, _ = as_role("Eng", "task", "block", "T1", "need", "API", "key")
    assert rc == 0 and "lead was told" in out
    assert "Eng blocked T1" in mailbox.pending(team, "lead")[-1]["body"]
    assert as_role("Eng", "task", "unblock", "T1")[0] == 0
    rc, out, _ = as_role("Eng", "task", "done", "T1")
    assert rc == 0 and "T2 is now ready; told Planner" in out and "no verify record" in out
    rc, out, _ = as_role("Planner", "task", "list", "--mine")
    assert out.startswith("T2") and "T1" not in out
    rc, out, _ = as_role("lead", "task", "list", "--json")
    assert [t["id"] for t in json.loads(out)] == ["T1", "T2"]
    rc, out, _ = as_role("lead", "task", "show", "t1")
    assert "history:" in out and "claim" in out
    rc, out, _ = as_role("Rev", "task", "reopen", "T1", "edge", "case")
    assert rc == 0 and "reopened" in out
    assert as_role("lead", "task", "assign", "T1", "Rev")[0] == 0
    assert tasks.get(team, "T1")["owner"] == "Rev"


def test_usage_errors_exit_2(as_role):
    assert as_role("lead", "task")[0] == 2
    assert as_role("lead", "task", "frob")[0] == 2
    assert as_role("lead", "nonsense")[0] == 2
    assert as_role("lead")[0] == 2
    rc, _, _err = as_role("lead", "verify", "T1")
    assert rc == 2


def test_verify_cli(team, as_role):
    tasks.add(team, "lead", "x", owner="Eng")
    rc, _, err = as_role("Eng", "verify", "T9", "--", "true")
    assert rc == 1 and "no task T9" in err
    assert as_role("Eng", "verify", "T1", "--", sys.executable, "-c", "import sys; sys.exit(4)")[0] == 4
    assert as_role("Eng", "verify", "--timeout", "5", "T1", "--", "true")[0] == 0
    assert as_role("Eng", "verify", "T1", "--timeout", "5", "--", "true")[0] == 0
    assert as_role("Eng", "verify", "T1", sys.executable, "-c", "pass")[0] == 0  # no "--"
    recs = [json.loads(x) for x in team.path("verify.jsonl").read_text().splitlines()]
    assert [r["exit"] for r in recs] == [4, 0, 0, 0]


def test_status_and_done_check(team, as_role):
    rc, out, _ = as_role("lead", "status")
    assert rc == 0 and out.startswith("Hermes Crew")
    rc, out, _ = as_role("lead", "status", "--json")
    assert rc == 0 and set(json.loads(out)) >= {"agents", "tasks", "files", "decisions", "done_check"}
    rc, out, _ = as_role("lead", "status", "--done-check")
    assert rc == 1 and "NOT DONE" in out
    tasks.add(team, "lead", "x", owner="Eng")
    tasks.done(team, "Eng", "T1")
    rc, out, _ = as_role("lead", "status", "--done-check")
    assert rc == 0 and out.startswith("DONE")
    rc, out, _ = as_role("lead", "status", "--done-check", "--json")
    assert rc == 0 and json.loads(out) == {"done": True, "missing": []}


def test_log(team, as_role):
    tasks.add(team, "lead", "x")
    rc, out, _ = as_role("lead", "log", "--kind", "task.")
    assert rc == 0 and "task.add" in out
    rc, out, _ = as_role("lead", "log", "--role", "nobody")
    assert "no events matching" in out


def test_board_passthrough(team, as_role):
    (team.root / "f.txt").write_text("x")
    rc, out, _ = as_role("A", "board", "own", "f.txt")
    assert rc == 0 and out.strip() == "A owns f.txt"
    rc, _, err = as_role("B", "board", "own", "f.txt")
    assert rc == 1 and "owned by A" in err
    rc, _, err = as_role("B", "board", "frob")
    assert rc == 2 and "usage:" in err


def test_launcher_subprocess(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HERMES_TEAM_DIR": str(tmp_path / "p"),
        "HERMES_TEAM_ROLE": "lead",
        "TMUX_TMPDIR": str(tmp_path),
    }
    p = subprocess.run([str(REPO / "bin" / "hermes-crew"), "--version"], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout.strip() == f"hermes-crew {__version__}"
    p = subprocess.run(
        [str(REPO / "bin" / "hermes-crew"), "task", "add", "hello"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0 and "added T1" in p.stdout
    assert (tmp_path / "p" / ".team" / ".gitignore").exists()
    env["HERMES_CREW_LIB"] = str(tmp_path / "nowhere")
    p = subprocess.run([str(REPO / "bin" / "hermes-crew"), "--version"], capture_output=True, text=True, env=env)
    assert p.returncode == 0  # falls back to the checkout


def test_task_done_note_and_show(team, as_role):
    tasks.add(team, "lead", "x", owner="Eng")
    rc, _, err = as_role("Eng", "task", "done", "T1", "--note", "a", "--handoff", "f.md")
    assert rc == 2 and "not allowed with" in err
    rc, out, _ = as_role("Eng", "task", "done", "T1", "--note", "API is parse(text); see README")
    assert rc == 0 and "T1 done (handoff: .team/handoff/T1.md)" in out
    rc, out, _ = as_role("lead", "task", "show", "T1")
    assert "  handoff note (.team/handoff/T1.md):\n    API is parse(text); see README" in out
    assert sorted(p.name for p in team.root.iterdir()) == [".team"]


READ_ONLY = [
    ["doctor"],
    ["doctor", "--json"],
    ["status"],
    ["status", "--json"],
    ["status", "--done-check"],
    ["log"],
    ["inbox"],
    ["inbox", "--all"],
    ["task", "list"],
    ["task", "list", "--mine"],
    ["task", "show", "T1"],
    ["board", "status"],
]


@pytest.mark.parametrize("argv", READ_ONLY, ids=lambda a: " ".join(a))
@pytest.mark.parametrize("via_env", [False, True], ids=["cwd", "HERMES_TEAM_DIR"])
def test_read_only_commands_never_create_team(tmp_path, monkeypatch, capsys, argv, via_env):
    proj = tmp_path / "notateam"
    proj.mkdir()
    monkeypatch.chdir(proj)
    if via_env:
        monkeypatch.setenv("HERMES_TEAM_DIR", str(proj))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "lead")
    with contextlib.suppress(SystemExit):
        cli.main(argv)
    out = capsys.readouterr().out
    assert list(proj.iterdir()) == [], f"{argv} wrote into a non-team dir"
    if argv == ["doctor"]:
        assert "not inside a team" in out


def test_doctor_subprocess_outside_team(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMUX_TMPDIR": str(tmp_path)}
    proj = tmp_path / "repo"
    proj.mkdir()
    p = subprocess.run([str(REPO / "bin" / "hermes-crew"), "doctor"], cwd=proj, capture_output=True, text=True, env=env)
    assert "not inside a team" in p.stdout and not (proj / ".team").exists()


# ---------------------------------------------------------------- lock
def test_lock_runs_command_and_returns_its_exit_code(team, as_role):
    assert as_role("Eng", "lock", "deliver", "--", sys.executable, "-c", "import sys; sys.exit(3)")[0] == 3
    assert team.path("locks", "deliver.lock").exists()
    assert as_role("Eng", "lock", "deliver", sys.executable, "-c", "pass")[0] == 0  # no "--"
    assert as_role("Eng", "lock", "deliver", "--", "true")[0] == 0


def test_lock_path_name_and_errors(team, as_role, tmp_path):
    lock = tmp_path / "elsewhere" / "x.lock"
    assert as_role("Eng", "lock", str(lock), "--", "true")[0] == 0 and lock.exists()
    rc, _, err = as_role("Eng", "lock", "deliver")
    assert rc == 2 and "usage: hermes-crew lock" in err
    rc, _, err = as_role("Eng", "lock", "..", "--", "true")
    assert rc == 1 and "bad lock name" in err
    rc, _, err = as_role("Eng", "lock", "deliver", "--", str(tmp_path / "no-such-cmd"))
    assert rc == 127 and "cannot run" in err


def test_lock_signal_exit_code(team, as_role):
    kill_self = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    assert as_role("Eng", "lock", "deliver", "--", sys.executable, "-c", kill_self)[0] == 128 + 15


def test_lock_is_exclusive_across_processes(tmp_path):
    lock, out = tmp_path / "l.lock", tmp_path / "out"
    body = f"import time; f = open({str(out)!r}, 'a'); f.write('S'); f.flush(); time.sleep(0.4); f.write('E')"
    argv = [str(REPO / "bin" / "hermes-crew"), "lock", str(lock), "--", sys.executable, "-c", body]
    ps = [subprocess.Popen(argv) for _ in range(3)]
    assert [p.wait(timeout=30) for p in ps] == [0, 0, 0]
    assert out.read_text() == "SE" * 3  # never two holders at once
