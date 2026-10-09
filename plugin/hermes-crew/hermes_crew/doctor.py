"""`hermes-crew doctor`: environment checks. Prints `OK|WARN|FAIL  <check>  <detail>`."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, state
from .team import Team

SKILLS = ("hermes-tmux-team", "hermes-tmux-teammate")


def hermes_home(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("HERMES_HOME") or Path(env.get("HOME") or Path.home()) / ".hermes")


def _run(*argv: str) -> str | None:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout or p.stderr).strip() if p.returncode == 0 else None


def check_python() -> tuple[str, str, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    return ("OK" if ok else "FAIL", "python", f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need >= 3.10)"))


def check_tmux() -> tuple[str, str, str]:
    if not shutil.which("tmux"):
        return ("FAIL", "tmux", "not installed (sudo apt install tmux)")
    out = _run("tmux", "-V") or ""
    m = re.search(r"(\d+)\.(\d+)", out)
    if not m:
        return ("WARN", "tmux", f"cannot read version: {out!r}")
    ver = (int(m.group(1)), int(m.group(2)))
    if ver < (3, 0):
        return ("FAIL", "tmux", f"{out} (need >= 3.0)")
    return ("OK", "tmux", out)


def check_hermes() -> tuple[str, str, str]:
    p = shutil.which("hermes")
    return ("OK", "hermes", p) if p else ("FAIL", "hermes", "not found on PATH (install Hermes Agent, or add ~/.local/bin to PATH)")


def check_plugin(home: Path) -> tuple[str, str, str]:
    d = home / "plugins" / "hermes-crew"
    init = d / "hermes_crew" / "__init__.py"
    if not (d / "plugin.yaml").exists() and not init.exists():
        return ("FAIL", "plugin installed", f"missing {d} (run install.sh)")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', init.read_text(errors="replace")) if init.exists() else None
    if not m:
        return ("FAIL", "plugin installed", f"{init} has no __version__")
    if m.group(1) != __version__:
        return ("FAIL", "plugin installed", f"version {m.group(1)} != hermes-crew {__version__} (re-run install.sh)")
    return ("OK", "plugin installed", f"{d} ({m.group(1)})")


def enabled_plugins(config_text: str) -> list[str]:
    """`plugins.enabled` from config.yaml text, without a yaml library (block or flow list)."""
    out: list[str] = []
    lines = config_text.splitlines()
    in_plugins = False
    plugins_indent = 0
    i = 0
    while i < len(lines):
        line = _nocomment(lines[i])
        i += 1
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"^plugins\s*:\s*$", line.strip()) and indent == 0:
            in_plugins, plugins_indent = True, indent
            continue
        if in_plugins and indent <= plugins_indent:
            in_plugins = False
        if not in_plugins:
            continue
        m = re.match(r"^\s*enabled\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            return [_unq(x) for x in rest.strip("[]").split(",") if x.strip()]
        en_indent = indent
        while i < len(lines):
            nxt = _nocomment(lines[i])
            if not nxt.strip():
                i += 1
                continue
            ni = len(nxt) - len(nxt.lstrip())
            item = re.match(r"^\s*-\s*(.+)$", nxt)
            if not item or ni < en_indent:
                break
            out.append(_unq(item.group(1)))
            i += 1
        return out
    return out


def _nocomment(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line).rstrip()


def _unq(s: str) -> str:
    return s.strip().strip("'\"")


def check_enabled(home: Path) -> tuple[str, str, str]:
    cfg = home / "config.yaml"
    if not cfg.exists():
        return ("FAIL", "plugin enabled", f"{cfg} missing (run hermes once, then: hermes plugins enable hermes-crew)")
    names = enabled_plugins(cfg.read_text(errors="replace"))
    if "hermes-crew" in names:
        return ("OK", "plugin enabled", "plugins.enabled contains hermes-crew")
    return ("FAIL", "plugin enabled", "not in plugins.enabled - run: hermes plugins enable hermes-crew")


def check_skills(home: Path) -> list[tuple[str, str, str]]:
    root = home / "skills"
    res = []
    for s in SKILLS:
        hits = sorted([*root.glob(f"{s}/SKILL.md"), *root.glob(f"*/{s}/SKILL.md")]) if root.is_dir() else []
        res.append(("OK", f"skill {s}", str(hits[0].parent)) if hits
                   else ("FAIL", f"skill {s}", f"not found under {root} (run install.sh)"))
    return res


def check_team(team: Team, now: float | None = None) -> list[tuple[str, str, str]]:
    if not team.exists():
        return [("OK", "team", f"not inside a team ({team.root} has no .team/) - team checks skipped")]
    res = []
    try:
        fd, tmp = tempfile.mkstemp(dir=team.dir, prefix=".doctor-")
        os.close(fd)
        os.unlink(tmp)
        res.append(("OK", ".team writable", str(team.dir)))
    except OSError as e:
        res.append(("FAIL", ".team writable", f"{team.dir}: {e}"))
    for a in state.all_agents(team):
        r = a["role"]
        if a["state"] == "exited":
            res.append(("OK", f"agent {r}", "exited cleanly"))
        elif state.is_alive(a, now):
            res.append(("OK", f"agent {r}", f"alive, {a['state']}"))
        else:
            res.append(("FAIL", f"agent {r}", f"not alive (pid {a.get('pid')}, last state {a['state']}) - "
                        f"restart it or tell lead"))
    return res


def run_checks(team: Team | None = None, env=None) -> list[tuple[str, str, str]]:
    home = hermes_home(env)
    res = [check_python(), check_tmux(), check_hermes(), check_plugin(home), check_enabled(home)]
    res += check_skills(home)
    res += check_team(team or Team.resolve())
    return res


def main(json_out: bool = False, team: Team | None = None) -> int:
    res = run_checks(team)
    if json_out:
        print(json.dumps([{"level": lv, "check": c, "detail": d} for lv, c, d in res], indent=1))
    else:
        w = max(len(c) for _, c, _ in res)
        for lv, c, d in res:
            print(f"{lv:<4}  {c:<{w}}  {d}")
        fails = sum(lv == "FAIL" for lv, _, _ in res)
        print(f"\n{fails} FAIL" if fails else "\nall good")
    return 1 if any(lv == "FAIL" for lv, _, _ in res) else 0
