"""Ownership policy for Hermes' ``pre_tool_call`` hook (pure functions, stdlib only).

``evaluate()`` decides whether a tool call may mutate files that another role owns on the team
board. It never raises: on any internal error it allows the call (a raising/slow
``pre_tool_call`` callback would BLOCK the tool in Hermes).

Guarded Hermes tools (Hermes 0.21.5, tools/file_tools.py and tools/terminal_tool.py):
  * ``write_file``  args ``path``, ``content``
  * ``patch``       args ``path`` (replace mode) or ``mode="patch"`` + ``patch`` (V4A text with
                    ``*** Add File: p`` / ``*** Update File: p`` / ``*** Delete File: p`` /
                    ``*** Move to: p`` headers)
  * ``terminal``    args ``command`` (+ optional ``workdir``) — best-effort shell parsing.

Terminal parsing is deliberately best-effort and only BLOCKS when a target clearly resolves to a
file owned by another role. Recognised: ``>``/``>>``/``>|``/``&>``/``&>>`` redirections,
``tee [-a]``, ``sed -i``, ``perl -i``, ``mv`` (sources and destination), ``cp`` (destination),
``rm``/``unlink``/``shred`` (incl. ``-r`` on a directory holding owned files), ``truncate``,
``dd of=``, ``git mv``/``git rm``; wrappers ``sudo``/``env``/``command``/``exec``/``nohup``/
``time``/``nice``; ``cd DIR`` earlier in the same command line; unquoted globs.
NOT recognised (will not be blocked): writes from inside scripts or interpreters
(``python -c``, ``node -e``, ``make``, …), ``$(...)``/backtick sub-commands, words containing
``$VARIABLES``, ``xargs``/``find -exec``/``find -delete``, ``git checkout/restore/reset``,
``ln``, ``install``, editors, and ``execute_code``/``delegate_task`` tool calls.
"""
from __future__ import annotations

import glob as _glob
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

FILE_TOOLS = ("write_file", "patch")
TERMINAL_TOOLS = ("terminal",)
TEAM_SUBDIR = ".team"

_V4A_HEADER = re.compile(r"^\*\*\*\s*(Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", re.M)


@dataclass(frozen=True)
class Target:
    path: str            # absolute, normalised (not yet symlink-resolved)
    op: str              # write | delete | move
    recursive: bool = False


@dataclass
class Decision:
    action: str = "allow"                 # allow | block
    message: str = ""
    claim: list[str] = field(default_factory=list)   # team-relative paths to auto-own

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    def hook_result(self) -> dict | None:
        """The value to return from Hermes' pre_tool_call callback."""
        return {"action": "block", "message": self.message} if self.blocked else None


# --------------------------------------------------------------------------- paths

def team_relative(path: str, team_dir: str) -> str | None:
    """Team-relative path like ``realpath -m --relative-to`` (board.log keys), or None when the
    path is outside the team dir, is the team dir itself, or lies under ``.team/``."""
    root = os.path.realpath(team_dir)
    real = os.path.realpath(path)
    if real == root or not real.startswith(root.rstrip(os.sep) + os.sep):
        return None
    rel = os.path.relpath(real, root)
    if rel == TEAM_SUBDIR or rel.startswith(TEAM_SUBDIR + os.sep):
        return None
    return rel.replace(os.sep, "/")


def _abs(path: str, cwd: str) -> str:
    path = os.path.expanduser(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def _same_role(a: str | None, b: str | None) -> bool:
    return (a or "").lower() == (b or "").lower()


def block_message(file: str, owner: str) -> str:
    return (f"hermes-crew: {file} is owned by {owner}. Send the owner the exact change: "
            f'hermes-crew send {owner} "..." (or ask lead to transfer it).')


# --------------------------------------------------------------------------- file tools

def file_tool_targets(tool_name: str, args: Mapping, cwd: str) -> list[Target]:
    """Targets of the structured file-mutating tools (write_file, patch)."""
    if not isinstance(args, Mapping):
        return []
    if tool_name == "write_file":
        p = args.get("path")
        return [Target(_abs(p, cwd), "write")] if isinstance(p, str) and p.strip() else []
    if tool_name != "patch":
        return []
    patch_text = args.get("patch")
    if args.get("mode") == "patch" or (isinstance(patch_text, str) and not args.get("path")):
        out = []
        for kind, p in _V4A_HEADER.findall(patch_text or ""):
            op = {"Delete File": "delete", "Move to": "write"}.get(kind, "write")
            out.append(Target(_abs(p, cwd), op))
        return out
    p = args.get("path")
    return [Target(_abs(p, cwd), "write")] if isinstance(p, str) and p.strip() else []


# --------------------------------------------------------------------------- shell tokenizer

_OPS = ("&>>", ">>", ">|", "&>", ">&", "<<<", "<<-", "<<", "&&", "||", ";;", ";", "|", "&",
        "<", ">", "(", ")", "\n")
_SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")", "\n"}
_WRITE_REDIRS = {">", ">>", ">|", "&>", "&>>", ">&"}


