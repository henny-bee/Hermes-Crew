#!/usr/bin/env bash
# Shared helpers for check-version.sh / bump-version.sh. Portable to bash 3.2 and BSD userland.
# Single source of truth: __version__ in plugin/hermes-crew/hermes_crew/__init__.py.
# Sourced after `cd` to the repo root, so all paths are relative to it.
# shellcheck disable=SC2034  # variables are used by the scripts that source this file

INIT_PY=plugin/hermes-crew/hermes_crew/__init__.py
PLUGIN_YAML=plugin/hermes-crew/plugin.yaml
SKILL_FILES=(skill/*/SKILL.md)

# Canonical version from __init__.py (empty if it cannot be read).
canonical_version() {
  sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$INIT_PY" 2>/dev/null | head -n 1
}

# Value of the first top-level `version:` line (quotes and spaces stripped). Empty if absent.
plugin_version() {
  awk '/^version:/ { sub(/^version:[ \t]*/, ""); gsub(/["'"'"' \t\r]/, ""); print; exit }' "$1"
}

# Value of `version:` inside the YAML front-matter only (between line 1 `---` and the next `---`).
frontmatter_version() {
  awk '
    NR == 1 { if ($0 != "---") exit; next }
    /^---[ \t]*$/ { exit }
    /^version:/ { sub(/^version:[ \t]*/, ""); gsub(/["'"'"' \t\r]/, ""); print; exit }
  ' "$1"
}
