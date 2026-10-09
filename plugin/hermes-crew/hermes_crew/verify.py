"""`hermes-crew verify <task> -- <cmd>`: the tool runs the check and records the evidence."""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import threading
import time

from . import events, store
from .team import Team

TAIL_BYTES = 2048


def run(team: Team, by: str, task_id: str, cmd: list[str], cwd: str | None = None,
        timeout: float | None = None, echo=None) -> dict:
    """Run `cmd` (argv; a single string is run by the shell), capture stdout+stderr combined,
    append a record to verify.jsonl and emit a `verify` event. Returns the record.
    `echo(bytes)` is called with output chunks as they arrive (to show it live). exit 124 = timeout."""
    cwd = cwd or os.getcwd()
    shell = len(cmd) == 1 and any(c in cmd[0] for c in " |&;<>()$`*?~")
    argv = cmd[0] if shell else cmd
    h = hashlib.sha256()
    buf = bytearray()
    t0 = time.monotonic()
    timed_out = False
    try:
        p = subprocess.Popen(argv, shell=shell, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        code, out = 127, f"cannot run {cmd[0]!r}: {e}\n".encode()
        h.update(out)
        buf += out
        if echo:
            echo(out)
    else:
        def pump() -> None:
            assert p.stdout is not None
            for chunk in iter(lambda: p.stdout.read1(65536), b""):
                h.update(chunk)
                buf.extend(chunk)
                del buf[:-TAIL_BYTES]
                if echo:
                    echo(chunk)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            code = p.wait(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(p)
            code = p.wait()
        reader.join(5)
        if timed_out:
            msg = f"\n[hermes-crew verify: timed out after {timeout:g} s, killed]\n".encode()
            buf += msg
            if echo:
                echo(msg)
            code = 124
        elif code < 0:
            code = 128 - code   # killed by signal N -> 128+N, like a shell
    tail = bytes(buf[-TAIL_BYTES:])
    ts = time.time()
    rec = {"ts": round(ts, 3), "t": store.now_iso(ts), "by": by, "task": task_id, "cmd": _cmdline(cmd),
           "cwd": cwd, "exit": code, "duration_s": round(time.monotonic() - t0, 3),
           "out_sha": h.hexdigest()[:16], "out_tail": tail.decode("utf-8", errors="replace")}
    with team.lock("verify"):
        store.append_jsonl(team.path("verify.jsonl"), rec)
    events.emit(team, by, "verify", task=task_id, cmd=rec["cmd"], exit=code, duration_s=rec["duration_s"])
    return rec


def _kill(p: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            return
        try:
            p.wait(2)
            return
        except subprocess.TimeoutExpired:
            continue


def _cmdline(cmd: list[str]) -> str:
    import shlex
    return cmd[0] if len(cmd) == 1 else shlex.join(cmd)


def records(team: Team, task_id: str | None = None) -> list[dict]:
    recs = store.read_jsonl(team.path("verify.jsonl"))
    return [r for r in recs if task_id is None or r.get("task") == task_id]


def last_by_task(team: Team) -> dict[str, dict]:
    """The latest verify record of each task."""
    out: dict[str, dict] = {}
    for r in records(team):
        out[str(r.get("task"))] = r
    return out
