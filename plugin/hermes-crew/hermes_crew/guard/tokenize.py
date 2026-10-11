"""POSIX-ish shell tokenizer for the guard. Hand-written, not ``shlex``: the guard needs what shlex
drops — whether an operator or glob was quoted (``grep ">" f``, ``rm '*.py'``), ``$(...)`` kept as
one word, ``$``/backtick expansions (unresolvable) and heredoc bodies (skipped)."""

from __future__ import annotations

from dataclasses import dataclass

OPS = ("&>>", ">>", ">|", "&>", ">&", "<<<", "<<-", "<<", "&&", "||", ";;", ";", "|", "&", "<", ">", "(", ")", "\n")
SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")", "\n"}
WRITE_REDIRS = {">", ">>", ">|", "&>", "&>>", ">&"}


@dataclass
class Word:
    text: str
    glob: bool = False  # contains unquoted glob characters
    dynamic: bool = False  # contains $..., `...` (cannot be resolved statically)
    op: bool = False


def _find(s: str, sub: str, start: int) -> int:
    j = s.find(sub, start)
    return len(s) if j < 0 else j


def tokenize(command: str) -> list[Word]:
    """Words and operators of a command line. Quoted operator characters stay literal."""
    out: list[Word] = []
    buf: list[str] = []
    cur, started, heredocs, i, n = Word(""), False, 0, 0, len(command)

    def flush() -> None:
        nonlocal buf, cur, started
        if started:
            cur.text = "".join(buf)
            out.append(cur)
        buf, cur, started = [], Word(""), False

    while i < n:
        c = command[i]
        if c == "'":
            j = _find(command, "'", i + 1)
            buf.append(command[i + 1 : j])
            started, i = True, j + 1
        elif c == '"':
            i, started = i + 1, True
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"\\$`\n':
                    i += 1
                elif command[i] in "$`":
                    cur.dynamic = True
                buf.append(command[i])
                i += 1
            i += 1
        elif c == "\\" and i + 1 < n:
            if command[i + 1] != "\n":
                buf.append(command[i + 1])
                started = True
            i += 2
        elif command.startswith("$(", i) or c == "`":
            j, depth = (i + 1, 1) if c == "$" else (_find(command, "`", i + 1), 0)
            while depth and j + 1 < n:  # the ")" closing "$(" (nested parens counted)
                j += 1
                depth += {"(": 1, ")": -1}.get(command[j], 0)
            j = n if depth else j
            buf.append(command[i : j + 1])
            started, cur.dynamic, i = True, True, j + 1
        elif c == "#" and not started:
            i = _find(command, "\n", i)
        elif c in " \t":
            flush()
            i += 1
        elif op := next((o for o in OPS if command.startswith(o, i)), None):
            # "2>" / "2>>": a pure-digit word glued to a redirection is an fd number, drop it
            if op[0] in "<>" and started and "".join(buf).isdigit() and not cur.glob:
                buf, cur, started = [], Word(""), False
            flush()
            out.append(Word(op, op=True))
            i += len(op)
            heredocs += op in ("<<", "<<-")
            if op == "\n" and heredocs:  # skip the bodies of the heredocs started on that line
                delims = [
                    (out[k + 1].text, w.text == "<<-")
                    for k, w in enumerate(out[:-1])
                    if w.op and w.text in ("<<", "<<-") and not out[k + 1].op
                ]
                for delim, strip_tabs in delims[-heredocs:]:
                    while i < n:
                        j = _find(command, "\n", i)
                        line, i = command[i:j], min(j + 1, n)
                        if (line.lstrip("\t") if strip_tabs else line) == delim:
                            break
                heredocs = 0
        else:
            cur.glob = cur.glob or c in "*?["
            cur.dynamic = cur.dynamic or c == "$"
            buf.append(c)
            started, i = True, i + 1
    flush()
    return out
