#!/usr/bin/env bash
# Run the Docker checks. See README.md.
#   ./run.sh distro [IMAGE...]      install + test suite on each distro (default: all of DISTROS)
#   ./run.sh terminals [NAME...]    each terminal on a virtual display (terminals/run.sh)
#   ./run.sh systemd [IMAGE...]     logout and restart detection under real systemd (systemd/run.sh)
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$here/.work
mkdir -p "$work"

# The distros README.md says the launcher is for, plus the oldest and newest
# Python and fzf they ship.
DISTROS=(ubuntu:22.04 ubuntu:24.04 ubuntu:26.04 debian:12 debian:13 fedora:42 fedora:43 archlinux:latest)

tag() { printf 'claude-launcher-%s:%s' "$1" "$(tr ':/' '--' <<< "$2")"; }

distro() {
  local images=("$@") failed=() image t log
  (( ${#images[@]} )) || images=("${DISTROS[@]}")
  for image in "${images[@]}"; do
    t=$(tag distro "$image")
    log=$work/distro-$(tr ':/' '--' <<< "$image").log
    printf '%-20s ' "$image"
    if ! docker build -q --build-arg "BASE=$image" -t "$t" "$here/distro" > "$log" 2>&1; then
      echo "image build failed (see $log)"; failed+=("$image")
      [[ -z ${CI:-} ]] || cat "$log"
      continue
    fi
    if docker run --rm -v "$repo:/src:ro" "$t" >> "$log" 2>&1; then
      echo "pass  $(grep -m1 -E '^[0-9]+ passed' "$log" || tail -1 "$log")"
    else
      echo "FAIL (see $log)"; failed+=("$image")
      [[ -z ${CI:-} ]] || cat "$log"
    fi
  done
  (( ${#failed[@]} == 0 )) || { echo "failed: ${failed[*]}"; return 1; }
}

case ${1:-} in
  distro) shift; distro "$@" ;;
  terminals|systemd) layer=$1; shift; exec "$here/$layer/run.sh" "$@" ;;
  *) sed -n '2,5p' "$0" | sed 's/^# //' >&2; exit 2 ;;
esac
