#!/usr/bin/env python3
"""Scrub a directory tree before it goes into the repo: replace machine-specific paths and fail loudly on anything
that looks like a secret.

usage: scrub.py <dir> <work-dir-to-replace> [<work-dir>...]
env:   SCRUB_SECRETS = newline-separated literal secret values that must not appear
"""

import os
import re
import sys
from pathlib import Path

root, *workdirs = sys.argv[1:]
secrets = [s for s in os.environ.get("SCRUB_SECRETS", "").split("\n") if len(s) >= 8]
repl = [(w, "/tmp/example") for w in workdirs] + [
    (os.path.expanduser(os.environ.get("REAL_HOME", "~")), "/home/user"),
]
WIN_USER = re.compile(r"/mnt/c/Users/[^/\s\"']+")  # WSL view of a Windows profile
PATTERNS = [
    r"sk-[A-Za-z0-9_-]{16,}",
    r"Bearer\s+[A-Za-z0-9._-]{12,}",
    r"(?i)api[_-]?key\"?\s*[:=]\s*\"?[A-Za-z0-9._-]{12,}",
    r"HERMES_CUSTOM_[A-Z0-9_]*API_KEY",
    r"AGENT_MAIL_TOKEN",
    r"eyJ[A-Za-z0-9_-]{20,}\.",
]
bad, n = [], 0
for dp, _dn, files in os.walk(root):
    for f in files:
        p = os.path.join(dp, f)
        try:
            text = Path(p).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            bad.append((p, "binary/unreadable file"))
            continue
        new = text
        for a, b in repl:
            if a:
                new = new.replace(a, b)
        new = WIN_USER.sub("C:/Users/user", new)
        for s in secrets:
            if s in new:
                bad.append((p, "contains a secret value"))
        for pat in PATTERNS:
            for m in re.finditer(pat, new):
                bad.append((p, f"matches {pat!r}: {m.group(0)[:12]}..."))
        if new != text:
            Path(p).write_text(new, encoding="utf-8")
            n += 1
print(f"scrub: {n} files rewritten, {len(bad)} problems")
for p, why in bad:
    print("  PROBLEM", p, why)
sys.exit(1 if bad else 0)