@dataclass
class Word:
    text: str
    glob: bool = False      # contains unquoted glob characters
    dynamic: bool = False   # contains $..., `...` (cannot be resolved statically)
    op: bool = False


def tokenize(command: str) -> list[Word]:
    """Small POSIX-ish shell tokenizer: quotes, escapes, operators, heredoc bodies skipped.
    Quoted operator characters stay literal (``grep ">" f`` is not a redirection)."""
    out: list[Word] = []
    i, n = 0, len(command)
    buf: list[str] = []
    cur = Word("")
    started = False
    pending_heredocs: list[tuple[str, bool]] = []

    def flush() -> None:
        nonlocal buf, cur, started
        if started:
            cur.text = "".join(buf)
            out.append(cur)
        buf, cur, started = [], Word(""), False

    while i < n:
        c = command[i]
        if c == "'":
            j = command.find("'", i + 1)
            j = n if j < 0 else j
            buf.append(command[i + 1:j]); started = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            started = True
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"\\$`\n':
                    buf.append(command[i + 1]); i += 2
                    continue
                if command[i] in "$`":
                    cur.dynamic = True
                buf.append(command[i]); i += 1
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if command[i + 1] != "\n":
                buf.append(command[i + 1]); started = True
            i += 2
            continue
        if c == "$" and command.startswith("$(", i):
            depth, j = 0, i
            while j < n:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            buf.append(command[i:j + 1]); started = True; cur.dynamic = True
            i = j + 1
            continue
        if c == "`":
            j = command.find("`", i + 1)
            j = n if j < 0 else j
            buf.append(command[i:j + 1]); started = True; cur.dynamic = True
            i = j + 1
            continue
        if c == "#" and not started:
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in " \t":
            flush(); i += 1
            continue
        op = next((o for o in _OPS if command.startswith(o, i)), None)
        if op:
            # "2>" / "2>>": a pure-digit word glued to a redirection is an fd number, drop it
            if op[0] in "<>" and started and "".join(buf).isdigit() and not cur.glob:
                buf, cur, started = [], Word(""), False
            flush()
            out.append(Word(op, op=True))
            i += len(op)
            if op in ("<<", "<<-"):
                pending_heredocs.append(("", op == "<<-"))
            if op == "\n" and pending_heredocs:
                i = _skip_heredocs(command, i, out, pending_heredocs)
            continue
        if c in "*?[":
            cur.glob = True
        if c == "$":
            cur.dynamic = True
        buf.append(c); started = True
        i += 1
    flush()
    return out


def _skip_heredocs(command: str, i: int, out: list[Word], pending: list) -> int:
    """At the start of the line after a ``<<DELIM`` command: skip each heredoc body."""
    delims = []
    for k, w in enumerate(out):
        if w.op and w.text in ("<<", "<<-") and k + 1 < len(out) and not out[k + 1].op:
            delims.append((out[k + 1].text, w.text == "<<-"))
    delims = delims[-len(pending):] if pending else []
    pending.clear()
    for delim, strip_tabs in delims:
        while i < len(command):
            j = command.find("\n", i)
            line = command[i:] if j < 0 else command[i:j]
            i = len(command) if j < 0 else j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
    return i


# --------------------------------------------------------------------------- shell analysis

_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin", "doas"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_wrappers(argv: list[Word]) -> list[Word]:
    while argv:
        head = argv[0].text
        if _ASSIGN.match(head):
            argv = argv[1:]
        elif head in _WRAPPERS:
            argv = argv[1:]
            while argv and argv[0].text.startswith("-"):
                takes_arg = head in ("sudo", "doas") and argv[0].text in ("-u", "-g", "-C", "-p")
                takes_arg = takes_arg or (head == "nice" and argv[0].text == "-n")
                argv = argv[2:] if takes_arg else argv[1:]
        elif head == "env":
            argv = argv[1:]
            while argv and (argv[0].text.startswith("-") or _ASSIGN.match(argv[0].text)):
                argv = argv[1:]
        else:
            break
    return argv


