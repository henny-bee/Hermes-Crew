#!/usr/bin/env bash
# Rewrite the version everywhere check-version.sh looks. Never touches git.
# Usage: scripts/bump-version.sh <x.y.z>
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_version-lib.sh
. scripts/_version-lib.sh

new="${1:-}"
if ! printf '%s' "$new" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "usage: bump-version.sh <x.y.z>" >&2
  exit 2
fi

# Portable in-place edit: filter into a temp file, then copy over (keeps the original's mode).
replace_with() { # file awk-program (uses -v v=<new>)
  local tmp
  tmp=$(mktemp)
  awk -v v="$new" "$2" "$1" >"$tmp"
  cat "$tmp" >"$1"
  rm -f "$tmp"
}

# __init__.py: the __version__ line
replace_with "$INIT_PY" '/^__version__ = "/ && !done { print "__version__ = \"" v "\""; done = 1; next } { print }'
# plugin.yaml: first top-level version: line
replace_with "$PLUGIN_YAML" '/^version:/ && !done { print "version: " v; done = 1; next } { print }'
# skills: first version: line inside the front-matter only
for f in "${SKILL_FILES[@]}"; do
  # shellcheck disable=SC2016  # awk program, not shell
  replace_with "$f" '
    NR == 1 { fm = ($0 == "---") }
    NR > 1 && fm && /^---[ \t]*$/ { fm = 0 }
    fm && NR > 1 && /^version:/ && !done { print "version: " v; done = 1; next }
    { print }'
done

bash scripts/check-version.sh --no-tag
echo "bump-version: now add a CHANGELOG.md entry and create tag v$new"
