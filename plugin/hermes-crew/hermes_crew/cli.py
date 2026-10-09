"""`hermes-crew` command line. Exit codes: 0 ok, 1 refused/failed, 2 usage."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, board, events, mailbox, state, status, tasks, verify
from .team import RESERVED, SYSTEM, Team, current_role, is_lead, same_role, valid_role

EPILOG = """examples:
  hermes-crew send Engineer "please implement T3"     hermes-crew status
  hermes-crew task add "Write parser" --deps T1        hermes-crew task claim T2
  hermes-crew verify T2 -- pytest -q                   hermes-crew board own src/parser.py"""


class Refused(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors: exit 2 with a hint
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n(see: {self.prog} --help)\n")
        sys.exit(2)


def _ctx() -> tuple[Team, str]:
    return Team.resolve(), current_role()


# ---------------------------------------------------------------- send / inbox
def recipients(team: Team, me: str, to: str) -> list[str]:
    if to.lower() == "all":
        rs = [r for r in team.roles() if not same_role(r, me)]
        if not same_role(me, "lead") and "lead" not in [r.lower() for r in rs]:
            rs.insert(0, "lead")
        if not rs:
            raise Refused("REFUSED: no teammates to send to - spawn some first: hermes-team-spawn <Role> \"<description>\"")
        return rs
    if not valid_role(to):
        raise Refused(f"REFUSED: '{to}' is not a role name (letters, digits, _). Team: {_team_list(team)}")
    if to.lower() in (SYSTEM, "user"):
        raise Refused(f"REFUSED: '{to}' cannot receive messages. Team: {_team_list(team)}")
    r = team.canon_role(to)
    if r is None:
        raise Refused(f"REFUSED: no teammate '{to}'. Team: {_team_list(team)} - check the spelling, "
                      f"or ask lead to spawn it")
    if same_role(r, me):
        raise Refused(f"REFUSED: you are {me}; send to someone else. Team: {_team_list(team)}")
    return [r]


def _team_list(team: Team) -> str:
    rs = team.roles()
    if "lead" not in [r.lower() for r in rs]:
        rs = ["lead", *rs]
    return " ".join(rs)


def cmd_send(a) -> int:
    team, me = _ctx()
    body = " ".join(a.message).strip()
    if not body:
        raise Refused('REFUSED: empty message: hermes-crew send <Role> "<text>"')
    for r in recipients(team, me, a.to):
        m = mailbox.send(team, me, r, body, reply_to=a.reply_to)
        print(f"queued #{mailbox.short_id(m['id'])} for {r} (delivered when {r} is idle)")
    return 0


def _fmt_msg(m: dict) -> str:
    body = str(m.get("body", "")).replace("\n", "\n    ")
    return f"  #{mailbox.short_id(m['id'])} {str(m.get('sent_at', ''))[11:19]} from {m.get('from')}:\n    {body}"


def cmd_inbox(a) -> int:
    team, me = _ctx()
    pend = mailbox.pending(team, me)
    if a.all:
        done = mailbox.delivered(team, me, 20)
        print(f"delivered to {me} (last {len(done)}):" if done else f"nothing delivered to {me} yet")
        for m in done:
            print(_fmt_msg(m))
    print(f"{len(pend)} pending for {me} (delivered automatically when {me} is idle):" if pend
          else f"no pending messages for {me}")
    for m in pend:
        print(_fmt_msg(m))
    return 0


# ---------------------------------------------------------------- status / watch / log
def cmd_status(a) -> int:
    team = Team.resolve()
    if a.done_check:
        v = status.done_check(team)
        if a.json:
            print(json.dumps({"done": not v, "missing": v}, indent=1, ensure_ascii=False))
        else:
            print(status.render_done_check(v))
        return 0 if not v else 1
    m = status.model(team)
    print(json.dumps(m, indent=1, ensure_ascii=False, default=str) if a.json else status.render(m))
    return 0


def cmd_watch(a) -> int:
    team = Team.resolve()
    try:
        while True:
            text = status.render(status.model(team))
            sys.stdout.write("\x1b[H\x1b[2J" + text + f"\n\n(every {a.interval:g}s · Ctrl+C to quit)\n")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_log(a) -> int:
    team = Team.resolve()
    evs = events.read(team, n=a.n, kind=a.kind, role=a.role)
    if a.json:
        for e in evs:
            print(json.dumps(e, ensure_ascii=False))
    elif not evs:
        print("no events" + (" matching" if a.kind or a.role else " yet"))
    for e in evs if not a.json else []:
        print(events.format_event(e))
    return 0


# ---------------------------------------------------------------- tasks
def _deps(s: str | None) -> list[str]:
    return [d.strip() for d in (s or "").replace(" ", ",").split(",") if d.strip()]


def _task_line(t: dict) -> str:
    extra = ""
    if t["waiting_on"] and t["status"] != "done":
        extra = f"  (waits {','.join(t['waiting_on'])})"
    if t.get("reason") and t["status"] in ("blocked", "orphaned", "open", "claimed"):
        extra += f"  ! {t['reason']}"
    return (f"{t['id']:<4} {t['status']:<11} {status._clip(t.get('owner') or '-', 12):<12} P{t['priority']}  "
            f"{status._clip(t['title'], 44)}{extra}")


def _when_ready(t: dict) -> str:
    if t["ready"]:
        return f". Start with: hermes-crew task start {t['id']}"
    return f". It waits for {', '.join(t['waiting_on'])}; you get a message when it is unblocked"


def cmd_task(a) -> int:
    team, me = _ctx()
    op = a.op
    if op == "add":
        owner = a.owner
        if owner:
            canon = team.canon_role(owner)
            if canon is None and valid_role(owner) and owner.lower() not in RESERVED:
                canon = owner     # may be spawned later
            if canon is None:
                raise Refused(f"REFUSED: '{owner}' cannot own tasks. Team: {_team_list(team)}")
            owner = canon
        t = tasks.add(team, me, " ".join(a.title), deps=_deps(a.deps), priority=a.priority, owner=owner, desc=a.desc)
        bits = [f"priority {t['priority']}"]
        if t["deps"]:
            bits.append(f"deps {','.join(t['deps'])}" + ("" if t["ready"] else " - not ready yet"))
        if t.get("owner"):
            bits.append(f"owner {t['owner']}")
        print(f"added {t['id']} '{t['title']}' ({', '.join(bits)})")
        if t.get("owner") and not same_role(t["owner"], me):
            mailbox.send(team, SYSTEM, t["owner"], f"{me} assigned you {t['id']} '{t['title']}'"
                         + _when_ready(t))
        return 0
    if op == "list":
        ts = tasks.mine(team, me) if a.mine else tasks.list_tasks(team)
        if a.json:
            print(json.dumps(ts, indent=1, ensure_ascii=False))
            return 0
        if not ts:
            print(f"no tasks{' owned by ' + me if a.mine else ''} - add one: hermes-crew task add \"<title>\"")
        for t in ts:
            print(_task_line(t))
        return 0
    if op == "show":
        t = tasks.get(team, a.id)
        if not t:
            raise Refused(f"REFUSED: no task {tasks.norm_id(a.id)} - see: hermes-crew task list")
        print(_task_line(t))
        for k in ("desc", "deps", "created_by", "created", "updated", "reason", "handoff", "prev_owner"):
            if t.get(k):
                print(f"  {k}: {', '.join(t[k]) if isinstance(t[k], list) else t[k]}")
        vs = verify.records(team, t["id"])
        for v in vs[-3:]:
            print(f"  verify {v['t'][11:19]} by {v['by']}: exit {v['exit']} ({v['duration_s']} s) {v['cmd']}")
        print("  history:")
        for ln in tasks.history(team, t["id"]):
            extra = {k: v for k, v in ln.items() if k not in ("ts", "t", "by", "op", "id")}
            print(f"    {ln['t'][11:19]} {ln['by']:<12} {ln['op']:<8} {json.dumps(extra, ensure_ascii=False) if extra else ''}".rstrip())
        return 0
    if op == "claim":
        t = tasks.claim(team, me, a.id)
        print(f"{t['owner']} owns {t['id']} '{t['title']}' - when you begin: hermes-crew task start {t['id']}")
    elif op == "start":
        t = tasks.start(team, me, a.id)
        print(f"{t['id']} in progress ({t['owner']})")
    elif op == "block":
        t = tasks.block(team, me, a.id, " ".join(a.reason))
        print(f"{t['id']} blocked: {t['reason']}")
        if not is_lead(me):
            mailbox.send(team, SYSTEM, "lead", f"{me} blocked {t['id']} '{t['title']}': {t['reason']}")
            print("(lead was told)")
    elif op == "unblock":
        t = tasks.unblock(team, me, a.id)
        print(f"{t['id']} unblocked, in progress ({t['owner']})")
    elif op == "done":
        before = {x["id"]: x for x in tasks.list_tasks(team)}
        t = tasks.done(team, me, a.id, handoff=a.handoff)
        print(f"{t['id']} done" + (f" (handoff: {t['handoff']})" if t.get("handoff") else ""))
        for x in tasks.list_tasks(team):
            if x["deps"] and x["ready"] and x["status"] != "done" and not before[x["id"]]["ready"]:
                print(f"  -> {x['id']} is now ready; told {x.get('owner') or 'lead (no owner)'}")
        v = verify.records(team, t["id"])
        if not v:
            print(f"  note: no verify record for {t['id']} - if it has a check, record it: "
                  f"hermes-crew verify {t['id']} -- <command>")
    elif op == "assign":
        owner = team.canon_role(a.role) or a.role
        if not valid_role(owner) or owner.lower() in (SYSTEM, "user"):
            raise Refused(f"REFUSED: '{a.role}' cannot own tasks. Team: {_team_list(team)}")
        t = tasks.assign(team, me, a.id, owner)
        print(f"{t['id']} assigned to {t['owner']}")
    elif op == "reopen":
        t = tasks.reopen(team, me, a.id, " ".join(a.reason))
        print(f"{t['id']} reopened ({t['status']}{', ' + t['owner'] if t.get('owner') else ''}): {t['reason']}")
    return 0


# ---------------------------------------------------------------- verify / board / doctor
def cmd_verify(a) -> int:
    team, me = _ctx()
    cmd = list(a.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("usage: hermes-crew verify <task> [--timeout S] -- <command...>", file=sys.stderr)
        return 2
    t = tasks.get(team, a.task)
    if t is None:
        raise Refused(f"REFUSED: no task {tasks.norm_id(a.task)} - see: hermes-crew task list "
                      f"(evidence must belong to a task)")

    def echo(b: bytes) -> None:
        sys.stdout.buffer.write(b)
        sys.stdout.flush()

    rec = verify.run(team, me, t["id"], cmd, timeout=a.timeout, echo=echo)
    sys.stdout.flush()
    print(f"\nverify {t['id']}: exit {rec['exit']} in {rec['duration_s']:g} s - recorded "
          f"(output sha {rec['out_sha']})", file=sys.stderr)
    return int(rec["exit"]) if 0 <= int(rec["exit"]) < 256 else 1


def cmd_board(a) -> int:
    return board.main(list(a.args))


def cmd_doctor(a) -> int:
    from . import doctor
    return doctor.main(json_out=a.json)


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="hermes-crew", description="Hermes Crew: messages, tasks, board and status of a Hermes agent team.",
               epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"hermes-crew {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>", parser_class=Parser)

    s = sub.add_parser("send", help="queue a message: send <Role|lead|all> <message...>")
    s.add_argument("to")
    s.add_argument("message", nargs="+")
    s.add_argument("--reply-to", help="id of the message this answers")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("inbox", help="my pending messages (--all: also recently delivered)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("status", help="agents, tasks, files, decisions (--done-check: exit 0 only if done)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--done-check", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("watch", help="live status, Ctrl+C to quit")
    s.add_argument("--interval", type=float, default=2.0)
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("board", help="decide|own|transfer|release|approve|status (same as hermes-team-board)",
                       add_help=False)
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_board)

    t = sub.add_parser("task", help="task registry: add|claim|start|block|unblock|done|assign|reopen|list|show")
    ts = t.add_subparsers(dest="op", metavar="<op>", parser_class=Parser, required=True)
    x = ts.add_parser("add", help='add "<title>" [--deps T1,T2] [--priority N] [--owner Role] [--desc TEXT]')
    x.add_argument("title", nargs="+")
    x.add_argument("--deps")
    x.add_argument("--priority", type=int, default=3, help="1 = highest (default 3)")
    x.add_argument("--owner")
    x.add_argument("--desc")
    for op in ("claim", "start", "unblock", "show"):
        ts.add_parser(op).add_argument("id")
    x = ts.add_parser("block")
    x.add_argument("id")
    x.add_argument("reason", nargs="+")
    x = ts.add_parser("reopen")
    x.add_argument("id")
    x.add_argument("reason", nargs="+")
    x = ts.add_parser("done")
    x.add_argument("id")
    x.add_argument("--handoff", metavar="FILE", help="notes for whoever continues; copied to .team/handoff/<id>.md")
    x = ts.add_parser("assign")
    x.add_argument("id")
    x.add_argument("role")
    x = ts.add_parser("list")
    x.add_argument("--mine", action="store_true")
    x.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_task)

    s = sub.add_parser("verify", help="verify <task> [--timeout S] -- <command...>: run it, record the evidence")
    s.add_argument("task")
    s.add_argument("--timeout", type=float, default=None)
    s.add_argument("command", nargs="*", help="the command to run, after --")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("log", help="recent events [-n N] [--kind PREFIX] [--role R]")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--kind")
    s.add_argument("--role")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("doctor", help="check the installation (exit 1 if anything FAILs)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["board"]:                 # raw pass-through: board parses its own args
        return board.main(argv[1:])
    p = build_parser()
    cmd_after = None
    if argv[:1] == ["verify"] and "--" not in argv:   # `verify T1 pytest -q`: the command starts after <task>
        i = 1
        while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-h", "--help"):
            i += 1 if "=" in argv[i] else 2
        if i + 1 < len(argv):
            argv = argv[: i + 1] + ["--"] + argv[i + 1:]
    if argv[:1] == ["verify"] and "--" in argv:   # options may come before or after <task>
        i = argv.index("--")
        argv, cmd_after = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    if cmd_after is not None:
        a.command = cmd_after
    if not getattr(a, "fn", None):
        p.print_help(sys.stderr)
        return 2
    try:
        return a.fn(a)
    except (Refused, tasks.TaskError, board.BoardError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
