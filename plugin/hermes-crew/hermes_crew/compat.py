"""Which Hermes Agent versions hermes-crew is known to work with, and which one is installed.

`KNOWN` maps an inclusive version range `(lo, hi)` to `ok | untested | broken`; `hi` None means
open-ended. Add a range only after checking the hooks in docs/hermes-api-contract.md against that
Hermes release. `scripts/gen-compat-row.py` renders the README "Tested with" row from this table.

Stdlib only. Never imports Hermes: the version comes from its install stamp or `hermes --version`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping

Version = tuple[int, ...]

# Only versions really exercised are "ok"; versions in between stay untested (not listed).
#   0.19.0: tests/smoke/run.sh passed (PyPI hermes-agent==0.19.0: plugin load, idle state, mail delivery).
#   0.21.5: docs/hermes-api-contract.md "Verified against: Hermes Agent v0.21.5 (git 85db7c3a)", and the smoke test.
KNOWN: dict[tuple[str, str | None], str] = {
    ("0.19.0", "0.19.0"): "ok",
    ("0.21.5", "0.21.5"): "ok",
}

STATUSES = ("ok", "untested", "broken")
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(text: str | None) -> Version | None:
    """`"0.21.5"`, `"v0.21.5"`, `"0.21.5+8076.g85db7c3"` -> `(0, 21, 5)`; None if not a version.

    Anything after the numeric release (a local `+...` part, `rc1`, `.dev0`) is ignored."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    v = tuple(int(p) for p in m.group(1).split("."))
    while len(v) > 1 and v[-1] == 0:  # 0.22 == 0.22.0
        v = v[:-1]
    return v


def _in_range(v: Version, lo: str, hi: str | None) -> bool:
    lo_v, hi_v = parse_version(lo), parse_version(hi)
    return lo_v is not None and lo_v <= v and (hi is None or (hi_v is not None and v <= hi_v))


def status_for(version: str | None, known: Mapping[tuple[str, str | None], str] | None = None) -> str:
    """`ok | untested | broken` for a Hermes version. A matching broken range beats an ok one;
    a version that is missing, unparseable or in no range is `untested`."""
    v = parse_version(version)
    if v is None:
        return "untested"
    hits = {s for (lo, hi), s in (KNOWN if known is None else known).items() if _in_range(v, lo, hi)}
    return "broken" if "broken" in hits else "ok" if "ok" in hits else "untested"


def installed_hermes_version(env: Mapping[str, str] | None = None) -> str | None:
    """The installed Hermes release (e.g. `"0.21.5"`), or None when Hermes is absent or unreadable.

    1. `<HERMES_HOME>/hermes-agent/install-stamp.json` `baseVersion` (written by Hermes' installer
       and updater, no subprocess).
    2. `<hermes> --version` ("Hermes Agent v0.21.5+..."), where `<hermes>` is `$HERMES_TEAM_HERMES`
       or `hermes` on PATH, with a short timeout.

    Never raises."""
    from .doctor import hermes_home  # lazy: doctor imports this module

    env = os.environ if env is None else env
    try:
        stamp = json.loads((hermes_home(env) / "hermes-agent" / "install-stamp.json").read_text("utf-8-sig"))
        base = stamp.get("baseVersion") if isinstance(stamp, dict) else None
        if isinstance(base, str) and parse_version(base):
            return _release(base)
    except (OSError, ValueError):
        pass
    exe = env.get("HERMES_TEAM_HERMES") or shutil.which("hermes", path=env.get("PATH"))
    if not exe:
        return None
    try:
        p = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            env=dict(env),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    m = re.search(r"Hermes(?: Agent)?\s+v?(\d+(?:\.\d+)+)", p.stdout or "") if p.returncode == 0 else None
    return m.group(1) if m else None


def _release(text: str) -> str:
    """The leading numeric release of a version string: `"0.21.5+8076.g85db7c3"` -> `"0.21.5"`."""
    m = _VERSION_RE.match(text.strip())
    return m.group(1) if m else text
