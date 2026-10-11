"""What a shell command line writes, moves or deletes. ``EXTRACTORS[name](argv, cwd)`` gets the
words of one command (wrappers like ``sudo``/``env``/``VAR=x`` stripped; ``argv[0]`` is the name) and
returns its targets; adding a command is adding one entry. ``cwd`` None = unknown (``cd $DIR``)."""

from __future__ import annotations

import glob as _glob
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .tokenize import SEPARATORS, WRITE_REDIRS, Word, tokenize


@dataclass(frozen=True)
class Target:
    path: str  # absolute, not normalised: `..` is resolved physically later (realpath)
    op: str  # write | delete | move
    recursive: bool = False  # also everything below path


def _abs(path: str, cwd: str) -> str:
    path = os.path.expanduser(path)  # no normpath: "<symlink>/.." must resolve physically, as realpath does
    return path if os.path.isabs(path) else os.path.join(cwd, path)


_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin", "doas", "env"}
_WRAPPER_OPTS_WITH_ARG = {"sudo": ("-u", "-g", "-C", "-p"), "doas": ("-u", "-g", "-C", "-p"), "nice": ("-n",)}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RECURSIVE = re.compile(r"(?s)^(-(?!-).*[rR]|--recursive$)")


def strip_wrappers(argv: list[Word]) -> list[Word]:
    while argv:
        head = argv[0].text
        if _ASSIGN.match(head):
            argv = argv[1:]
        elif head in _WRAPPERS:  # skip its options (env: also VAR=x)
            argv = argv[1:]
            while argv and (argv[0].text.startswith("-") or (head == "env" and _ASSIGN.match(argv[0].text))):
                argv = argv[2:] if argv[0].text in _WRAPPER_OPTS_WITH_ARG.get(head, ()) else argv[1:]
        else:
            break
    return argv


def operands(argv: list[Word], opts_with_arg: Iterable[str] = ()) -> list[Word]:
    """Non-option words after the command name (``--`` ends options)."""
    opts, out, k, end = set(opts_with_arg), [], 1, False
    while k < len(argv):
        w = argv[k]
        if not end and w.text == "--":
            end = True
        elif not end and w.text.startswith("-") and w.text != "-":
            k += w.text in opts
        else:
            out.append(w)
        k += 1
    return out


def expand(word: Word, cwd: str | None) -> list[str]:
    """Absolute paths a word denotes ([] when it cannot be resolved statically)."""
    if word.dynamic or word.op or not word.text or (cwd is None and not os.path.isabs(os.path.expanduser(word.text))):
        return []
    if not word.glob:
        return [_abs(word.text, cwd or "/")]
    try:
        return sorted(_glob.glob(_abs(word.text, cwd or "/")))[:500]
    except (OSError, ValueError, re.error):
        return []


def _paths(words: Iterable[Word], cwd: str | None, op: str = "write", recursive: bool = False) -> list[Target]:
    return [Target(p, op, recursive) for w in words for p in expand(w, cwd)]


def _in_place_files(argv: list[Word], script_opts: str, arg_opts: str, sed: bool) -> list[Word]:
    """Files of ``sed -i`` / ``perl -i`` ([] without -i); without -e/-f the first operand is the script."""
    in_place, script_given, files, k, end = False, False, [], 1, False
    while k < len(argv):
        t = argv[k].text
        if t == "--" and not end:
            end = True
            if not sed:
                files.extend(argv[k + 1 :])
                break
        elif sed and not end and t.startswith("--"):
            in_place = in_place or t.startswith("--in-place")
            script_given = script_given or t.startswith(("--expression", "--file"))
            k += t.startswith(("--expression", "--file", "--line-length")) and "=" not in t
        elif not end and t.startswith("-") and len(t) > 1 and (sed or not files):
            for j, ch in enumerate(t[1:], 1):
                if ch == "i":
                    in_place = True
                    break  # rest of the group is the backup suffix
                if ch in script_opts + arg_opts:
                    script_given = script_given or ch in script_opts
                    k += ch in script_opts + (arg_opts if sed else "") and j == len(t) - 1  # argument is next word
                    break
        else:
            files.append(argv[k])
        k += 1
    return (files if script_given else files[1:]) if in_place else []


def _dir_aware(dest: Word, sources: list[Word], cwd: str | None, force_dir: bool) -> list[Target]:
    """``cp``-style destination: into the directory when it is one, else the file itself."""
    out = []
    for d in expand(dest, cwd):
        if force_dir or dest.text.endswith("/") or os.path.isdir(d):
            out += [os.path.join(d, os.path.basename(p.rstrip("/"))) for s in sources for p in expand(s, cwd)]
        else:
            out.append(d)
    return [Target(p, "write") for p in out]


def _opt_values(argv: list[Word], sep: Iterable[str] = (), eq: Iterable[str] = ()) -> list[Word]:
    """Values of ``NAME VALUE`` options (names in ``sep``) and ``NAME=VALUE`` ones (``eq``), in order."""
    out = []
    for k, w in enumerate(argv[1:], 1):
        name, has_eq, value = w.text.partition("=")
        if has_eq and name in eq:
            out.append(Word(value, w.glob, w.dynamic))
        elif w.text in sep and k + 1 < len(argv):
            out.append(argv[k + 1])
    return out


