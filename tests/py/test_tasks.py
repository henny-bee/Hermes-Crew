from __future__ import annotations

import json
import multiprocessing as mp

import pytest

import mp_workers
from hermes_crew import events, mailbox, tasks
from hermes_crew.tasks import TaskError


def test_add_ids_and_fold(team):
    t1 = tasks.add(team, "lead", "Plan")
    t2 = tasks.add(team, "lead", "Build", deps=["t1"], priority=1, owner="Eng", desc="the thing")
    assert (t1["id"], t2["id"]) == ("T1", "T2")
    assert t1["status"] == "open" and t1["ready"]
    assert t2["status"] == "claimed" and t2["owner"] == "Eng" and t2["deps"] == ["T1"]
    assert not t2["ready"] and t2["waiting_on"] == ["T1"] and t2["priority"] == 1
    lines = [json.loads(x) for x in team.path("tasks.jsonl").read_text().splitlines()]
    assert all({"ts", "t", "by", "op", "id"} <= set(x) for x in lines)
    assert [e["kind"] for e in events.read(team, kind="task.")] == ["task.add", "task.add"]


@pytest.mark.parametrize("kw,msg", [
    ({"deps": ["T9"]}, "unknown dependency T9"),
    ({"priority": 0}, "priority must be >= 1"),
    ({"priority": "x"}, "must be a number"),
])
def test_add_rejects(team, kw, msg):
    tasks.add(team, "lead", "a")
    with pytest.raises(TaskError, match=msg):
        tasks.add(team, "lead", "b", **kw)
    with pytest.raises(TaskError, match="needs a title"):
        tasks.add(team, "lead", "  ")


def test_cycle_rejected_on_edit(team):
    tasks.add(team, "lead", "a")
    tasks.add(team, "lead", "b", deps=["T1"])
    tasks.add(team, "lead", "c", deps=["T2"])
    with pytest.raises(TaskError, match=r"dependency cycle T1 -> T3 -> T2 -> T1|dependency cycle"):
        tasks.edit(team, "lead", "T1", deps=["T3"])
    with pytest.raises(TaskError, match="itself"):
        tasks.edit(team, "lead", "T1", deps=["T1"])
    assert tasks.edit(team, "lead", "T3", title="C!", priority=2)["title"] == "C!"


def test_claim_rules(team):
    tasks.add(team, "lead", "a")
    tasks.add(team, "lead", "b", deps=["T1"])
    with pytest.raises(TaskError, match=r"T2 is not ready - waiting for T1 \(open\)"):
        tasks.claim(team, "Eng", "T2")
    tasks.claim(team, "Eng", "T1")
    with pytest.raises(TaskError, match="REFUSED: T1 is claimed by Eng - ask Eng or lead"):
        tasks.claim(team, "Rev", "T1")
    assert tasks.claim(team, "eng", "T1")["owner"] == "Eng"     # idempotent for the owner
    with pytest.raises(TaskError, match="no task T7"):
        tasks.claim(team, "Eng", "T7")
    with pytest.raises(TaskError, match="not a task id"):
        tasks.claim(team, "Eng", "foo")


def test_owner_only_ops(team):
    tasks.add(team, "lead", "a")
    with pytest.raises(TaskError, match="has no owner - claim it first"):
        tasks.start(team, "Eng", "T1")
    tasks.claim(team, "Eng", "T1")
    with pytest.raises(TaskError, match="only Eng or lead can start"):
        tasks.start(team, "Rev", "T1")
    assert tasks.start(team, "Eng", "T1")["status"] == "in_progress"
    assert tasks.block(team, "lead", "T1", "waiting for API key")["status"] == "blocked"
    with pytest.raises(TaskError, match="say why"):
        tasks.block(team, "Eng", "T1", " ")
    assert tasks.unblock(team, "Eng", "T1")["status"] == "in_progress"
    with pytest.raises(TaskError, match="not blocked"):
        tasks.unblock(team, "Eng", "T1")
    with pytest.raises(TaskError, match="only Eng or lead can finish"):
        tasks.done(team, "Rev", "T1")
    assert tasks.done(team, "Eng", "T1")["status"] == "done"
    with pytest.raises(TaskError, match="already done"):
        tasks.done(team, "Eng", "T1")
    with pytest.raises(TaskError, match="already done"):
        tasks.claim(team, "Rev", "T1")
    t = tasks.reopen(team, "Rev", "T1", "tests fail on empty input")
    assert t["status"] == "claimed" and t["reason"] == "tests fail on empty input"
    assert "reopened T1" in mailbox.pending(team, "Eng")[-1]["body"]


