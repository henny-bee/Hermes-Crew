"""Coverage for team.py (tmux helpers, identity, role discovery) and store.py (file primitives).

tmux is replaced by a stub so these tests never touch a real server.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from hermes_crew import store
from hermes_crew import team as team_mod
from hermes_crew.team import Team


# ---------------------------------------------------------------- tmux wrapper
def test_tmux_none_when_binary_missing(monkeypatch):
    monkeypatch.setattr(team_mod.shutil, "which", lambda _n: None)
    assert team_mod.tmux("list-panes") is None


def test_tmux_strips_trailing_newline(monkeypatch):
    monkeypatch.setattr(team_mod.shutil, "which", lambda _n: "/usr/bin/tmux")
    monkeypatch.setattr(team_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "x\ny\n", ""))
    assert team_mod.tmux("list-panes") == "x\ny"


def test_tmux_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(team_mod.shutil, "which", lambda _n: "/usr/bin/tmux")
    monkeypatch.setattr(team_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no server"))
    assert team_mod.tmux("list-panes") is None


@pytest.mark.parametrize("exc", [OSError("boom"), subprocess.TimeoutExpired("tmux", 3)])
def test_tmux_none_on_exception(monkeypatch, exc):
    monkeypatch.setattr(team_mod.shutil, "which", lambda _n: "/usr/bin/tmux")

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(team_mod.subprocess, "run", boom)
    assert team_mod.tmux("list-panes") is None


def test_pane_and_window_option(monkeypatch):
    seen = []

    def fake(*args, **_k):
        seen.append(args)
        return "val" if args[0] in ("display", "show") else None

    monkeypatch.setattr(team_mod, "tmux", fake)
    assert team_mod.pane_option("%1", "@role") == "val"
    assert team_mod.window_option("s:0", "@team_dir") == "val"
    assert seen[0][-1] == "#{@role}"
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: "")
    assert team_mod.pane_option("%1", "@role") is None
    assert team_mod.window_option("s:0", "@team_dir") is None


# ---------------------------------------------------------------- my_pane
def test_ppid_reads_proc_and_tolerates_garbage(monkeypatch):
    assert team_mod._ppid(os.getpid()) == os.getppid()  # no /proc on macOS: needs the ps fallback there
    assert team_mod._ppid(2**30) == 0


def test_my_pane_walks_ancestors(monkeypatch):
    tree = {500: 400, 400: 300, 300: 1}
    monkeypatch.setattr(team_mod, "_ppid", lambda p: tree.get(p, 0))
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: "junk\n300 %7\nabc %9\n42 %1")
    assert team_mod.my_pane(500) == "%7"


def test_my_pane_falls_back_to_env_pane(monkeypatch):
    monkeypatch.setattr(team_mod, "_ppid", lambda p: 0)
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: "42 %1\n43 %2")
    monkeypatch.setenv("TMUX_PANE", "%2")
    assert team_mod.my_pane(999) == "%2"
    monkeypatch.setenv("TMUX_PANE", "%99")
    assert team_mod.my_pane(999) is None
    monkeypatch.delenv("TMUX_PANE")
    assert team_mod.my_pane(999) is None


def test_my_pane_none_without_tmux(monkeypatch):
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: None)
    assert team_mod.my_pane() is None


def test_my_pane_survives_parent_cycle(monkeypatch):
    monkeypatch.setattr(team_mod, "_ppid", lambda p: 5 if p == 6 else 6)
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: "42 %1")
    assert team_mod.my_pane(5) is None


# ---------------------------------------------------------------- identity
def test_current_role_env_wins():
    assert team_mod.current_role({"HERMES_TEAM_ROLE": " Dev "}) == "Dev"


def test_current_role_from_pane_option(monkeypatch):
    monkeypatch.setattr(team_mod, "my_pane", lambda: "%3")
    monkeypatch.setattr(team_mod, "pane_option", lambda p, n: "Planner")
    assert team_mod.current_role({}) == "Planner"
    assert team_mod.current_role({}, pane="%4") == "Planner"


def test_current_role_defaults_to_user(monkeypatch):
    monkeypatch.setattr(team_mod, "my_pane", lambda: None)
    assert team_mod.current_role({}) == "user"
    monkeypatch.setattr(team_mod, "my_pane", lambda: "%3")
    monkeypatch.setattr(team_mod, "pane_option", lambda p, n: None)
    assert team_mod.current_role({}) == "user"


def test_role_helpers():
    assert team_mod.valid_role("Eng_1") and not team_mod.valid_role("") and not team_mod.valid_role("a b")
    assert team_mod.same_role("Dev", "dev") and not team_mod.same_role(None, "dev")
    assert team_mod.is_lead("LEAD") and team_mod.is_lead("user") and not team_mod.is_lead(None)


# ---------------------------------------------------------------- Team
def test_team_identity_and_repr(tmp_path):
    a, b = Team(tmp_path), Team(str(tmp_path))
    assert a == b and hash(a) == hash(b) and a != "x"
    assert repr(a).startswith("Team(")
    assert a.path("x", "y") == a.dir / "x" / "y"
    assert not a.exists()
    a.ensure()
    assert a.exists() and (a.dir / ".gitignore").read_text() == "*\n"


def test_resolve_env_dir_wins(tmp_path):
    assert Team.resolve(env={"HERMES_TEAM_DIR": str(tmp_path)}) == Team(tmp_path)


def test_resolve_via_tmux_window_option(monkeypatch, tmp_path):
    monkeypatch.setattr(team_mod, "my_pane", lambda: "%1")
    monkeypatch.setattr(team_mod, "window_option", lambda t, n: str(tmp_path))
    assert Team.resolve(env={}) == Team(tmp_path)


def test_resolve_walks_up_to_dot_team(monkeypatch, tmp_path):
    monkeypatch.setattr(team_mod, "my_pane", lambda: None)
    (tmp_path / ".team").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert Team.resolve(deep, env={}) == Team(tmp_path)


def test_resolve_falls_back_to_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(team_mod, "my_pane", lambda: "%1")
    monkeypatch.setattr(team_mod, "window_option", lambda t, n: None)
    assert Team.resolve(tmp_path, env={}) == Team(tmp_path)


def test_ensure_tolerates_unwritable_gitignore(tmp_path, monkeypatch):
    t = Team(tmp_path)
    real = Path.write_text

    def deny(self, *a, **k):
        if self.name == ".gitignore":
            raise OSError("ro")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", deny)
    t.ensure()
    assert t.exists()


def test_nested_locks_with_different_names_create_files(team):
    with team.lock("a"), team.lock("b"):
        pass
    assert (team.dir / "locks" / "a.lock").exists()


def test_roles_from_files_mail_and_tmux(team, monkeypatch):
    (team.dir / "agents").mkdir()
    (team.dir / "agents" / "Dev.json").write_text("{}")
    (team.dir / "agents" / ".hidden.json").write_text("{}")
    (team.dir / "agents" / "user.json").write_text("{}")
    (team.dir / "briefs").mkdir()
    (team.dir / "briefs" / "Planner.md").write_text("x")
    (team.dir / "briefs" / "bad name.md").write_text("x")
    (team.dir / "mail" / "QA").mkdir(parents=True)
    (team.dir / "mail" / "stray.txt").parent.mkdir(exist_ok=True)
    (team.dir / "mail" / "stray.txt").write_text("")
    (team.dir / "mail" / "dev").mkdir()  # same role, other case: first spelling wins
    monkeypatch.setattr(
        team_mod, "tmux", lambda *a, **k: f"{team.root}\tTester\n/elsewhere\tOther\n{team.root}\t\n\t\n"
    )
    assert team.roles() == ["Dev", "Planner", "QA", "Tester"]
    assert team.canon_role("LEAD") == "lead"
    assert team.canon_role("tester") == "Tester"
    assert team.canon_role("nobody") is None


def test_tmux_roles_empty_without_tmux(team, monkeypatch):
    monkeypatch.setattr(team_mod, "tmux", lambda *a, **k: None)
    assert team.tmux_roles() == []


def test_same_dir_handles_oserror(monkeypatch, tmp_path):
    def boom(self, *a, **k):
        raise OSError("loop")

    monkeypatch.setattr(Path, "resolve", boom)
    assert team_mod._same_dir("/x", tmp_path) is False


# ---------------------------------------------------------------- store
def test_now_iso_format():
    assert len(store.now_iso()) == 19 and store.now_iso(0)[4] == "-"


def test_jsonl_roundtrip_and_corruption(tmp_path):
    p = tmp_path / "d" / "x.jsonl"
    assert store.read_jsonl(p) == []
    store.append_jsonl(p, {"a": 1, "u": "é"})
    store.append_line(p, "not json\n")
    store.append_line(p, "[1,2]")
    store.append_line(p, "   ")
    store.append_jsonl(p, {"b": 2})
    assert store.read_jsonl(p) == [{"a": 1, "u": "é"}, {"b": 2}]


def test_append_line_handles_short_writes(tmp_path, monkeypatch):
    real = os.write
    calls = []

    def short(fd, data):
        calls.append(len(data))
        return real(fd, bytes(data)[:2])

    monkeypatch.setattr(store.os, "write", short)
    store.append_line(tmp_path / "f", "abcdef")
    monkeypatch.undo()
    assert (tmp_path / "f").read_text() == "abcdef\n" and len(calls) > 1


def test_atomic_writes_and_reads(tmp_path):
    store.write_json_atomic(tmp_path / "s" / "a.json", {"k": [1, "é"]})
    assert store.read_json(tmp_path / "s" / "a.json") == {"k": [1, "é"]}
    store.write_text_atomic(tmp_path / "s" / "t.txt", "hi")
    assert (tmp_path / "s" / "t.txt").read_text() == "hi"
    assert not [p for p in (tmp_path / "s").iterdir() if p.name.endswith(".tmp")]


def test_read_json_default(tmp_path):
    assert store.read_json(tmp_path / "missing", default=7) == 7
    (tmp_path / "bad.json").write_text("{nope")
    assert store.read_json(tmp_path / "bad.json", default=[]) == []


def test_atomic_write_failure_cleans_temp_and_keeps_old(tmp_path):
    target = tmp_path / "a.json"
    store.write_json_atomic(target, {"v": 1})
    with pytest.raises(TypeError):
        store.write_json_atomic(target, {"v": object()})
    assert json.loads(target.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_write_text_atomic_failure_cleans_temp(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("replace failed")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.write_text_atomic(tmp_path / "t.txt", "x")
    assert list(tmp_path.iterdir()) == []


def test_flock_releases_after_error(tmp_path):
    p = tmp_path / "l" / "x.lock"
    with pytest.raises(RuntimeError), store.flock(p):
        raise RuntimeError("inside")
    with store.flock(p):  # would deadlock if the first lock leaked
        pass
