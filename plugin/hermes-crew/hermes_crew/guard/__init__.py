"""Ownership policy for Hermes' ``pre_tool_call`` hook (pure functions, stdlib only).

``evaluate()`` decides whether a tool call may mutate files another role owns on the team board.
It never raises: on any internal error it allows the call (a raising ``pre_tool_call`` callback
would BLOCK the tool). Guarded tools (Hermes 0.21.5): ``write_file`` (path), ``patch`` (path, or
V4A text with ``*** Add File|Update File|Delete File|Move to:`` headers) and ``terminal``
(command, workdir), parsed best-effort by ``tokenize.py`` and ``commands.EXTRACTORS``. Terminal
calls are only blocked when a target clearly resolves to a file owned by another role; writes from
interpreters, ``$(...)``, ``$VARS``, ``xargs``/``find -exec`` etc. are not recognised (allowed).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .commands import EXTRACTORS, Target, _abs, command_targets
from .tokenize import Word, tokenize

__all__ = ["EXTRACTORS", "Decision", "Target", "Word", "command_targets", "evaluate", "team_relative", "tokenize"]

FILE_TOOLS = ("write_file", "patch")
TERMINAL_TOOLS = ("terminal",)
TEAM_SUBDIR = ".team"
_V4A_HEADER = re.compile(r"^\*\*\*\s*(Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", re.M)


@dataclass
class Decision:
    action: str = "allow"  # allow | block
    message: str = ""
    claim: list[str] = field(default_factory=list)  # team-relative paths to auto-own

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    def hook_result(self) -> dict[str, Any] | None:
        """The value to return from Hermes' pre_tool_call callback."""
        return {"action": "block", "message": self.message} if self.blocked else None


def team_relative(path: str, team_dir: str) -> str | None:
    """Team-relative path like ``realpath -m --relative-to`` (board.log keys), or None when the
    path is outside the team dir, is the team dir itself, or lies under ``.team/``."""
    root, real = os.path.realpath(team_dir), os.path.realpath(path)
    if real == root or not real.startswith(root.rstrip(os.sep) + os.sep):
        return None
    rel = os.path.relpath(real, root)
    return None if rel == TEAM_SUBDIR or rel.startswith(TEAM_SUBDIR + os.sep) else rel.replace(os.sep, "/")


def block_message(file: str, owner: str) -> str:
    return (
        f"hermes-crew: {file} is owned by {owner}. Send the owner the exact change: "
        f'hermes-crew send {owner} "..." (or ask lead to transfer it).'
    )


def file_tool_targets(tool_name: str, args: Mapping[str, Any], cwd: str) -> list[Target]:
    """Targets of the structured file-mutating tools (write_file, patch)."""
    if not isinstance(args, Mapping) or tool_name not in FILE_TOOLS:
        return []
    text = args.get("patch")
    if tool_name == "patch" and (args.get("mode") == "patch" or (isinstance(text, str) and not args.get("path"))):
        return [
            Target(_abs(p, cwd), "delete" if k == "Delete File" else "write")
            for k, p in _V4A_HEADER.findall(text or "")
        ]
    p = args.get("path")
    return [Target(_abs(p, cwd), "write")] if isinstance(p, str) and p.strip() else []


def owner_conflicts(
    targets: Iterable[Target], team_dir: str, role: str, owners: Mapping[str, str]
) -> list[tuple[str, str]]:
    """(team-relative file, owner) pairs for targets owned by a role other than ``role``."""
    out: dict[str, str] = {}
    for t in targets:
        rel = team_relative(t.path, team_dir)
        hits = [] if rel is None else [rel] if rel in owners else []
        if rel is not None and (t.recursive or t.op == "move"):  # a directory: everything owned below it
            hits += [f for f in owners if f.startswith(rel.rstrip("/") + "/")]
        out.update((f, owners[f]) for f in hits if owners[f] and owners[f].lower() != (role or "").lower())
    return list(out.items())


def evaluate(
    tool_name: str,
    args: Mapping[str, Any] | None,
    *,
    team_dir: str,
    role: str,
    owners: Mapping[str, str],
    cwd: str | None = None,
    auto_own: bool = True,
) -> Decision:
    """Policy decision for one tool call. Never raises (errors → allow)."""
    try:
        args = args or {}
        if not isinstance(args, Mapping):
            return Decision()
        workdir, command = args.get("workdir"), args.get("command")
        base = (workdir if isinstance(workdir, str) else None) or cwd or team_dir
        if tool_name in FILE_TOOLS:
            targets = file_tool_targets(tool_name, args, base)
        elif tool_name in TERMINAL_TOOLS and isinstance(command, str) and owners:
            targets = command_targets(command, base)
        else:
            return Decision()
        conflicts = owner_conflicts(targets, team_dir, role, owners)
        if conflicts:
            msg, others = block_message(*conflicts[0]), ", ".join(f"{f} ({o})" for f, o in conflicts[1:])
            return Decision("block", msg + (f" Also owned by others: {others}." if others else ""))
        if not (auto_own and tool_name in FILE_TOOLS):
            return Decision()
        rels = [team_relative(t.path, team_dir) for t in targets if t.op == "write"]
        return Decision("allow", claim=[r for r in dict.fromkeys(rels) if r is not None and r not in owners])
    except Exception:
        return Decision()
