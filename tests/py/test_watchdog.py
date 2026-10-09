"""hermes_crew.watchdog: pure escalation rules + dedup."""
from __future__ import annotations

from hermes_crew import watchdog
from hermes_crew.watchdog import Escalation, check, dedup

NOW = 1_000_000.0


def agent(role, state="idle", since=NOW - 10, alive=True, **kw):
    return {"role": role, "state": state, "since": since, "pid": 4242, "started": NOW - 3600,
            "heartbeat": NOW - 5, "_alive": alive, **kw}


def alive(rec, now):
    return rec.get("_alive", True)


def run(agents, tasks=(), **kw):
    return check(NOW, agents, tasks, alive=alive, **kw)


def kinds(escs):
    return sorted(e.kind for e in escs)


def test_quiet_team_has_no_escalations():
    agents = [agent("lead"), agent("Coder", "busy"), agent("Tester")]
    assert run(agents, [{"id": "T1", "title": "x", "status": "in_progress", "owner": "Coder"}]) == []


def test_blocked_over_180s():
    a = agent("Coder", "blocked", since=NOW - 181, blocked_kind="clarify")
    [e] = run([agent("lead", "busy"), a])
    assert e.kind == "blocked" and e.role == "Coder"
    assert e.text.startswith("Coder waits for your input in its pane: clarify")
    assert run([agent("lead", "busy"), agent("Coder", "blocked", since=NOW - 179)]) == []


def test_blocked_key_changes_per_episode():
    e1 = run([agent("Coder", "blocked", since=NOW - 200)])[0]
    e2 = run([agent("Coder", "blocked", since=NOW - 190)])[0]
    assert e1.key != e2.key


def test_dead_agent_orphans_its_unfinished_tasks():
    tasks = [
        {"id": "T1", "title": "api", "status": "in_progress", "owner": "Coder"},
        {"id": "T2", "title": "db", "status": "claimed", "owner": "coder"},
        {"id": "T3", "title": "ui", "status": "done", "owner": "Coder"},
        {"id": "T4", "title": "x", "status": "in_progress", "owner": "Tester"},
        {"id": "T5", "title": "y", "status": "blocked", "owner": "Coder"},
    ]
    escs = run([agent("lead", "busy"), agent("Coder", "busy", alive=False), agent("Tester", "busy")], tasks)
    [e] = escs
    assert e.kind == "dead" and e.orphan_tasks == ("T1", "T2", "T5")
    assert "Coder is gone" in e.text and "T1 'api'" in e.text and "T4" not in e.text


def test_exited_agent_is_not_dead():
    assert run([agent("Coder", "exited", alive=False)]) == []


def test_dead_agent_without_tasks():
    [e] = run([agent("Coder", "idle", alive=False)])
    assert e.orphan_tasks == () and "no open tasks" in e.text


def test_lead_and_system_roles_are_not_watched():
    assert run([agent("lead", "blocked", since=NOW - 999, alive=False),
                agent("user", alive=False)]) == []


def test_failed_turn_once_per_turn():
    a = agent("Coder", "idle", last_error="HTTP 500 server_error: boom", turn_id="t-1")
    [e] = run([agent("lead", "busy"), a])
    assert e.kind == "failed" and "Coder's last turn failed: HTTP 500" in e.text
    b = dict(a, turn_id="t-2")
    assert run([agent("lead", "busy"), b])[0].key != e.key
    # a busy agent with a transient api error is not escalated yet
    assert run([agent("lead", "busy"), agent("Coder", "busy", last_error="429")]) == []


def test_expired_messages_skip_lead_sender():
    expired = [{"id": "abc-Coder-1f2e", "from": "Coder", "to": "Tester", "body": "please review"},
               {"id": "abd-lead-0000", "from": "lead", "to": "Tester", "body": "x"},
               {"id": "abe-hermes-crew-0001", "from": "hermes-crew", "to": "Tester", "body": "y"}]
    escs = run([agent("lead", "busy")], expired=expired)
    assert [e.key for e in escs] == ["expired:abc-Coder-1f2e"]
    assert "#1f2e from Coder to Tester expired" in escs[0].text


def test_stall_when_everyone_idle_and_work_open():
    agents = [agent("lead", since=NOW - 300), agent("Coder", since=NOW - 130), agent("Tester", since=NOW - 125)]
    tasks = [{"id": "T1", "title": "api", "status": "claimed", "owner": "Coder"},
             {"id": "T2", "title": "docs", "status": "open", "owner": None},
             {"id": "T3", "title": "done", "status": "done", "owner": "Tester"}]
    [e] = run(agents, tasks, unapproved=[("src/app.py", "Coder")])
    assert e.kind == "stall"
    assert "T1 'api' claimed — owner Coder" in e.text
    assert "T2 'docs' open — owner nobody" in e.text
    assert "T3" not in e.text
    assert "src/app.py (owner Coder) has no current approval" in e.text
    assert e.key == f"stall:{NOW - 125}"


def test_no_stall_cases():
    tasks = [{"id": "T1", "title": "api", "status": "in_progress", "owner": "Coder"}]
    idle_long = NOW - 500
    # someone recently active
    assert run([agent("lead", since=idle_long), agent("Coder", since=NOW - 60)], tasks) == []
    # lead busy
    assert run([agent("lead", "busy"), agent("Coder", since=idle_long)], tasks) == []
    # nothing outstanding
    done = [{"id": "T1", "title": "api", "status": "done", "owner": "Coder"}]
    assert run([agent("lead", since=idle_long), agent("Coder", since=idle_long)], done) == []
    # a teammate is blocked (not idle) — blocked rule applies instead
    escs = run([agent("lead", since=idle_long), agent("Coder", "blocked", since=idle_long)], tasks)
    assert kinds(escs) == ["blocked"]
    # no live teammates at all
    assert run([agent("lead", since=idle_long)], tasks) == []


def test_stall_ignores_dead_and_exited_teammates():
    tasks = [{"id": "T1", "title": "api", "status": "orphaned", "owner": None}]
    agents = [agent("lead", since=NOW - 500), agent("Coder", since=NOW - 500),
              agent("Old", "exited", alive=False)]
    assert kinds(run(agents, tasks)) == ["stall"]


def test_dedup_pure():
    a, b = Escalation("k1", "stall", "x"), Escalation("k2", "dead", "y")
    new, keys = dedup([a, b, a], ["k0"])
    assert new == [a, b] and keys == ["k0", "k1", "k2"]
    new, keys = dedup([a, b], keys)
    assert new == []


def test_dedup_keeps_bounded_history():
    keys = [f"k{i}" for i in range(watchdog.KEEP_KEYS + 50)]
    _, kept = dedup([Escalation("new", "stall", "t")], keys)
    assert len(kept) == watchdog.KEEP_KEYS and kept[-1] == "new"


def test_filter_new_persists(team):
    e = Escalation("dead:Coder:1:2", "dead", "gone")
    assert watchdog.filter_new(team, [e]) == [e]
    assert watchdog.filter_new(team, [e]) == []
    assert (team.dir / "escalations.json").exists()
    assert watchdog.filter_new(team, []) == []


def test_default_alive_uses_pid_and_heartbeat():
    import os
    import time
    now = time.time()
    assert watchdog.default_alive({"pid": os.getpid(), "heartbeat": now - 1}, now)
    assert not watchdog.default_alive({"pid": os.getpid(), "heartbeat": now - 120}, now)
    assert not watchdog.default_alive({"pid": 2 ** 22 + 12345, "heartbeat": now}, now)
