from __future__ import annotations

import json
import shutil
import stat

import pytest
from hermes_crew import compat


def test_parse_version():
    assert compat.parse_version("0.21.5") == (0, 21, 5)
    assert compat.parse_version("v0.21.5") == (0, 21, 5)
    assert compat.parse_version(" 0.21.5+8076.g85db7c3 ") == (0, 21, 5)
    assert compat.parse_version("1.2.0rc1") == (1, 2)
    assert compat.parse_version("0.22") == compat.parse_version("0.22.0") == compat.parse_version("0.22.0.0")
    for bad in (None, "", "unknown", "git.85db7c3", "v", "x1.2"):
        assert compat.parse_version(bad) is None


def test_known_table_is_well_formed():
    assert compat.KNOWN, "at least the version the contract doc was verified against"
    assert compat.KNOWN[("0.21.5", "0.21.5")] == "ok"
    assert compat.KNOWN[("0.19.0", "0.19.0")] == "ok"  # smoke-tested
    assert compat.status_for("0.20.0") == "untested"  # in between: not extrapolated
    for (lo, hi), status in compat.KNOWN.items():
        assert status in compat.STATUSES
        assert compat.parse_version(lo) is not None
        if hi is not None:
            assert compat.parse_version(hi) is not None
            assert compat.parse_version(lo) <= compat.parse_version(hi)


def test_status_for_shipped_table():
    assert compat.status_for("0.21.5") == "ok"
    assert compat.status_for("v0.21.5+8076.g85db7c3") == "ok"
    assert compat.status_for("0.21.4") == "untested"
    assert compat.status_for("0.21.6") == "untested"
    for unknown in (None, "", "unknown", "garbage"):
        assert compat.status_for(unknown) == "untested"


def test_status_for_ranges():
    known = {
        ("0.20", "0.20.9"): "ok",  # inclusive on both ends, 0.20 == 0.20.0
        ("0.20.3", "0.20.3"): "broken",  # broken beats ok when ranges overlap
        ("0.30.0", None): "broken",  # open-ended
        ("0.25.0", "0.25.0"): "untested",
    }
    assert compat.status_for("0.20.0", known) == "ok"
    assert compat.status_for("0.20.9", known) == "ok"
    assert compat.status_for("0.20.10", known) == "untested"
    assert compat.status_for("0.19.99", known) == "untested"
    assert compat.status_for("0.20.3", known) == "broken"
    assert compat.status_for("0.25", known) == "untested"
    assert compat.status_for("0.30", known) == "broken"
    assert compat.status_for("7.0.0", known) == "broken"
    assert compat.status_for("0.29.99", known) == "untested"
    assert compat.status_for("0.21.5", {("bogus", None): "ok"}) == "untested"
    assert compat.status_for("0.21.5", {("0.1", "bogus"): "ok"}) == "untested"


def test_status_for_uses_module_table(monkeypatch):
    monkeypatch.setattr(compat, "KNOWN", {("9.0", None): "broken"})
    assert compat.status_for("9.1") == "broken"
    assert compat.status_for("0.21.5") == "untested"


def _env(tmp_path, **extra):
    """An env with an empty HERMES_HOME and a PATH that has no hermes on it."""
    (tmp_path / "bin").mkdir(exist_ok=True)
    return {"HOME": str(tmp_path / "home"), "PATH": str(tmp_path / "bin"), **extra}


def _fake_hermes(path, out="Hermes Agent v0.22.1+12.gabc1234 (2026.10.1) · upstream x\n", rc=0, sleep=0):
    nap = f"{shutil.which('sleep')} {sleep}\n" if sleep else ""  # absolute: PATH holds only tmp/bin
    path.write_text(f"#!/bin/sh\n{nap}printf '%s' '{out}'\nexit {rc}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _stamp(home, data):
    d = home / "hermes-agent"
    d.mkdir(parents=True)
    (d / "install-stamp.json").write_text(data if isinstance(data, str) else json.dumps(data))


def test_installed_version_from_stamp(tmp_path):
    env = _env(tmp_path)
    _stamp(tmp_path / "home" / ".hermes", {"baseVersion": "0.21.5", "displayVersion": "0.21.5+8076.g85db7c3"})
    assert compat.installed_hermes_version(env) == "0.21.5"
    # HERMES_HOME wins over HOME/.hermes, like doctor.hermes_home
    _stamp(tmp_path / "custom", {"baseVersion": "v0.23.0+4"})
    assert compat.installed_hermes_version({**env, "HERMES_HOME": str(tmp_path / "custom")}) == "0.23.0"


def test_installed_version_from_binary_on_path(tmp_path):
    env = _env(tmp_path)
    _fake_hermes(tmp_path / "bin" / "hermes")
    assert compat.installed_hermes_version(env) == "0.22.1"


def test_bad_stamp_falls_back_to_binary(tmp_path):
    env = _env(tmp_path)
    _fake_hermes(tmp_path / "bin" / "hermes")
    for data in ("{not json", "[1, 2]", {"baseVersion": "unknown"}, {"commit": "abc"}, "\xff\xfe"):
        home = tmp_path / "home" / ".hermes"
        if home.exists():
            (home / "hermes-agent" / "install-stamp.json").unlink()
            (home / "hermes-agent").rmdir()
        _stamp(home, data)
        assert compat.installed_hermes_version(env) == "0.22.1", data


def test_hermes_team_hermes_overrides_path(tmp_path):
    env = _env(tmp_path)
    _fake_hermes(tmp_path / "bin" / "hermes")
    other = _fake_hermes(tmp_path / "other-hermes", out="Hermes Agent v0.19.0\n")
    env["HERMES_TEAM_HERMES"] = str(other)
    assert compat.installed_hermes_version(env) == "0.19.0"


@pytest.mark.parametrize(
    "out,rc",
    [
        ("Hermes Agent v0.22.1\n", 1),  # non-zero exit
        ("something else entirely\n", 0),  # unrecognised output
        ("", 0),
    ],
)
def test_binary_failures_return_none(tmp_path, out, rc):
    env = _env(tmp_path)
    _fake_hermes(tmp_path / "bin" / "hermes", out=out, rc=rc)
    assert compat.installed_hermes_version(env) is None


def test_absent_and_broken_never_raise(tmp_path, monkeypatch):
    assert compat.installed_hermes_version(_env(tmp_path)) is None
    assert compat.installed_hermes_version(_env(tmp_path, HERMES_TEAM_HERMES=str(tmp_path / "missing"))) is None
    notexec = tmp_path / "notexec"
    notexec.write_text("x")
    assert compat.installed_hermes_version(_env(tmp_path, HERMES_TEAM_HERMES=str(notexec))) is None
    assert compat.installed_hermes_version(_env(tmp_path, HERMES_TEAM_HERMES="bad\0path")) is None
    # default env: conftest's isolated HOME, PATH without hermes
    monkeypatch.delenv("HERMES_TEAM_HERMES", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert compat.installed_hermes_version() is None


def test_slow_binary_times_out(tmp_path, monkeypatch):
    import subprocess

    real_run = subprocess.run

    def fast_timeout(*a, **kw):
        assert kw["timeout"] <= 10
        kw["timeout"] = 0.2
        return real_run(*a, **kw)

    monkeypatch.setattr(compat.subprocess, "run", fast_timeout)
    env = _env(tmp_path)
    _fake_hermes(tmp_path / "bin" / "hermes", sleep=5)
    assert compat.installed_hermes_version(env) is None
