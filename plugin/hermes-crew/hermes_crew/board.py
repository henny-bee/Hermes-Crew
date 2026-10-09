"""Team board: port of bin/hermes-team-board. Same `.team/board.log` format and output text.

board.log is TSV, one entry per line: <time> <role> <KIND> <fields...>
  DECIDE   <text> <replaces#|"">
  OWN      <relpath>
  TRANSFER <relpath> <new owner>
  RELEASE  <relpath>
  APPROVE  <relpath> <sha256[:12]> <what was checked>
The lock file is `.team/board.log.lock` (the one the bash script uses), so both can run side by side.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import events, store
from .team import Team, is_lead, same_role

MIN_EVIDENCE = 20


class BoardError(Exception):
    """A refused board operation; str() is the message for the agent."""


def log_path(team: Team) -> Path:
    return team.path("board.log")


def _lock(team: Team):
    team.ensure()
    return store.flock(team.path("board.log.lock"))


def clean(text: str) -> str:
    """Tabs/newlines -> spaces (a log field must stay on one line, in one column)."""
    return text.replace("\t", " ").replace("\n", " ")


def rel(team: Team, path: str | os.PathLike) -> str:
    """Path relative to the team dir, symlinks resolved, missing parts allowed (realpath -m)."""
    return os.path.relpath(os.path.realpath(path), team.root)


def inside(team: Team, path: str | os.PathLike) -> bool:
    """True if `path` is inside the team dir and not under .team/."""
    r = rel(team, path)
    return not (r == ".." or r.startswith("../") or r == ".team" or r.startswith(".team/") or r == ".")


def sha12(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# ---------------------------------------------------------------- read
def entries(team: Team) -> list[list[str]]:
    """board.log split into fields (missing log -> [])."""
    try:
        text = log_path(team).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return [line.split("\t") for line in text.splitlines() if line]


def _f(e: list[str], i: int) -> str:
    return e[i] if len(e) > i else ""


def owners(team: Team, ents: list[list[str]] | None = None) -> dict[str, str]:
    """Current owner of every owned file (OWN claims, TRANSFER hands over, RELEASE frees)."""
    o: dict[str, str] = {}
    for e in entries(team) if ents is None else ents:
        kind = _f(e, 2)
        if kind == "OWN":
            o[_f(e, 3)] = _f(e, 1)
        elif kind == "TRANSFER":
            o[_f(e, 3)] = _f(e, 4)
        elif kind == "RELEASE":
            o.pop(_f(e, 3), None)
    return o


def owner_of(team: Team, relpath: str) -> str | None:
    """Current owner of `relpath` (a path relative to the team dir), or None."""
    return owners(team).get(relpath) or None


def decisions(team: Team, ents: list[list[str]] | None = None) -> list[dict]:
    """All decisions: {n, time, role, text, replaces (int|None), replaced_by (int|None)}."""
    out: list[dict] = []
    for e in entries(team) if ents is None else ents:
        if _f(e, 2) != "DECIDE":
            continue
        rep = _f(e, 4)
        out.append({"n": len(out) + 1, "time": _f(e, 0), "role": _f(e, 1), "text": _f(e, 3),
                    "replaces": int(rep) if rep.isdigit() else None, "replaced_by": None})
    for d in out:
        r = d["replaces"]
        if r is not None and 1 <= r <= len(out):
            out[r - 1]["replaced_by"] = d["n"]
    return out


def current_decisions(team: Team) -> list[dict]:
    return [d for d in decisions(team) if d["replaced_by"] is None]


def approvals(team: Team, relpath: str, ents: list[list[str]] | None = None) -> list[dict]:
    """Latest approval per approver of `relpath`: {role, hash, evidence, time}."""
    a: dict[str, dict] = {}
    for e in entries(team) if ents is None else ents:
        if _f(e, 2) == "APPROVE" and _f(e, 3) == relpath:
            a[_f(e, 1)] = {"role": _f(e, 1), "hash": _f(e, 4), "evidence": _f(e, 5), "time": _f(e, 0)}
    return list(a.values())


def files(team: Team) -> list[dict]:
    """Every file ever owned or approved: {file, owner, hash, approvals[{role,hash,evidence,status}], ok}.
    status: OK | STALE | IGNORED (approver owns the file now)."""
    ents = entries(team)
    own = owners(team, ents)
    names = sorted({_f(e, 3) for e in ents if _f(e, 2) in ("OWN", "TRANSFER", "RELEASE", "APPROVE")})
    out = []
    for f in names:
        p = team.root / f
        cur = sha12(p) if p.is_file() else "missing"
        o = own.get(f) or None
        aps, ok = [], False
        for ap in approvals(team, f, ents):
            if o is not None and same_role(ap["role"], o):
                st = "IGNORED"
            elif ap["hash"] == cur:
                st, ok = "OK", True
            else:
                st = "STALE"
            aps.append({**ap, "status": st})
        out.append({"file": f, "owner": o, "hash": cur, "approvals": aps, "ok": ok})
    return out


# ---------------------------------------------------------------- write (lock held)
def _put(team: Team, role: str, *fields: str) -> None:
    store.append_line(log_path(team), "\t".join((store.now_iso(), role, *fields)))


def decide(team: Team, role: str, text: str, replaces: int | None = None) -> int:
    """Record a decision; returns its number. Number assigned under the lock."""
    with _lock(team):
        ds = decisions(team)
        n = len(ds) + 1
        if replaces is not None:
            if not (1 <= replaces < n):
                raise BoardError(f"REFUSED: --replaces {replaces}: no such decision (existing: 1..{n - 1})")
            gone = ds[replaces - 1]["replaced_by"]
            if gone:
                raise BoardError(f"REFUSED: decision #{replaces} was already replaced by #{gone}")
        _put(team, role, "DECIDE", clean(text), "" if replaces is None else str(replaces))
    events.emit(team, role, "board.decide", n=n, text=text[:200], replaces=replaces)
    return n


def own(team: Team, role: str, *relpaths: str) -> list[str]:
    """Claim files (paths relative to the team dir). All-or-nothing; raises BoardError if any
    is owned by someone else. Returns the claimed paths."""
    with _lock(team):
        o = owners(team)
        for r in relpaths:
            cur = o.get(r)
            if cur and not same_role(cur, role):
                raise BoardError(f"REFUSED: {r} is owned by {cur} - ask {cur} (or lead) to run: "
                                 f"hermes-team-board transfer {r} <Role>")
        for r in relpaths:
            _put(team, role, "OWN", r)
    for r in relpaths:
        events.emit(team, role, "board.own", file=r)
    return list(relpaths)


def transfer(team: Team, role: str, relpath: str, to: str) -> str:
    """Hand a file to another role (owner or lead). Returns the previous owner."""
    with _lock(team):
        o = owner_of(team, relpath)
        if not o:
            raise BoardError(f"REFUSED: {relpath} has no owner - claim it with: hermes-team-board own {relpath}")
        if not (same_role(o, role) or is_lead(role)):
            raise BoardError(f"REFUSED: {relpath} is owned by {o} - only {o} or lead can transfer it")
        _put(team, role, "TRANSFER", relpath, to)
    events.emit(team, role, "board.transfer", file=relpath, **{"from": o, "to": to})
    return o


def release(team: Team, role: str, relpath: str) -> str | None:
    """Give up ownership (owner or lead). Returns the previous owner (None: had none)."""
    with _lock(team):
        o = owner_of(team, relpath)
        if not o:
            return None
        if not (same_role(o, role) or is_lead(role)):
            raise BoardError(f"REFUSED: {relpath} is owned by {o} - only {o} or lead can release it")
        _put(team, role, "RELEASE", relpath)
    events.emit(team, role, "board.release", file=relpath, **{"from": o})
    return o


def approve(team: Team, role: str, relpath: str, digest: str, evidence: str) -> None:
    """Approve the content with hash `digest` (non-owners only)."""
    with _lock(team):
        o = owner_of(team, relpath)
        if o and same_role(o, role):
            raise BoardError(f"REFUSED: you own {relpath} - an approval must come from someone else")
        _put(team, role, "APPROVE", relpath, digest, clean(evidence))
    events.emit(team, role, "board.approve", file=relpath, hash=digest, evidence=evidence[:200])


# ---------------------------------------------------------------- status (text)
def pane_idle(pane: str, symbol: str | None = None) -> bool:
    """Port of bash pane_idle(): the Hermes classic REPL in `pane` sits at an EMPTY prompt."""
    from .team import tmux
    sym = symbol or os.environ.get("HERMES_TEAM_PROMPT_SYMBOL") or "❯"
    info = tmux("display", "-p", "-t", pane, "#{cursor_y} #{cursor_x} #{pane_in_mode}")
    if not info:
        return False
    try:
        y, x, mode = info.split()
    except ValueError:
        return False
    if mode != "0":
        return False
    line = tmux("capture-pane", "-p", "-t", pane, "-S", y, "-E", y)
    if line is None:
        return False
    line += " "
    m = re.match(r"^([A-Za-z0-9_.-]+ )?" + re.escape(sym) + " ", line)
    if not m or str(len(m.group(0))) != x:
        return False
    if "/steer" in line or "Processing command" in line:
        return False
    if re.match(r"^(\[[0-9]+\]|Allow|Deny)", line[len(m.group(0)):]):
        return False
    return True


# ---------------------------------------------------------------- CLI (same text as the bash script)
USAGE = """usage:
  hermes-team-board decide [--replaces <#>] "<decision>"   record (or replace) a shared decision
  hermes-team-board own <file>...                         claim files you author
  hermes-team-board transfer <file> <Role>                hand a file to another role (owner or lead)
  hermes-team-board release <file>                        give up ownership (owner or lead)
  hermes-team-board approve <file> "<what you checked>"   approve the CURRENT content of a file
  hermes-team-board status                                team, owners, decisions, approvals"""


def status_text(team: Team, pane: str | None = None) -> str:
    """The `status` output of the bash script."""
    from . import state
    from .team import tmux
    out = [f"== team ({team.root}) =="]
    win = tmux("display", "-p", "-t", pane, "#{session_name}:#{window_index}") if pane else None
    if win:
        panes = tmux("list-panes", "-t", win, "-F", "#{pane_id} #{@role} #{@model}") or ""
        for line in panes.splitlines():
            parts = line.split()
            pid_, r, m = (parts + ["", "", ""])[:3]
            rec = state.read(team, r) if r else None
            if rec and state.is_alive(rec):
                st = rec["state"]
            else:
                st = "idle" if pane_idle(pid_) else "busy"
            out.append(f"  {r or '?':<18} {st:<5} model={m or 'default'}")
    if not log_path(team).exists():
        out.append("(board is empty)")
        return "\n".join(out)
    ents = entries(team)
    out.append("== decisions (current; replaced ones hidden) ==")
    for d in decisions(team, ents):
        if d["replaced_by"] is None:
            out.append(f"  #{d['n']} [{d['time'][11:]} {d['role']}] {d['text']}")
    out.append("== files ==")
    for f in files(team):
        out.append(f"  {f['file']:<40} owner={f['owner'] or '(none)'}")
        for a in f["approvals"]:
            s = {"OK": "OK", "STALE": "STALE (file changed after approval)",
                 "IGNORED": "ignored (approver owns the file now)"}[a["status"]]
            out.append(f"      approved by {a['role']:<14} {s} - {a['evidence'][:90]}")
        if not f["owner"]:
            out.append("      !! no owner")
        if not f["ok"]:
            out.append("      !! no valid approval from a non-owner")
    return "\n".join(out)


def main(argv: list[str], team: Team | None = None, role: str | None = None) -> int:
    """`hermes-crew board ...` == `hermes-team-board ...`: same arguments, output and exit codes."""
    import sys
    from .team import current_role, my_pane
    pane = my_pane() if (team is None or role is None) else None
    team = team or Team.resolve()
    role = role or current_role(pane=pane)
    cmd = argv[0] if argv else ""
    if cmd != "status":
        team.ensure()

    def usage() -> int:
        print(USAGE, file=sys.stderr)
        return 2

    def refused(e: BoardError) -> int:
        print(str(e), file=sys.stderr)
        return 1

    args = argv[1:]
    try:
        if cmd == "decide":
            rep_s = ""
            if args[:1] == ["--replaces"]:
                if len(args) < 2:
                    return usage()
                rep_s = args[1][1:] if args[1].startswith("#") else args[1]
                args = args[2:]
            text = " ".join(args)
            if not text:
                return usage()
            rep = None
            if rep_s:
                if not rep_s.isdigit():
                    n = len(decisions(team)) + 1
                    raise BoardError(f"REFUSED: --replaces {rep_s}: no such decision (existing: 1..{n - 1})")
                rep = int(rep_s)
            n = decide(team, role, text, rep)
            print(f"decision #{n} recorded" + (f" (replaces #{rep})" if rep is not None else ""))
        elif cmd == "own":
            if not args:
                return usage()
            for r in own(team, role, *(rel(team, f) for f in args)):
                print(f"{role} owns {r}")
        elif cmd == "transfer":
            if len(args) < 2 or not args[0] or not ROLE_ARG.match(args[1]):
                return usage()
            r = rel(team, args[0])
            o = transfer(team, role, r, args[1])
            print(f"{r}: {o} -> {args[1]}")
        elif cmd == "release":
            if not args or not args[0]:
                return usage()
            r = rel(team, args[0])
            o = release(team, role, r)
            print(f"{r} has no owner" if o is None else f"{r} released (was {o})")
        elif cmd == "approve":
            f = args[0] if args else ""
            ev = clean(" ".join(args[1:]))
            if not f or not os.path.isfile(f):
                print('usage: hermes-team-board approve <existing file> "<what you checked>"', file=sys.stderr)
                return 2
            if len(ev) < MIN_EVIDENCE:
                print("REFUSED: describe what you actually checked (at least a sentence)", file=sys.stderr)
                return 1
            r, h = rel(team, f), sha12(f)
            approve(team, role, r, h, ev)
            print(f"approved {r} @ {h}")
        elif cmd == "status":
            print(status_text(team, pane if pane is not None else my_pane()))
        else:
            return usage()
    except BoardError as e:
        return refused(e)
    return 0


ROLE_ARG = re.compile(r"^[A-Za-z0-9_]+$")
