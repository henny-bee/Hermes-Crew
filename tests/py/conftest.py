"""Shared pytest setup: full isolation from the user's environment (no real ~/.hermes, no real tmux
server, no team env vars). hermes_crew is imported from the installed package (pip install -e '.[dev]')."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    sock = tmp_path_factory.mktemp("tmux")
    for v in (
        "TMUX",
        "TMUX_PANE",
        "HERMES_TEAM_DIR",
        "HERMES_TEAM_ROLE",
        "HERMES_CREW_SESSION",
        "HERMES_HOME",
        "HERMES_CREW_AUTO_OWN",
        "HERMES_CREW_LIB",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMUX_TMPDIR", str(sock))  # a private (empty) tmux server, never the user's
    yield


@pytest.fixture
def team(tmp_path):
    from hermes_crew.team import Team

    return Team(tmp_path / "proj").ensure()
