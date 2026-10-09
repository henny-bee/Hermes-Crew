from __future__ import annotations

import json
import multiprocessing as mp

import mp_workers
from hermes_crew import events, mailbox


def test_send_pending_claim(team):
    m1 = mailbox.send(team, "lead", "Eng", "line1\nline2 with 'quotes' $(x) `y`")
    m2 = mailbox.send(team, "Planner", "Eng", "second")
    assert m1["id"] < m2["id"]                               # sortable ids
    assert [m["id"] for m in mailbox.pending(team, "Eng")] == [m1["id"], m2["id"]]
    assert not list((mailbox.box(team, "Eng") / "tmp").iterdir())
    got = mailbox.claim_all(team, "Eng")
    assert [m["body"] for m in got] == [m1["body"], "second"]
    assert mailbox.pending(team, "Eng") == []
    assert mailbox.claim(team, "Eng", m1["id"]) is None       # already taken
    assert [m["id"] for m in mailbox.delivered(team, "Eng")] == [m1["id"], m2["id"]]
    assert events.read(team, kind="msg.queued")[0]["to"] == "Eng"


def test_case_insensitive_box(team):
    mailbox.send(team, "lead", "Engineer", "a")
    mailbox.send(team, "lead", "engineer", "b")
    assert len(mailbox.pending(team, "ENGINEER")) == 2
    assert [d.name for d in team.path("mail").iterdir()] == ["Engineer"]


def test_unclaim_and_redeliver(team):
    m = mailbox.send(team, "lead", "Eng", "x")
    assert mailbox.claim(team, "Eng", m["id"])
    assert mailbox.unclaim(team, "Eng", m["id"])
    assert [p["id"] for p in mailbox.pending(team, "Eng")] == [m["id"]]
    assert mailbox.claim(team, "Eng", m["id"])["body"] == "x"


def test_dedup_never_delivers_twice(team):
    m = mailbox.send(team, "lead", "Eng", "x")
    b = mailbox.box(team, "Eng")
    mailbox.claim(team, "Eng", m["id"])
    # a duplicate copy reappears in new/ (e.g. crash between link and unlink)
    (b / "new" / f"{m['id']}.json").write_text((b / "cur" / f"{m['id']}.json").read_text())
    assert mailbox.claim_all(team, "Eng") == []
    assert mailbox.pending(team, "Eng") == []


def test_expire_notifies_sender(team):
    m = mailbox.send(team, "Planner", "Eng", "old news", ttl_s=10)
    fresh = mailbox.send(team, "lead", "Eng", "fresh", ttl_s=3600)
    gone = mailbox.expire(team, now=m["ts"] + 11)
    assert [g["id"] for g in gone] == [m["id"]]
    assert [p["id"] for p in mailbox.pending(team, "Eng")] == [fresh["id"]]
    notes = mailbox.pending(team, "Planner")
    assert len(notes) == 1 and notes[0]["from"] == "hermes-crew"
    assert "expired undelivered" in notes[0]["body"] and "Eng" in notes[0]["body"]
    assert m["id"] not in {p["id"] for p in mailbox.pending(team, "Eng")}
    assert mailbox.claim(team, "Eng", m["id"]) is None
    assert events.read(team, kind="msg.expired")


def test_expire_system_message_does_not_loop(team):
    m = mailbox.send(team, "hermes-crew", "lead", "x", ttl_s=1)
    mailbox.expire(team, now=m["ts"] + 5)
    assert not team.path("mail", "hermes-crew").exists()


def test_render_single_and_batch():
    a = {"id": "18f-lead-ab12", "from": "lead", "sent_at": "2026-10-09T14:02:11", "body": "hi\nthere"}
    b = {"id": "190-Eng-cd34", "from": "Eng", "sent_at": "2026-10-09T14:02:15", "body": "yo"}
    one = mailbox.render([a])
    assert one.startswith("[from lead sent 14:02:11 · #ab12] hi\nthere")
    assert one.endswith('(reply: hermes-crew send lead "...")')
    many = mailbox.render([a, b])
    assert many.startswith("You have 2 new team messages:\n\n[from lead")
    assert "[from Eng sent 14:02:15 · #cd34] yo" in many
    assert mailbox.render([]) == ""


def test_concurrent_claim_no_duplicates(team, tmp_path):
    n = 120
    for i in range(n):
        mailbox.send(team, "lead", "Eng", f"m{i}")
    ctx = mp.get_context("spawn")
    outs = [tmp_path / f"o{i}.json" for i in range(5)]
    ps = [ctx.Process(target=mp_workers.claim_mail, args=(str(team.root), "Eng", str(o), 30)) for o in outs]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
        assert p.exitcode == 0
    got = [i for o in outs for i in json.loads(o.read_text())]
    assert len(got) == n and len(set(got)) == n


def test_concurrent_send_while_claiming(team, tmp_path):
    ctx = mp.get_context("spawn")
    senders = [ctx.Process(target=mp_workers.send_mail, args=(str(team.root), i, 30)) for i in range(4)]
    outs = [tmp_path / f"c{i}.json" for i in range(3)]
    claimers = [ctx.Process(target=mp_workers.claim_mail, args=(str(team.root), "Eng", str(o), 400)) for o in outs]
    for p in senders + claimers:
        p.start()
    for p in senders + claimers:
        p.join(90)
        assert p.exitcode == 0
    got = [i for o in outs for i in json.loads(o.read_text())] + [m["id"] for m in mailbox.claim_all(team, "Eng")]
    assert len(got) == 120 and len(set(got)) == 120
