"""verify.py, status.py (model, render, done-check)"""
from __future__ import annotations

import json
import os
import sys
import time

from hermes_crew import board, events, state, status, tasks, verify


def test_verify_records_exit_and_tail(team, tmp_path):
    big = "x" * 5000
    rec = verify.run(team, "Eng", "T1", [sys.executable, "-u", "-c", f"import sys; print('{big}'); print('err', file=sys.stderr); sys.exit(3)"],
                     cwd=str(tmp_path))
    assert rec["exit"] == 3 and rec["cwd"] == str(tmp_path)
    assert len(rec["out_tail"]) == 2048 and rec["out_tail"].endswith("err\n")
    assert len(rec["out_sha"]) == 16 and rec["duration_s"] >= 0
    saved = json.loads(team.path("verify.jsonl").read_text())
    assert saved["task"] == "T1" and saved["exit"] == 3
    assert events.read(team, kind="verify")[0]["exit"] == 3


def test_verify_shell_string_and_echo(team):
    seen = []
    rec = verify.run(team, "Eng", "T1", ["echo a && echo b"], echo=seen.append)
    assert rec["exit"] == 0 and b"".join(seen) == b"a\nb\n"


def test_verify_timeout_and_missing_command(team):
    t0 = time.monotonic()
    rec = verify.run(team, "Eng", "T1", [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.5)
    assert rec["exit"] == 124 and time.monotonic() - t0 < 5 and "timed out" in rec["out_tail"]
    rec = verify.run(team, "Eng", "T1", ["/no/such/cmd"])
    assert rec["exit"] == 127
    assert [r["exit"] for r in verify.records(team, "T1")] == [124, 127]
    assert verify.last_by_task(team)["T1"]["exit"] == 127


def _approve(team, role, f):
    board.approve(team, role, f, board.sha12(team.root / f), "read all of it and ran the examples")


def test_done_check(team):
    v = status.done_check(team)
    assert len(v) == 1 and "nothing recorded" in v[0]
    tasks.add(team, "lead", "build", owner="Eng")
    (team.root / "a.py").write_text("print(1)\n")
    board.own(team, "Eng", "a.py")
    verify.run(team, "Eng", "T1", ["false"])
    v = status.done_check(team)
    assert any("task T1 'build' is claimed, Eng" in x for x in v)
    assert any("file a.py (owner Eng) has no OK approval" in x for x in v)
    assert any("task T1: last verify failed (exit 1)" in x for x in v)
    tasks.done(team, "Eng", "T1")
    _approve(team, "Rev", "a.py")
    verify.run(team, "Eng", "T1", ["true"])
    assert status.done_check(team) == []
    (team.root / "a.py").write_text("print(2)\n")              # approval becomes stale
    v = status.done_check(team)
    assert len(v) == 1 and "STALE approval by Rev" in v[0]


def test_model_and_render_fit_80_columns(team):
    now = time.time()
    state.update(team, "lead", state="idle", pid=os.getpid(), heartbeat=now)
    state.update(team, "Engineer_with_long_name", state="busy", pid=os.getpid(), heartbeat=now, task="T1",
                 tokens_in=123456, tokens_out=7890, last_error="rate limit " * 20)
    state.update(team, "Rev", state="blocked", blocked_kind="approval", pid=os.getpid(), heartbeat=now)
    state.update(team, "Gone", state="idle", pid=2 ** 22 + 99, heartbeat=now - 500)
    tasks.add(team, "lead", "A very long task title that goes on and on and on " * 3, owner="Rev")
    tasks.add(team, "lead", "second", deps=["T1"])
    tasks.block(team, "Rev", "T1", "needs the API key from the user, which nobody has given yet")
    (team.root / "deep" / "path").mkdir(parents=True)
    f = "deep/path/" + "very_long_file_name_" * 4 + ".py"
    (team.root / f).write_text("x")
    board.own(team, "Engineer_with_long_name", f)
    board.decide(team, "lead", "use JSON lines everywhere " * 6)
    m = status.model(team, now)
    json.dumps(m)                                              # --json must be serializable
    roles = {a["role"]: a for a in m["agents"]}
    assert roles["Gone"]["dead"] and roles["lead"]["alive"]
    text = status.render(m, width=80)
    lines = text.splitlines()
    assert max(len(x) for x in lines) <= 80, "\n".join(x for x in lines if len(x) > 80)
    assert "blocked" in text and "waits for human: approval" in text
    assert "DEAD" in text and "123k/7.9k" in text
    assert "NOT DONE" in text and "waits T1" in text
    assert lines[0].startswith("Hermes Crew")


def test_human_formats():
    assert status.human_tokens(999) == "999" and status.human_tokens(12000) == "12k"
    assert status.human_tokens(2_500_000) == "2.5M"
    assert status.human_age(5) == "5s" and status.human_age(125) == "2m" and status.human_age(3700) == "1h01"
    assert status.human_age(None) == "-"
