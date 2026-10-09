"""Maildir mailbox: `.team/mail/<Role>/{tmp,new,cur,expired}/<id>.json`.

send    = write tmp/<id>.json, then rename -> new/  (a reader never sees half a message)
claim   = rename new/ -> cur/  (atomic; whoever renames first owns the delivery)
unclaim = rename cur/ -> new/  (delivery failed, retry later)
expire  = new/ -> expired/ after ttl_s, and the sender is told
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from . import events, store
from .team import SYSTEM, Team

DEFAULT_TTL = 3600
SUBDIRS = ("tmp", "new", "cur", "expired")


def box(team: Team, role: str) -> Path:
    """Mailbox dir of `role`; an existing dir with another spelling (case-insensitive) wins."""
    root = team.path("mail")
    if root.is_dir() and not (root / role).is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name.lower() == role.lower():
                return d
    return root / role


def _ensure(team: Team, role: str) -> Path:
    team.ensure()
    b = box(team, role)
    for s in SUBDIRS:
        (b / s).mkdir(parents=True, exist_ok=True)
    return b


def new_id(sender: str) -> str:
    return f"{time.time_ns():x}-{sender}-{secrets.token_hex(2)}"


def short_id(msg_id: str) -> str:
    """Last 4 hex chars of the id (display only)."""
    return msg_id.rsplit("-", 1)[-1]


def send(team: Team, sender: str, to: str, body: str, reply_to: str | None = None,
         ttl_s: int = DEFAULT_TTL) -> dict:
    """Queue `body` for `to`. Returns the message dict. Emits `msg.queued`."""
    b = _ensure(team, to)
    ts = time.time()
    mid = new_id(sender)
    msg = {"id": mid, "from": sender, "to": b.name, "sent_at": store.now_iso(ts), "ts": round(ts, 6),
           "body": body, "reply_to": reply_to, "ttl_s": int(ttl_s)}
    tmp = b / "tmp" / f"{mid}.json"
    tmp.write_text(store.dumps(msg) + "\n", encoding="utf-8")
    os.rename(tmp, b / "new" / f"{mid}.json")
    events.emit(team, sender, "msg.queued", id=mid, to=b.name, body=body[:200], reply_to=reply_to)
    return msg


def _load(p: Path) -> dict | None:
    m = store.read_json(p)
    return m if isinstance(m, dict) else None


def pending(team: Team, role: str) -> list[dict]:
    """Messages waiting in new/, oldest first."""
    d = box(team, role) / "new"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        m = _load(f)
        if m:
            out.append(m)
    return out


def _seen(b: Path, mid: str) -> bool:
    return (b / "cur" / f"{mid}.json").exists() or (b / "expired" / f"{mid}.json").exists()


def claim(team: Team, role: str, msg_id: str) -> dict | None:
    """Atomically move one message new/ -> cur/. None if someone else got it or it is a duplicate."""
    b = box(team, role)
    src = b / "new" / f"{msg_id}.json"
    if _seen(b, msg_id):          # dedup: already delivered or expired -> never again
        try:
            src.unlink()
        except FileNotFoundError:
            pass
        return None
    dst = b / "cur" / f"{msg_id}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        # link+unlink instead of rename: rename would silently overwrite a concurrent claimer's file.
        os.link(src, dst)
    except FileExistsError:
        return None
    except FileNotFoundError:
        return None
    except OSError:
        try:
            os.rename(src, dst)
        except OSError:
            return None
        return _load(dst)
    try:
        os.unlink(src)
    except FileNotFoundError:
        pass            # a concurrent dedup check removed src; our link created dst, so it is ours
    return _load(dst)


def claim_all(team: Team, role: str) -> list[dict]:
    """Claim every pending message (oldest first); returns the ones this caller got."""
    got = []
    for m in pending(team, role):
        c = claim(team, role, m["id"])
        if c:
            got.append(c)
    return got


def unclaim(team: Team, role: str, msg_id: str) -> bool:
    """Put a claimed message back into new/ (delivery failed)."""
    b = box(team, role)
    try:
        os.rename(b / "cur" / f"{msg_id}.json", b / "new" / f"{msg_id}.json")
        return True
    except OSError:
        return False


def mark_delivered(team: Team, role: str, msgs: list[dict]) -> None:
    """Emit one `msg.delivered` event per message (role = recipient)."""
    for m in msgs:
        events.emit(team, role, "msg.delivered", id=m["id"], **{"from": m.get("from")})


def delivered(team: Team, role: str, n: int = 20) -> list[dict]:
    """The last `n` delivered messages (cur/), oldest first."""
    d = box(team, role) / "cur"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.json"))[-n:] if n > 0 else []
    return [m for m in (_load(f) for f in files) if m]


def expired_msgs(team: Team, role: str) -> list[dict]:
    d = box(team, role) / "expired"
    return [m for m in (_load(f) for f in sorted(d.glob("*.json"))) if m] if d.is_dir() else []


def expire(team: Team, now: float | None = None) -> list[dict]:
    """Move messages older than their ttl_s from new/ to expired/; tell each sender. Returns them."""
    now = time.time() if now is None else now
    root = team.path("mail")
    if not root.is_dir():
        return []
    gone = []
    for b in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted((b / "new").glob("*.json")) if (b / "new").is_dir() else []:
            m = _load(f)
            if not m:
                continue
            try:
                age = now - float(m.get("ts") or 0)
                ttl = float(m.get("ttl_s") or DEFAULT_TTL)
            except (TypeError, ValueError):
                continue
            if age <= ttl:
                continue
            (b / "expired").mkdir(exist_ok=True)
            try:
                os.rename(f, b / "expired" / f.name)
            except OSError:
                continue            # delivered/claimed meanwhile
            gone.append(m)
            events.emit(team, SYSTEM, "msg.expired", id=m["id"], to=b.name, **{"from": m.get("from")})
            sender = str(m.get("from") or "")
            if sender and sender.lower() not in (SYSTEM, "user"):
                send(team, SYSTEM, sender,
                     f"your message #{short_id(m['id'])} to {b.name} expired undelivered after "
                     f"{int(ttl)} s ({b.name} never became idle): {_clip(str(m.get('body', '')), 120)}")
    return gone


def render(msgs: list[dict]) -> str:
    """The text injected into the recipient's session for a batch of messages."""
    def block(m: dict) -> str:
        t = str(m.get("sent_at", ""))[11:19]
        return f"[from {m.get('from')} sent {t} · #{short_id(str(m.get('id', '')))}] {m.get('body', '')}"

    if not msgs:
        return ""
    if len(msgs) == 1:
        text = block(msgs[0])
    else:
        text = f"You have {len(msgs)} new team messages:\n\n" + "\n\n".join(block(m) for m in msgs)
    senders = []
    for m in msgs:
        s = str(m.get("from"))
        if s not in senders and s.lower() not in (SYSTEM, "user"):
            senders.append(s)
    target = senders[0] if len(senders) == 1 else "<Role>"
    if senders:
        text += f'\n\n(reply: hermes-crew send {target} "...")'
    return text


def _clip(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
