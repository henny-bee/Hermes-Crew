#!/usr/bin/env bash
# Print the CHANGELOG.md section for one version (Keep a Changelog format), without its heading.
# Usage: scripts/changelog-section.sh <version>      e.g. 3.0.0 (a leading "v" is accepted)
# Exits 1 if the section is missing or empty.
set -euo pipefail

ver="${1:-}"
ver="${ver#v}"
if [ -z "$ver" ]; then
  echo "usage: changelog-section.sh <version>" >&2
  exit 2
fi
file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/CHANGELOG.md"
if [ ! -f "$file" ]; then
  echo "changelog-section: $file not found" >&2
  exit 1
fi

# Section starts at "## [3.0.0]" or "## 3.0.0" (optionally followed by " - date") and ends at the next "## ".
body="$(awk -v v="$ver" '
  /^## / {
    if (inside) exit
    h = $0
    sub(/^## +\[?/, "", h)
    sub(/[] -].*$/, "", h)
    if (h == v) { inside = 1; next }
  }
  inside { print }
' "$file")"

# Trim leading/trailing blank lines.
body="$(printf '%s\n' "$body" | sed -e '/./,$!d' | awk '{l[NR]=$0} END{n=NR; while(n>0 && l[n]=="") n--; for(i=1;i<=n;i++) print l[i]}')"
if [ -z "$body" ]; then
  echo "changelog-section: no entry for version $ver in CHANGELOG.md" >&2
  exit 1
fi
printf '%s\n' "$body"
