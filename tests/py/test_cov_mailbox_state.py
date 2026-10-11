"""Coverage for mailbox.py edge paths (dedup, races, expiry, rendering) and state.py (liveness)."""

from __future__ import annotations

import os
import time

import pytest
from hermes_crew import events, mailbox, state, store


# ---------------------------------------------------------------- mailbox
def test_box_prefers_existing_dir_with_other_case(team):
    mailbox.send(team, "lead", "Dev", "hi")
    assert mailbox.box(team, "dev").name == "Dev"
    assert mailbox.box(team, "nobody").name == "nobody"


def test_send_to_other_case_lands_in_existing_box(team):
    m1 = mailbox.send(team, "lead", "Dev", "one")
    m2 = mailbox.send(team, "lead", "dev", "two")
    assert m1["to"] == m2["to"] == "Dev"
    assert [m["body"] for m in mailbox.pending(team, "DEV")] == ["one", "two"]


def test_pending_empty_and_ignores_garbage(team):
    assert mailbox.pending(team, "Nobody") == []
    mailbox.send(team, "lead", "Dev", "ok")
    (mailbox.box(team, "Dev") / "new" / "zzz.json").write_text("{bad")
    assert [m["body"] for m in mailbox.pending(team, "Dev")] == ["ok"]


def test_claim_is_exclusive_and_dedups(team):
    m = mailbox.send(team, "lead", "Dev", "x")
    got = mailbox.claim(team, "Dev", m["id"])
    assert got and got["body"] == "x"
    assert mailbox.claim(team, "Dev", m["id"]) is None  # nothing in new/ any more
    # a duplicate copy reappearing in new/ is dropped, never re-delivered
    b = mailbox.box(team, "Dev")
    store.write_json_atomic(b / "new" / f"{m['id']}.json", m)
    assert mailbox.claim(team, "Dev", m["id"]) is None
    assert not (b / "new" / f"{m['id']}.json").exists()
    assert mailbox.pending(team, "Dev") == []


def test_claim_dedup_tolerates_missing_src(team):
    m = mailbox.send(team, "lead", "Dev", "x")
    mailbox.claim(team, "Dev", m["id"])
    assert mailbox.claim(team, "Dev", m["id"]) is None


def test_claim_when_link_unavailable_falls_back_to_rename(team, monkeypatch):
    m = mailbox.send(team, "lead", "Dev", "x")

    def nolink(*_a, **_k):
        raise PermissionError("no hardlinks here")

    monkeypatch.setattr(mailbox.os, "link", nolink)
    got = mailbox.claim(team, "Dev", m["id"])
    assert got and got["id"] == m["id"]
    assert [d["id"] for d in mailbox.delivered(team, "Dev")] == [m["id"]]


