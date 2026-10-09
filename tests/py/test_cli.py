from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO
from hermes_crew import cli, mailbox, state, tasks


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
    rc, _, err = as_role("lead", "verify", "T1")
    assert rc == 2


def test_verify_cli(team, as_role):
    tasks.add(team, "lead", "x", owner="Eng")
    rc, _, err = as_role("Eng", "verify", "T9", "--", "true")
    assert rc == 1 and "no task T9" in err
    assert as_role("Eng", "verify", "T1", "--", sys.executable, "-c", "import sys; sys.exit(4)")[0] == 4
    assert as_role("Eng", "verify", "--timeout", "5", "T1", "--", "true")[0] == 0
    assert as_role("Eng", "verify", "T1", "--timeout", "5", "--", "true")[0] == 0
    assert as_role("Eng", "verify", "T1", sys.executable, "-c", "pass")[0] == 0   # no "--"
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
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "HERMES_TEAM_DIR": str(tmp_path / "p"),
           "HERMES_TEAM_ROLE": "lead", "TMUX_TMPDIR": str(tmp_path)}
    p = subprocess.run([sys.executable, str(REPO / "bin" / "hermes-crew"), "--version"], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout.strip() == "hermes-crew 3.0.0"
    p = subprocess.run([sys.executable, str(REPO / "bin" / "hermes-crew"), "task", "add", "hello"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0 and "added T1" in p.stdout
    assert (tmp_path / "p" / ".team" / ".gitignore").exists()
    env["HERMES_CREW_LIB"] = str(tmp_path / "nowhere")
    p = subprocess.run([sys.executable, str(REPO / "bin" / "hermes-crew"), "--version"], capture_output=True, text=True, env=env)
    assert p.returncode == 0          # falls back to the checkout