def _operands(argv: list[Word], opts_with_arg: Iterable[str] = ()) -> list[Word]:
    """Non-option words after the command name (``--`` ends options)."""
    opts_with_arg = set(opts_with_arg)
    out, k, end = [], 1, False
    while k < len(argv):
        w = argv[k]
        if not end and w.text == "--":
            end = True
        elif not end and w.text.startswith("-") and w.text != "-":
            if w.text in opts_with_arg:
                k += 1
        else:
            out.append(w)
        k += 1
    return out


def _expand(word: Word, cwd: str | None) -> list[str]:
    """Absolute paths a word denotes ([] when it cannot be resolved statically)."""
    if word.dynamic or word.op or not word.text or cwd is None and not os.path.isabs(
            os.path.expanduser(word.text)):
        return []
    base = cwd or "/"
    if word.glob:
        try:
            pattern = _abs(word.text, base)
            return sorted(_glob.glob(pattern))[:500]
        except (OSError, ValueError, re.error):
            return []
    return [_abs(word.text, base)]


def _sed_files(argv: list[Word]) -> list[Word]:
    in_place, script_given, files, k, end = False, False, [], 1, False
    while k < len(argv):
        t = argv[k].text
        if not end and t == "--":
            end = True
        elif not end and t.startswith("--"):
            if t.startswith("--in-place"):
                in_place = True
            elif t.startswith(("--expression", "--file")):
                script_given = True
                k += 0 if "=" in t else 1
            elif t in ("--line-length",):
                k += 1
        elif not end and t.startswith("-") and len(t) > 1:
            for j, ch in enumerate(t[1:], 1):
                if ch == "i":
                    in_place = True
                    break           # rest of the group is the backup suffix
                if ch in "efl":
                    script_given = script_given or ch in "ef"
                    if j == len(t) - 1:
                        k += 1      # argument is the next word
                    break
        else:
            files.append(argv[k])
        k += 1
    if not in_place:
        return []
    return files if script_given else files[1:]


def _perl_files(argv: list[Word]) -> list[Word]:
    in_place, script_given, files, k = False, False, [], 1
    while k < len(argv):
        t = argv[k].text
        if t == "--":
            files.extend(argv[k + 1:])
            break
        if t.startswith("-") and len(t) > 1 and not files:
            body = t[1:]
            for j, ch in enumerate(body):
                if ch in "eE":
                    script_given = True
                    if j == len(body) - 1:
                        k += 1
                    break
                if ch == "i":
                    in_place = True
                    break           # rest is the backup suffix
                if ch in "IMmx":
                    break           # option with glued argument
        else:
            files.append(argv[k])
        k += 1
    if not in_place:
        return []
    return files if script_given else files[1:]


def _dir_aware(dest_words: list[Word], sources: list[Word], cwd: str | None,
               force_dir: bool = False) -> list[str]:
    out = []
    for dest in dest_words:
        for d in _expand(dest, cwd):
            if force_dir or dest.text.endswith("/") or os.path.isdir(d):
                for s in sources:
                    out.extend(os.path.join(d, os.path.basename(p.rstrip("/"))) for p in _expand(s, cwd))
            else:
                out.append(d)
    return out


def _command_targets(argv: list[Word], cwd: str | None) -> list[Target]:
    argv = _strip_wrappers(argv)
    if not argv:
        return []
    name = os.path.basename(argv[0].text)
    if name == "git" and len(argv) > 1 and argv[1].text in ("mv", "rm"):
        argv = argv[1:]
        name = argv[0].text
    if name == "tee":
        ops = _operands(argv)
        return [Target(p, "write") for w in ops for p in _expand(w, cwd)]
    if name == "sed":
        return [Target(p, "write") for w in _sed_files(argv) for p in _expand(w, cwd)]
    if name == "perl":
        return [Target(p, "write") for w in _perl_files(argv) for p in _expand(w, cwd)]
    if name in ("rm", "unlink", "shred"):
        rec = any(w.text.startswith("-") and not w.text.startswith("--") and ("r" in w.text or "R" in w.text)
                  or w.text in ("--recursive",) for w in argv[1:])
        return [Target(p, "delete", rec) for w in _operands(argv) for p in _expand(w, cwd)]
    if name == "truncate":
        ops = _operands(argv, ("-s", "--size", "-r", "--reference"))
        return [Target(p, "write") for w in ops for p in _expand(w, cwd)]
    if name == "dd":
        out = []
        for w in argv[1:]:
            if w.text.startswith("of="):
                out.extend(Target(p, "write") for p in _expand(Word(w.text[3:], w.glob, w.dynamic), cwd))
        return out
    if name in ("mv", "cp"):
        tdir = None
        for k, w in enumerate(argv[1:], 1):
            if w.text in ("-t",) and k + 1 < len(argv):
                tdir = argv[k + 1]
            elif w.text.startswith("--target-directory="):
                tdir = Word(w.text.split("=", 1)[1], w.glob, w.dynamic)
        ops = _operands(argv, ("-t", "--target-directory", "-S", "--suffix"))
        if tdir is not None:
            sources, dests, force = [o for o in ops if o is not tdir], [tdir], True
        else:
            if len(ops) < 2:
                return []
            sources, dests, force = ops[:-1], ops[-1:], False
        out = [Target(p, "write") for p in _dir_aware(dests, sources, cwd, force)]
        if name == "mv":
            out.extend(Target(p, "move") for s in sources for p in _expand(s, cwd))
        return out
    return []


