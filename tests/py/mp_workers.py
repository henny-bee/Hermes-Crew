"""Top-level worker functions for multiprocessing concurrency tests (must be importable by spawn)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin" / "hermes-crew"))


def _team(root):
    from hermes_crew.team import Team
    return Team(root)


def append_events(root: str, who: int, n: int) -> None:
    from hermes_crew import events
    t = _team(root)
    for i in range(n):
        events.emit(t, f"R{who}", "usage", i=i, pad="x" * 3000)


def claim_mail(root: str, role: str, out: str, rounds: int = 50) -> None:
    import json
    import time
    from hermes_crew import mailbox
    t = _team(root)
    got = []
    for _ in range(rounds):
        got += [m["id"] for m in mailbox.claim_all(t, role)]
        time.sleep(0.001)
    Path(out).write_text(json.dumps(got))


def send_mail(root: str, who: int, n: int) -> None:
    from hermes_crew import mailbox
    t = _team(root)
    for i in range(n):
        mailbox.send(t, f"S{who}", "Eng", f"m{who}-{i}")


def add_tasks(root: str, who: int, n: int, out: str) -> None:
    import json
    from hermes_crew import tasks
    t = _team(root)
    ids = [tasks.add(t, f"R{who}", f"task {who}-{i}")["id"] for i in range(n)]
    Path(out).write_text(json.dumps(ids))


def claim_task(root: str, role: str, task_id: str, out: str) -> None:
    import json
    from hermes_crew import tasks
    t = _team(root)
    try:
        tasks.claim(t, role, task_id)
        ok = True
    except tasks.TaskError:
        ok = False
    Path(out).write_text(json.dumps(ok))


def board_own(root: str, role: str, rel: str, out: str) -> None:
    import json
    from hermes_crew import board
    t = _team(root)
    try:
        board.own(t, role, rel)
        ok = True
    except board.BoardError:
        ok = False
    Path(out).write_text(json.dumps(ok))


def board_decide(root: str, role: str, text: str, out: str) -> None:
    import json
    from hermes_crew import board
    t = _team(root)
    n = board.decide(t, role, text)
    Path(out).write_text(json.dumps(n))
