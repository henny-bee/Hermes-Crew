"""team.py, store.py, events.py, state.py"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time

import pytest

import mp_workers
from hermes_crew import events, state, store
from hermes_crew.team import Team, current_role, is_lead, same_role, valid_role


# ---------------------------------------------------------------- team
def test_resolve_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_TEAM_DIR", str(tmp_path / "x"))
    assert Team.resolve(cwd=tmp_path).root == (tmp_path / "x").resolve()


def test_resolve_ancestor_with_team(tmp_path):
    (tmp_path / "p" / ".team").mkdir(parents=True)
    sub = tmp_path / "p" / "a" / "b"
    sub.mkdir(parents=True)
    assert Team.resolve(cwd=sub).root == (tmp_path / "p").resolve()


def test_resolve_falls_back_to_cwd(tmp_path):
    assert Team.resolve(cwd=tmp_path).root == tmp_path.resolve()


def test_ensure_writes_gitignore(tmp_path):
    t = Team(tmp_path).ensure()
    assert (t.dir / ".gitignore").read_text() == "*\n"


def test_current_role(monkeypatch):
    assert current_role() == "user"             # no env, no tmux pane
    monkeypatch.setenv("HERMES_TEAM_ROLE", "Engineer")
    assert current_role() == "Engineer"
    assert current_role(env={}) == "user"


def test_role_helpers():
    assert valid_role("Eng_2") and not valid_role("a b") and not valid_role("")
    assert same_role("eng", "ENG") and not same_role("eng", None)
    assert is_lead("lead") and is_lead("user") and not is_lead("Eng")


def test_roles_and_canon(team):
    state.update(team, "Engineer", state="idle")
    (team.path("mail", "Planner")).mkdir(parents=True)
    assert team.roles() == ["Engineer", "Planner"]
    assert team.canon_role("engineer") == "Engineer"
    assert team.canon_role("LEAD") == "lead"
    assert team.canon_role("nobody") is None


# ---------------------------------------------------------------- store
def test_jsonl_roundtrip_skips_garbage(tmp_path):
    p = tmp_path / "a.jsonl"
    store.append_jsonl(p, {"a": 1, "s": "ünï\ncode"})
    with open(p, "a") as f:
        f.write('{"broken": \n')
    store.append_jsonl(p, {"a": 2})
    assert [o["a"] for o in store.read_jsonl(p)] == [1, 2]
    assert store.read_jsonl(tmp_path / "missing") == []


def test_atomic_json(tmp_path):
    p = tmp_path / "d" / "x.json"
    store.write_json_atomic(p, {"k": [1, 2]})
    assert store.read_json(p) == {"k": [1, 2]}
    assert store.read_json(tmp_path / "nope", default=5) == 5
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]   # no temp leftovers


# ---------------------------------------------------------------- events
def test_events_emit_and_filter(team):
    events.emit(team, "Eng", "msg.queued", id="1")
    events.emit(team, "lead", "task.add", id="T1")
    events.emit(team, "Eng", "task.claim", id="T1")
    evs = events.read(team)
    assert [e["kind"] for e in evs] == ["msg.queued", "task.add", "task.claim"]
    assert all({"ts", "t", "role", "kind"} <= set(e) for e in evs)
    assert [e["kind"] for e in events.read(team, kind="task.")] == ["task.add", "task.claim"]
    assert [e["kind"] for e in events.read(team, role="eng")] == ["msg.queued", "task.claim"]
    assert len(events.read(team, n=1)) == 1
    assert "task.claim" in events.format_event(evs[-1])


def test_events_concurrent_appends_are_whole_lines(team):
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=mp_workers.append_events, args=(str(team.root), i, 40)) for i in range(6)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
        assert p.exitcode == 0
    raw = team.path("events.jsonl").read_text().splitlines()
    assert len(raw) == 240
    assert all(json.loads(line)["kind"] == "usage" for line in raw)
    assert len({(json.loads(line)["role"], json.loads(line)["i"]) for line in raw}) == 240


# ---------------------------------------------------------------- state
def test_state_update_and_events(team):
    r = state.update(team, "Eng", state="starting", pid=os.getpid(), heartbeat=time.time())
    assert r["state"] == "starting" and r["tokens_in"] == 0 and r["plugin_version"] == "3.0.0"
    since0 = r["since"]
    time.sleep(0.01)
    r = state.update(team, "Eng", state="busy", turn_id="t1")
    assert r["since"] > since0
    r = state.update(team, "Eng", state="blocked", blocked_kind="approval")
    assert r["blocked_kind"] == "approval"
    r = state.update(team, "Eng", state="busy")
    assert r["blocked_kind"] is None
    r = state.heartbeat(team, "Eng")                       # no state change -> no event
    kinds = [(e["from"], e["to"]) for e in events.read(team, kind="state")]
    assert kinds == [(None, "starting"), ("starting", "busy"), ("busy", "blocked"), ("blocked", "busy")]
    assert state.read(team, "eng")["state"] == "busy"     # case-insensitive read
    with pytest.raises(ValueError):
        state.update(team, "Eng", state="sleepy")


def test_add_tokens(team):
    state.update(team, "Eng", state="idle")
    state.add_tokens(team, "Eng", 10, 5)
    r = state.add_tokens(team, "Eng", 1, 2)
    assert (r["tokens_in"], r["tokens_out"]) == (11, 7)


def test_alive_dead(team):
    now = time.time()
    rec = state.update(team, "Eng", state="idle", pid=os.getpid(), heartbeat=now)
    assert state.is_alive(rec, now) and not state.is_dead(rec, now)
    assert not state.is_alive(rec, now + 61)
    assert state.is_dead(rec, now + 61)
    gone = dict(rec, pid=2 ** 22 + 12345)
    assert not state.is_alive(gone, now)
    assert not state.is_dead(dict(gone, state="exited"), now)


def test_all_agents_lead_first(team):
    for r in ("Zed", "lead", "Amy"):
        state.update(team, r, state="idle")
    assert [a["role"] for a in state.all_agents(team)] == ["lead", "Amy", "Zed"]