def command_targets(command: str, cwd: str | None) -> list[Target]:
    """Files a shell command line would clearly write, move or delete (best effort)."""
    words = tokenize(command)
    targets: list[Target] = []
    seg: list[Word] = []

    def finish(seg: list[Word]) -> None:
        nonlocal cwd
        argv: list[Word] = []
        k = 0
        while k < len(seg):
            w = seg[k]
            if w.op and w.text in _WRITE_REDIRS:
                nxt = seg[k + 1] if k + 1 < len(seg) else None
                if nxt is not None and not nxt.op:
                    is_fd = w.text == ">&" and (nxt.text.isdigit() or nxt.text == "-")
                    if not is_fd:
                        targets.extend(Target(p, "write") for p in _expand(nxt, cwd))
                    k += 2
                    continue
            elif w.op and w.text in ("<", "<<", "<<-", "<<<"):
                k += 2
                continue
            elif not w.op:
                argv.append(w)
            k += 1
        stripped = _strip_wrappers(argv)
        if stripped and stripped[0].text in ("cd", "pushd"):
            dest = stripped[1] if len(stripped) > 1 else Word("~")
            paths = _expand(dest, cwd) if dest.text != "-" else []
            cwd = paths[0] if len(paths) == 1 and not dest.glob else None
            return
        targets.extend(_command_targets(argv, cwd))

    for w in words:
        if w.op and w.text in _SEPARATORS:
            finish(seg)
            seg = []
        else:
            seg.append(w)
    finish(seg)
    return targets


# --------------------------------------------------------------------------- policy

def owner_conflicts(targets: Iterable[Target], team_dir: str, role: str,
                    owners: Mapping[str, str]) -> list[tuple[str, str]]:
    """(team-relative file, owner) pairs for targets owned by a role other than ``role``."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for t in targets:
        rel = team_relative(t.path, team_dir)
        if rel is None:
            continue
        hits = [rel] if rel in owners else []
        if t.op in ("delete", "move") and (t.recursive or t.op == "move"):
            prefix = rel.rstrip("/") + "/"
            hits += [f for f in owners if f.startswith(prefix)]
        for f in hits:
            owner = owners.get(f)
            if owner and not _same_role(owner, role) and f not in seen:
                seen.add(f)
                out.append((f, owner))
    return out


def evaluate(tool_name: str, args: Mapping | None, *, team_dir: str, role: str,
             owners: Mapping[str, str], cwd: str | None = None, auto_own: bool = True) -> Decision:
    """Policy decision for one tool call. Never raises (errors → allow)."""
    try:
        return _evaluate(tool_name, args or {}, team_dir, role, owners, cwd, auto_own)
    except Exception:  # noqa: BLE001 - the guard must never break a tool call
        return Decision()


def _evaluate(tool_name: str, args: Mapping, team_dir: str, role: str, owners: Mapping[str, str],
              cwd: str | None, auto_own: bool) -> Decision:
    if not isinstance(args, Mapping):
        return Decision()
    workdir = args.get("workdir") if isinstance(args.get("workdir"), str) else None
    base = workdir or cwd or team_dir
    if tool_name in FILE_TOOLS:
        targets = file_tool_targets(tool_name, args, base)
    elif tool_name in TERMINAL_TOOLS:
        command = args.get("command")
        if not isinstance(command, str) or not owners:
            return Decision()
        targets = command_targets(command, base)
    else:
        return Decision()
    conflicts = owner_conflicts(targets, team_dir, role, owners)
    if conflicts:
        file, owner = conflicts[0]
        msg = block_message(file, owner)
        if len(conflicts) > 1:
            msg += " Also owned by others: " + ", ".join(f"{f} ({o})" for f, o in conflicts[1:]) + "."
        return Decision("block", msg)
    claim: list[str] = []
    if auto_own and tool_name in FILE_TOOLS:
        for t in targets:
            rel = team_relative(t.path, team_dir)
            if t.op == "write" and rel is not None and rel not in owners and rel not in claim:
                claim.append(rel)
    return Decision("allow", claim=claim)