def test_claim_returns_none_if_both_link_and_rename_fail(team, monkeypatch):
    m = mailbox.send(team, "lead", "Dev", "x")
    monkeypatch.setattr(mailbox.os, "link", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(mailbox.os, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert mailbox.claim(team, "Dev", m["id"]) is None


@pytest.mark.parametrize("exc", [FileExistsError, FileNotFoundError])
def test_claim_loses_race(team, monkeypatch, exc):
    m = mailbox.send(team, "lead", "Dev", "x")
    monkeypatch.setattr(mailbox.os, "link", lambda *a, **k: (_ for _ in ()).throw(exc()))
    assert mailbox.claim(team, "Dev", m["id"]) is None


def test_claim_survives_concurrent_unlink_of_src(team, monkeypatch):
    m = mailbox.send(team, "lead", "Dev", "x")
    real_unlink = os.unlink

    def gone(p, *a, **k):
        real_unlink(p)
        raise FileNotFoundError(p)

    monkeypatch.setattr(mailbox.os, "unlink", gone)
    got = mailbox.claim(team, "Dev", m["id"])
    assert got and got["id"] == m["id"]


def test_claim_all_unclaim_and_delivered(team):
    ids = [mailbox.send(team, "lead", "Dev", f"m{i}")["id"] for i in range(3)]
    got = mailbox.claim_all(team, "Dev")
    assert [g["body"] for g in got] == ["m0", "m1", "m2"]
    assert mailbox.pending(team, "Dev") == []
    assert mailbox.unclaim(team, "Dev", ids[1]) is True
    assert mailbox.unclaim(team, "Dev", ids[1]) is False  # already back in new/
    assert [m["body"] for m in mailbox.pending(team, "Dev")] == ["m1"]
    assert [m["body"] for m in mailbox.delivered(team, "Dev", 1)] == ["m2"]
    assert mailbox.delivered(team, "Dev", 0) == []
    assert mailbox.delivered(team, "Nobody") == []


def test_mark_delivered_emits_events(team):
    m = mailbox.send(team, "lead", "Dev", "x")
    mailbox.mark_delivered(team, "Dev", [m])
    ev = events.read(team, kind="msg.delivered")
    assert any(e.get("id") == m["id"] for e in ev)


def _age(team, role, mid, seconds):
    p = mailbox.box(team, role) / "new" / f"{mid}.json"
    m = store.read_json(p)
    m["ts"] = time.time() - seconds
    store.write_json_atomic(p, m)


def test_expire_moves_old_messages_and_tells_sender(team):
    assert mailbox.expire(team) == []  # no mail dir yet
    old = mailbox.send(team, "Planner", "Dev", "stale", ttl_s=10)
    fresh = mailbox.send(team, "Planner", "Dev", "fresh", ttl_s=10)
    mailbox.send(team, "lead", "Dev", "bad ts")
    _age(team, "Dev", old["id"], 100)
    gone = mailbox.expire(team)
    assert [g["id"] for g in gone] == [old["id"]]
    assert [m["id"] for m in mailbox.expired_msgs(team, "Dev")] == [old["id"]]
    assert fresh["id"] in [m["id"] for m in mailbox.pending(team, "Dev")]
    notice = mailbox.pending(team, "Planner")
    assert len(notice) == 1 and notice[0]["from"] == "hermes-crew" and "expired" in notice[0]["body"]
    assert mailbox.expired_msgs(team, "Nobody") == []


def test_expire_does_not_notify_system_or_user_and_skips_junk(team):
    a = mailbox.send(team, "user", "Dev", "x", ttl_s=1)
    b = mailbox.send(team, "hermes-crew", "Dev", "y", ttl_s=1)
    for m in (a, b):
        _age(team, "Dev", m["id"], 50)
    d = mailbox.box(team, "Dev") / "new"
    (d / "junk.json").write_text("{bad")
    store.write_json_atomic(d / "weird.json", {"id": "w", "ts": "NaN?", "ttl_s": "x"})
    (team.path("mail") / "file-not-dir").write_text("")
    assert len(mailbox.expire(team)) == 2
    assert mailbox.pending(team, "user") == [] and mailbox.pending(team, "hermes-crew") == []


def test_expire_skips_message_claimed_meanwhile(team, monkeypatch):
    m = mailbox.send(team, "Planner", "Dev", "x", ttl_s=1)
    _age(team, "Dev", m["id"], 50)
    monkeypatch.setattr(mailbox.os, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert mailbox.expire(team) == []


def test_render_variants():
    assert mailbox.render([]) == ""
    one = {"id": "1-Dev-abcd", "from": "Dev", "sent_at": "2026-01-01T10:20:30", "body": "hello"}
    out = mailbox.render([one])
    assert out.startswith("[from Dev sent 10:20:30 · #abcd] hello") and 'send Dev "..."' in out
    two = {**one, "id": "2-QA-ef01", "from": "QA"}
    out = mailbox.render([one, two])
    assert out.startswith("You have 2 new team messages") and "send <Role>" in out
    sysmsg = {**one, "from": "hermes-crew"}
    assert "reply:" not in mailbox.render([sysmsg, {**one, "from": "user"}])
    assert mailbox.short_id("a-b-c") == "c"


def test_clip():
    assert mailbox._clip("a\nb", 10) == "a b"
    assert mailbox._clip("x" * 50, 10).endswith("…") and len(mailbox._clip("x" * 50, 10)) == 10


# ---------------------------------------------------------------- state
def test_update_creates_then_merges_and_tracks_since(team):
    r = state.update(team, "Dev", state="idle", pid=os.getpid())
    assert r["state"] == "idle" and r["role"] == "Dev" and r["started"]
    s1 = r["since"]
    time.sleep(0.01)
    r2 = state.update(team, "Dev", turns=3)
    assert r2["since"] == s1 and r2["turns"] == 3
    r3 = state.set_state(team, "Dev", "busy", turn_id="t1")
    assert r3["since"] > s1 and r3["state"] == "busy"


def test_blocked_kind_cleared_when_leaving_blocked(team):
    state.set_state(team, "Dev", "blocked", blocked_kind="approval")
    assert state.read(team, "Dev")["blocked_kind"] == "approval"
    assert state.set_state(team, "Dev", "idle")["blocked_kind"] is None
    state.set_state(team, "Dev", "blocked", blocked_kind="x")
    # explicit blocked_kind survives a non-blocked transition when the caller says so
    assert state.set_state(team, "Dev", "busy", blocked_kind="keep")["blocked_kind"] == "keep"


def test_update_rejects_unknown_state(team):
    with pytest.raises(ValueError):
        state.update(team, "Dev", state="sleeping")


def test_state_change_emits_event(team):
    state.set_state(team, "Dev", "idle")
    state.set_state(team, "Dev", "busy")
    changes = [(e.get("from"), e.get("to")) for e in events.read(team, kind="state")]
    assert (None, "idle") in changes or ("starting", "idle") in changes
    assert ("idle", "busy") in changes


def test_read_case_insensitive_and_garbage(team):
    state.update(team, "Dev", state="idle")
    assert state.read(team, "dev")["role"] == "Dev"
    assert state.read(team, "nobody") is None
    (team.dir / "agents" / "Bad.json").write_text("[1]")
    assert state.read(team, "Bad") is None
    assert [r["role"] for r in state.all_agents(team)] == ["Dev"]


def test_all_agents_sorts_lead_first(team):
    for r in ("Zed", "lead", "Amy"):
        state.update(team, r, state="idle")
    assert [r["role"] for r in state.all_agents(team)] == ["lead", "Amy", "Zed"]
    assert state.all_agents(type(team)(team.root / "nowhere")) == []


def test_heartbeat_and_tokens_accumulate(team):
    state.update(team, "Dev", state="idle")
    rec = state.heartbeat(team, "Dev")
    assert rec["pid"] == os.getpid() and rec["heartbeat"]
    assert state.heartbeat(team, "Dev", pid=4242)["pid"] == 4242
    state.add_tokens(team, "Dev", 10, 5)
    rec = state.add_tokens(team, "Dev", 1, None)
    assert (rec["tokens_in"], rec["tokens_out"]) == (11, 5)


@pytest.mark.parametrize(
    "pid,expected", [(None, False), ("x", False), (0, False), (-3, False), (os.getpid(), True), (2**22 + 12345, False)]
)
def test_pid_exists(pid, expected):
    assert state.pid_exists(pid) is expected


def test_pid_exists_permission_and_oserror(monkeypatch):
    def deny(*_a):
        raise PermissionError

    monkeypatch.setattr(state.os, "kill", deny)
    assert state.pid_exists(5) is True

    def other(*_a):
        raise OSError

    monkeypatch.setattr(state.os, "kill", other)
    assert state.pid_exists(5) is False


def test_liveness():
    now = 1000.0
    live = {"pid": os.getpid(), "heartbeat": now - 5, "state": "idle"}
    assert state.is_alive(live, now) and not state.is_dead(live, now)
    stale = {**live, "heartbeat": now - 500}
    assert not state.is_alive(stale, now) and state.is_dead(stale, now)
    assert not state.is_dead({**stale, "state": "exited"}, now)
    assert not state.is_alive({**live, "heartbeat": "junk"}, now)
    assert not state.is_alive({**live, "heartbeat": None}, now)
    assert not state.is_alive(None) and not state.is_dead(None)