def _mv_cp(argv: list[Word], cwd: str | None) -> list[Target]:
    tdirs = _opt_values(argv, ("-t",), ("--target-directory",))
    tdir = tdirs[-1] if tdirs else None
    ops = operands(argv, ("-t", "--target-directory", "-S", "--suffix"))
    if tdir is None and len(ops) < 2:
        return []
    sources, dest = ([o for o in ops if o is not tdir], tdir) if tdir is not None else (ops[:-1], ops[-1])
    out = _dir_aware(dest, sources, cwd, tdir is not None)
    return out + (_paths(sources, cwd, "move") if os.path.basename(argv[0].text) == "mv" else [])


_RSYNC_OPTS_WITH_ARG = (
    *("-e", "--rsh", "-f", "--filter", "-T", "--temp-dir", "--exclude", "--include", "--exclude-from"),
    *("--include-from", "--files-from", "--partial-dir", "--backup-dir", "--link-dest", "--compare-dest"),
    *("--copy-dest", "--log-file", "--out-format", "--chmod", "--port", "--bwlimit"),
)


def _rsync(argv: list[Word], cwd: str | None) -> list[Target]:
    ops = [w for w in operands(argv, _RSYNC_OPTS_WITH_ARG) if ":" not in w.text]
    if len(ops) < 2:
        return []  # remote side (host:path) or a listing only
    dest, out = ops[-1], []
    for src in ops[:-1]:
        for p in expand(src, cwd):
            if not os.path.isdir(p):
                out += _dir_aware(dest, [Word(p)], cwd, False)
            else:  # a directory: anything below dest ("src/" = its contents) or below dest/<name> ("src")
                into = "" if src.text.endswith("/") else os.path.basename(p.rstrip("/"))
                out += [Target(os.path.join(d, into) if into else d, "write", True) for d in expand(dest, cwd)]
    if any(w.text.startswith("--delete") for w in argv):  # removes files below the destination
        out += _paths(ops[-1:], cwd, "delete", True)
    if any(w.text == "--remove-source-files" for w in argv):
        out += _paths(ops[:-1], cwd, "move")
    return out


def _rm(argv: list[Word], cwd: str | None) -> list[Target]:
    return _paths(operands(argv), cwd, "delete", any(_RECURSIVE.match(w.text) for w in argv[1:]))


Extractor = Callable[[list[Word], str | None], list[Target]]
EXTRACTORS: dict[str, Extractor] = {
    "tee": lambda a, cwd: _paths(operands(a), cwd),
    "sed": lambda a, cwd: _paths(_in_place_files(a, "ef", "l", sed=True), cwd),
    "perl": lambda a, cwd: _paths(_in_place_files(a, "eE", "IMmx", sed=False), cwd),
    **dict.fromkeys(("rm", "unlink", "shred"), _rm),
    "truncate": lambda a, cwd: _paths(operands(a, ("-s", "--size", "-r", "--reference")), cwd),
    "dd": lambda a, cwd: _paths(_opt_values(a, eq=("of",)), cwd),
    "mv": _mv_cp,
    "cp": _mv_cp,
    "git": lambda a, cwd: EXTRACTORS[a[1].text](a[1:], cwd) if len(a) > 1 and a[1].text in ("mv", "rm") else [],
    "rsync": _rsync,
    # cmake --install DIR --prefix P: writes anything below P (without --prefix: the cached prefix)
    "cmake": lambda a, cwd: (
        _paths(_opt_values(a, ("--prefix",), ("--prefix",)), cwd, "write", True)
        if any(w.text == "--install" for w in a)
        else []
    ),
}


def command_targets(command: str, cwd: str | None) -> list[Target]:
    """Files a shell command line would clearly write, move or delete (best effort)."""
    targets: list[Target] = []
    seg: list[Word] = []
    for w in [*tokenize(command), Word("\n", op=True)]:
        if not (w.op and w.text in SEPARATORS):
            seg.append(w)
            continue
        argv: list[Word] = []
        k = 0
        while k < len(seg):
            s, nxt = seg[k], seg[k + 1] if k + 1 < len(seg) else None
            if s.op and s.text in WRITE_REDIRS and nxt is not None and not nxt.op:
                if not (s.text == ">&" and (nxt.text.isdigit() or nxt.text == "-")):  # >&2 is an fd
                    targets += _paths([nxt], cwd)
                k += 1
            elif s.op and s.text in ("<", "<<", "<<-", "<<<"):
                k += 1
            elif not s.op:
                argv.append(s)
            k += 1
        argv, seg = strip_wrappers(argv), []
        if argv and argv[0].text in ("cd", "pushd"):
            dest = argv[1] if len(argv) > 1 else Word("~")
            paths = expand(dest, cwd) if dest.text != "-" else []
            cwd = paths[0] if len(paths) == 1 and not dest.glob else None
        elif argv and (fn := EXTRACTORS.get(os.path.basename(argv[0].text))):
            targets += fn(argv, cwd)
    return targets
