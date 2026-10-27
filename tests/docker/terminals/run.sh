#!/usr/bin/env bash
# Every terminal, live on a virtual X display: ../run.sh terminals [NAME...]
# (default: all). Results table on stdout; the full log, with each terminal's
# own output, in ../.work/terminals.log and ../.work/terminals-logs/.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
work=$here/../.work
tag=claude-launcher-terminals:arch
mkdir -p "$work"
log=$work/terminals.log
if ! docker build -q -t "$tag" "$here" > "$log" 2>&1; then
  echo "image build failed (see $log)"; exit 1
fi
rm -rf "$work/terminals-logs"
name=claude-launcher-terminals-$$
status=0
docker run --name "$name" -v "$repo:/src:ro" --shm-size=512m "$tag" "$@" 2>&1 | tee -a "$log" || true
status=$(docker inspect -f '{{.State.ExitCode}}' "$name")
docker cp "$name:/tmp/logs" "$work/terminals-logs" >/dev/null 2>&1 || true
docker rm "$name" >/dev/null
exit "$status"
