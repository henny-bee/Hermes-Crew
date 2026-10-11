#!/usr/bin/env python3
"""Generate / check the README "Tested with" row from ``hermes_crew.compat.KNOWN``.

    python scripts/gen-compat-row.py            print the complete table row (with markers) to stdout
    python scripts/gen-compat-row.py --check    exit 1 if README.md's marked cell differs from KNOWN
    python scripts/gen-compat-row.py --readme PATH   use another README (default: <repo>/README.md)

The README holds exactly one line of the form
    | Tested with | <!-- compat:start -->Hermes 0.21.5 – 0.21.5<!-- compat:end --> |
Only ``ok`` ranges are listed as tested; ``broken`` ranges are appended as "; known broken: ...";
``untested`` ranges are not listed. This script never writes the README.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- compat:start -->", "<!-- compat:end -->"
EN_DASH = "–"
LABEL = "Tested with"

Version = tuple[int, ...]


def parse_version(text: str) -> Version:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not m:
        raise ValueError(f"not a plain version: {text!r}")
    return tuple(int(p) for p in m.group(1).split("."))


def fmt_version(v: Version) -> str:
    return ".".join(str(p) for p in v)


def normalise_range(key: object) -> tuple[Version, Version | None]:
    """KNOWN key -> (lo, hi); hi None = open-ended. Accepts (lo, hi) pairs, a single version,
    "lo - hi" / "lo..hi" / "lo,hi" strings and ">=lo,<=hi" / ">=lo" specifier strings."""
    if isinstance(key, (tuple, list)):
        if len(key) == 1:
            key = key[0]
        elif len(key) == 2:
            lo, hi = key
            return parse_version(str(lo)), (None if hi in (None, "", "*") else parse_version(str(hi)))
        else:
            raise ValueError(f"unsupported KNOWN key: {key!r}")
    s = str(key).strip()
    if re.search(r"[<>=!~]", s):
        lo_m = re.search(r">=?\s*v?(\d+(?:\.\d+)*)", s)
        hi_m = re.search(r"<=\s*v?(\d+(?:\.\d+)*)", s)
        if re.search(r"<\s*v?\d", s) and not hi_m:
            raise ValueError(f"exclusive upper bound is not representable: {key!r}")
        if not lo_m:
            raise ValueError(f"unsupported KNOWN key: {key!r}")
        return parse_version(lo_m.group(1)), (parse_version(hi_m.group(1)) if hi_m else None)
    parts = [p for p in re.split(r"\s*(?:\.\.|,|\s[-–]\s|\s*–\s*)\s*", s) if p]
    if len(parts) == 1:
        return parse_version(parts[0]), parse_version(parts[0])
    if len(parts) == 2:
        return parse_version(parts[0]), (None if parts[1] in ("*", "+") else parse_version(parts[1]))
    raise ValueError(f"unsupported KNOWN key: {key!r}")


def fmt_range(lo: Version, hi: Version | None) -> str:
    if hi is None:
        return f"{fmt_version(lo)}+"
    if lo == hi:
        return fmt_version(lo)
    return f"{fmt_version(lo)} {EN_DASH} {fmt_version(hi)}"


def load_known() -> dict[object, object]:
    sys.path.insert(0, str(ROOT / "plugin" / "hermes-crew"))
    from hermes_crew import compat

    return dict(compat.KNOWN)


def cell_text(known: dict[object, object]) -> str:
    ok: list[tuple[Version, Version | None]] = []
    broken: list[tuple[Version, Version | None]] = []
    for key, status in known.items():
        rng = normalise_range(key)
        if status == "ok":
            ok.append(rng)
        elif status == "broken":
            broken.append(rng)
        elif status != "untested":
            raise ValueError(f"unknown status {status!r} for {key!r} (ok | untested | broken)")

    def render(ranges: list[tuple[Version, Version | None]]) -> str:
        ordered = sorted(ranges, key=lambda r: (r[0], r[1] or (10**9,)))
        return ", ".join(fmt_range(lo, hi) for lo, hi in ordered)

    text = "Hermes " + (render(ok) if ok else "none")
    if broken:
        text += f"; known broken: {render(broken)}"
    return text


def row(cell: str) -> str:
    return f"| {LABEL} | {START}{cell}{END} |"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare README.md with compat.KNOWN; exit 1 on drift")
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md", help="README to check (default: repo README.md)")
    args = ap.parse_args(argv)

    try:
        expected = cell_text(load_known())
    except (ImportError, AttributeError, ValueError) as e:
        print(f"gen-compat-row: cannot build the row from hermes_crew.compat.KNOWN: {e}", file=sys.stderr)
        return 2
    if not args.check:
        print(row(expected))
        return 0

    try:
        readme = args.readme.read_text(encoding="utf-8")
    except OSError as e:
        print(f"gen-compat-row: cannot read {args.readme}: {e}", file=sys.stderr)
        return 1
    if readme.count(START) != 1 or readme.count(END) != 1:
        print(
            f"gen-compat-row: {args.readme} must contain exactly one {START} ... {END} pair "
            f"(found {readme.count(START)} start, {readme.count(END)} end)",
            file=sys.stderr,
        )
        return 1
    start = readme.index(START) + len(START)
    end = readme.index(END)
    if end < start:
        print("gen-compat-row: end marker comes before start marker", file=sys.stderr)
        return 1
    found = readme[start:end]
    if found != expected:
        print("gen-compat-row: README 'Tested with' row is out of date with hermes_crew.compat.KNOWN", file=sys.stderr)
        print(f"  expected: {expected}", file=sys.stderr)
        print(f"  found:    {found}", file=sys.stderr)
        print("  regenerate with: python scripts/gen-compat-row.py", file=sys.stderr)
        return 1
    print(f"gen-compat-row: OK ({expected})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
