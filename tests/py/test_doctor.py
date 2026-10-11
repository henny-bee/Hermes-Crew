from __future__ import annotations

import os
import time

import pytest
from hermes_crew import __version__, compat, doctor, state
from hermes_crew.team import Team


def test_enabled_plugins_parse():
    block = "model: x\nplugins:\n  enabled:\n    - foo   # comment\n    - 'hermes-crew'\n  other: 1\ndisplay:\n  x: y\n"
    assert doctor.enabled_plugins(block) == ["foo", "hermes-crew"]
    assert doctor.enabled_plugins('plugins:\n  enabled: [a, "hermes-crew"]\n') == ["a", "hermes-crew"]
    assert doctor.enabled_plugins("plugins:\n  disabled:\n    - hermes-crew\n") == []
    assert doctor.enabled_plugins("other:\n  enabled:\n    - hermes-crew\n") == []
    assert doctor.enabled_plugins("plugins:\n  enabled:\n  - x\n") == ["x"]


def _install(home, version=__version__, enabled=True):
    d = home / "plugins" / "hermes-crew" / "hermes_crew"
    d.mkdir(parents=True)
    (d.parent / "plugin.yaml").write_text("name: hermes-crew\n")
    (d / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - hermes-crew\n" if enabled else "x: 1\n")
    for s in ("hermes-tmux-team", "productivity/hermes-tmux-teammate"):
        (home / "skills" / s).mkdir(parents=True)
        (home / "skills" / s / "SKILL.md").write_text("x")


def test_plugin_and_skill_checks(tmp_path):
    home = tmp_path / "h"
    assert doctor.check_plugin(home)[0] == "FAIL"
    assert doctor.check_enabled(home)[0] == "FAIL"
    _install(home)
    assert doctor.check_plugin(home)[0] == "OK"
    assert doctor.check_enabled(home)[0] == "OK"
    assert [r[0] for r in doctor.check_skills(home)] == ["OK", "OK"]
    (home / "plugins" / "hermes-crew" / "hermes_crew" / "__init__.py").write_text('__version__ = "2.9"\n')
    r = doctor.check_plugin(home)
    assert r[0] == "FAIL" and "re-run install.sh" in r[2]


@pytest.mark.parametrize(
    "version,known,level,detail",
    [
        ("0.21.5", None, "OK", "0.21.5 (tested)"),
        ("0.99.0", None, "WARN", f"0.99.0 untested with hermes-crew {__version__}"),
        ("0.30.1", {("0.30", None): "broken"}, "FAIL", "0.30.1 is known broken"),
        (None, None, "WARN", "cannot determine the installed Hermes version"),
    ],
)
def test_hermes_version_check(monkeypatch, version, known, level, detail):
    seen = []
    monkeypatch.setattr(compat, "installed_hermes_version", lambda env=None: seen.append(env) or version)
    if known is not None:
        monkeypatch.setattr(compat, "KNOWN", known)
    env = {"HOME": "/x"}
    assert doctor.check_hermes_version(env) == (level, "hermes version", detail)
    assert seen == [env]


def test_hermes_version_line_follows_hermes_check(team, monkeypatch, tmp_path):
    monkeypatch.setattr(compat, "installed_hermes_version", lambda env=None: "0.21.5")
    names = [c for _, c, _ in doctor.run_checks(team, {"HOME": str(tmp_path)})]
    assert names.index("hermes version") == names.index("hermes") + 1


def test_team_checks(team):
    now = time.time()
    assert doctor.check_team(Team(team.root.parent / "elsewhere"))[0][0] == "OK"
    state.update(team, "A", state="idle", pid=os.getpid(), heartbeat=now)
    state.update(team, "B", state="exited", pid=2**22 + 5, heartbeat=now - 999)
    state.update(team, "C", state="busy", pid=2**22 + 5, heartbeat=now - 999)
    res = {c: lv for lv, c, _ in doctor.check_team(team, now)}
    assert res == {".team writable": "OK", "agent A": "OK", "agent B": "OK", "agent C": "FAIL"}


def test_main_output_and_exit(team, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(compat, "installed_hermes_version", lambda env=None: None)  # never runs a real hermes
    home = tmp_path / "hh"
    _install(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    doctor.main(team=team)
    out = capsys.readouterr().out
    assert any(line.startswith(("OK  ", "WARN", "FAIL")) for line in out.splitlines())
    assert "plugin installed" in out
    assert "hermes version" in out
    # `hermes` is not on PATH in the test env -> at least that check fails -> exit 1
    monkeypatch.setenv("PATH", "/nonexistent")
    assert doctor.main(team=team) == 1
