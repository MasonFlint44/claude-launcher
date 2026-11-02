#!/usr/bin/env bash
# A version's notes from CHANGELOG.md: what's under "## [VERSION]", up to the
# next heading or the links at the end. Fails if there are none.
# usage: scripts/release-notes.sh 0.1.0
set -euo pipefail
version=${1:?usage: release-notes.sh VERSION}
changelog=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/CHANGELOG.md
notes=$(awk -v v="$version" '
  /^## / { on = index($0, "## [" v "]") == 1; next }
  /^\[[^]]+\]: / { on = 0 }
  on
' "$changelog" | sed -e '/./,$!d')
[[ -n ${notes//[[:space:]]/} ]] || { echo "no notes for $version in CHANGELOG.md" >&2; exit 1; }
printf '%s\n' "$notes"
