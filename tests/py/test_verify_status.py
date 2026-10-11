"""verify.py, status.py (model, render, done-check)"""

from __future__ import annotations

import json
import os
import sys
import time

from hermes_crew import board, events, state, status, store, tasks, verify


def test_verify_records_exit_and_tail(team, tmp_path):
    big = "x" * 5000
    rec = verify.run(
        team,
        "Eng",
        "T1",
        [sys.executable, "-u", "-c", f"import sys; print('{big}'); print('err', file=sys.stderr); sys.exit(3)"],
        cwd=str(tmp_path),
    )
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
    (team.root / "a.py").write_text("print(2)\n")  # approval becomes stale
    v = status.done_check(team)
    assert len(v) == 1 and "STALE approval by Rev" in v[0]


def test_model_and_render_fit_80_columns(team):
    now = time.time()
    state.update(team, "lead", state="idle", pid=os.getpid(), heartbeat=now)
    state.update(
        team,
        "Engineer_with_long_name",
        state="busy",
        pid=os.getpid(),
        heartbeat=now,
        task="T1",
        tokens_in=123456,
        tokens_out=7890,
        last_error="rate limit " * 20,
    )
    state.update(team, "Rev", state="blocked", blocked_kind="approval", pid=os.getpid(), heartbeat=now)
    state.update(team, "Gone", state="idle", pid=2**22 + 99, heartbeat=now - 500)
    tasks.add(team, "lead", "A very long task title that goes on and on and on " * 3, owner="Rev")
    tasks.add(team, "lead", "second", deps=["T1"])
    tasks.block(team, "Rev", "T1", "needs the API key from the user, which nobody has given yet")
    (team.root / "deep" / "path").mkdir(parents=True)
    f = "deep/path/" + "very_long_file_name_" * 4 + ".py"
    (team.root / f).write_text("x")
    board.own(team, "Engineer_with_long_name", f)
    board.decide(team, "lead", "use JSON lines everywhere " * 6)
    m = status.model(team, now)
    json.dumps(m)  # --json must be serializable
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


def test_metrics_wall_clock_reopens_tokens(team, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    def at(t, fn, *a, **k):
        clock[0] = t
        return fn(team, *a, **k)

    at(1000, tasks.add, "lead", "api", owner="Eng")  # owned from the start
    at(1000, tasks.add, "lead", "docs")
    at(1000, tasks.add, "lead", "later")
    at(1010, tasks.start, "Eng", "T1")
    at(1100, tasks.done, "Eng", "T1")
    at(1200, tasks.claim, "Doc", "T2")
    at(1300, tasks.done, "Doc", "T2")
    at(1310, tasks.reopen, "lead", "T2", "typo")
    at(1500, tasks.done, "Doc", "T2")
    state.add_tokens(team, "Eng", 100, 20)
    state.add_tokens(team, "Eng", 100, 20)
    state.add_tokens(team, "Doc", 5, 1)
    m = status.metrics(team)
    assert m["tasks"]["T1"] == {
        "claimed": store.now_iso(1000),
        "done": store.now_iso(1100),
        "wall_s": 100.0,
        "reopens": 0,
    }
    assert m["tasks"]["T2"]["wall_s"] == 300.0 and m["tasks"]["T2"]["reopens"] == 1
    assert m["tasks"]["T3"] == {"claimed": None, "done": None, "wall_s": None, "reopens": 0}
    assert m["roles"]["Eng"] == {"tokens_in": 200, "tokens_out": 40}
    assert m["roles"]["Doc"] == {"tokens_in": 5, "tokens_out": 1}
    assert m["totals"] == {"tasks_timed": 2, "wall_s": 400.0, "reopens": 1, "tokens_in": 205, "tokens_out": 41}
    at(1600, tasks.reopen, "lead", "T1", "broken")  # reopened and not done again: no wall-clock
    m = status.metrics(team)
    assert m["tasks"]["T1"]["wall_s"] is None and m["tasks"]["T1"]["reopens"] == 1


def test_metrics_in_status_json(team, monkeypatch, capsys):
    from hermes_crew import cli

    tasks.add(team, "lead", "x", owner="Eng")
    monkeypatch.setenv("HERMES_TEAM_DIR", str(team.root))
    monkeypatch.setenv("HERMES_TEAM_ROLE", "lead")
    assert cli.main(["status", "--json"]) == 0
    m = json.loads(capsys.readouterr().out)["metrics"]
    assert set(m) == {"tasks", "roles", "totals"} and m["tasks"]["T1"]["claimed"] and m["tasks"]["T1"]["wall_s"] is None