def test_done_notifies_newly_ready(team):
    tasks.add(team, "lead", "a", owner="Eng")
    tasks.add(team, "lead", "b", owner="Eng")
    tasks.add(team, "lead", "c", deps=["T1", "T2"], owner="Rev")
    tasks.add(team, "lead", "d", deps=["T1"])                       # unowned -> lead
    tasks.done(team, "Eng", "T1")
    lead_msgs = [m["body"] for m in mailbox.pending(team, "lead")]
    assert lead_msgs == ["T4 'd' is unblocked (deps done: T1) - it has no owner: hermes-crew task assign T4 <Role>"]
    assert mailbox.pending(team, "Rev") == []                      # T3 still waits for T2
    tasks.done(team, "Eng", "T2")
    rev = [m["body"] for m in mailbox.pending(team, "Rev")]
    assert rev == ["T3 'c' is unblocked (deps done: T1, T2). Start it: hermes-crew task start T3"]
    assert all(m["from"] == "hermes-crew" for m in mailbox.pending(team, "Rev"))


def test_handoff(team, tmp_path):
    tasks.add(team, "lead", "a", owner="Eng")
    with pytest.raises(TaskError, match="does not exist"):
        tasks.done(team, "Eng", "T1", handoff=tmp_path / "nope.md")
    note = tmp_path / "n.md"
    note.write_text("parser done; edge cases in tests/test_x.py")
    t = tasks.done(team, "Eng", "T1", handoff=note)
    assert t["handoff"] == ".team/handoff/T1.md"
    assert team.path("handoff", "T1.md").read_text() == note.read_text()


def test_orphan(team):
    tasks.add(team, "lead", "a", owner="Eng")
    tasks.add(team, "lead", "b", owner="Eng")
    tasks.add(team, "lead", "c")
    tasks.done(team, "Eng", "T2")
    with pytest.raises(TaskError, match="only lead"):
        tasks.orphan(team, "Rev", "T1", "x")
    assert tasks.orphan_tasks_of(team, "hermes-crew", "eng", "Eng died") == ["T1"]
    t = tasks.get(team, "T1")
    assert t["status"] == "orphaned" and t["owner"] is None and t["prev_owner"] == "Eng"
    assert tasks.orphan(team, "lead", "T3", "x") is None                # unowned: no-op
    assert tasks.claim(team, "Rev", "T1")["owner"] == "Rev"             # orphaned can be claimed


def test_assign(team):
    tasks.add(team, "lead", "a")
    assert tasks.assign(team, "Rev", "T1", "Eng")["status"] == "claimed"   # unowned: anyone may propose
    with pytest.raises(TaskError, match="only Eng or lead can reassign"):
        tasks.assign(team, "Rev", "T1", "Rev")
    assert tasks.assign(team, "lead", "T1", "Rev")["owner"] == "Rev"
    assert "assigned you T1" in mailbox.pending(team, "Rev")[0]["body"]


def test_concurrent_ids_unique(team, tmp_path):
    ctx = mp.get_context("spawn")
    outs = [tmp_path / f"o{i}.json" for i in range(5)]
    ps = [ctx.Process(target=mp_workers.add_tasks, args=(str(team.root), i, 12, str(o))) for i, o in enumerate(outs)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
        assert p.exitcode == 0
    ids = [i for o in outs for i in json.loads(o.read_text())]
    assert sorted(ids, key=lambda x: int(x[1:])) == [f"T{i}" for i in range(1, 61)]
    assert len(tasks.list_tasks(team)) == 60


def test_concurrent_claim_one_winner(team, tmp_path):
    for _ in range(4):
        tasks.add(team, "lead", "x")
    ctx = mp.get_context("spawn")
    for tid in ("T1", "T2", "T3", "T4"):
        outs = [tmp_path / f"{tid}-{i}.json" for i in range(4)]
        ps = [ctx.Process(target=mp_workers.claim_task, args=(str(team.root), f"R{i}", tid, str(o)))
              for i, o in enumerate(outs)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(60)
        assert sum(json.loads(o.read_text()) for o in outs) == 1
    claims = [x for x in tasks.history(team, "T1") if x["op"] == "claim"]
    assert len(claims) == 1


def test_done_with_note(team, tmp_path):
    tasks.add(team, "lead", "a", owner="Eng")
    tasks.add(team, "lead", "b", owner="Eng")
    with pytest.raises(TaskError, match="not both"):
        tasks.done(team, "Eng", "T1", handoff=tmp_path / "x", note="y")
    with pytest.raises(TaskError, match="--note is empty"):
        tasks.done(team, "Eng", "T1", note="  ")
    t = tasks.done(team, "Eng", "T1", note="parser done.\nedge cases: see tests/test_x.py")
    assert t["handoff"] == ".team/handoff/T1.md"
    assert team.path("handoff", "T1.md").read_text() == "parser done.\nedge cases: see tests/test_x.py\n"
    assert tasks.handoff_text(team, t).startswith("parser done.")
    assert list(team.root.iterdir()) == [team.dir]          # nothing written into the project
    assert tasks.handoff_text(team, tasks.get(team, "T2")) is None
