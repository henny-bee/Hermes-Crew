#!/usr/bin/env bash
# Assert that every place that carries the version agrees with __version__:
#   plugin.yaml, the front-matter of every skill/*/SKILL.md, and the latest git tag (v<version>).
# Usage: scripts/check-version.sh [--no-tag]
#   --no-tag  skip the git tag comparison (for bump-version.sh and PRs that bump before tagging)
# Exit: 0 ok, 1 mismatch, 2 usage / __version__ unreadable.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_version-lib.sh
. scripts/_version-lib.sh

check_tag=1
case "${1:-}" in
  --no-tag) check_tag=0 ;;
  "") ;;
  *) echo "usage: check-version.sh [--no-tag]" >&2; exit 2 ;;
esac

want=$(canonical_version)
if [ -z "$want" ]; then
  echo "check-version: cannot read __version__ from $INIT_PY" >&2
  exit 2
fi

rc=0
compare() { # label got [missing-message]
  if [ -z "$2" ]; then
    echo "check-version: $1: ${3:-no version found}" >&2
    rc=1
  elif [ "$2" != "$want" ]; then
    echo "check-version: $1: version $2 != __version__ $want" >&2
    rc=1
  fi
}

compare "$PLUGIN_YAML" "$(plugin_version "$PLUGIN_YAML")" "no version: line"
for f in "${SKILL_FILES[@]}"; do
  compare "$f" "$(frontmatter_version "$f")" "no version: in front-matter"
done

if [ "$check_tag" = 1 ]; then
  tag=""
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    tag=$(git tag --list 'v*' --sort=-v:refname | head -n 1)
  fi
  if [ -z "$tag" ]; then
    echo "check-version: no v* git tag found (CI needs fetch-depth: 0)" >&2
    rc=1
  elif [ "${tag#v}" != "$want" ]; then
    echo "check-version: git tag $tag != __version__ $want" >&2
    rc=1
  fi
fi

[ "$rc" = 0 ] && echo "check-version: OK $want"
exit "$rc"
