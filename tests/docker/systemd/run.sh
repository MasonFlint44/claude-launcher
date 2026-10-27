#!/usr/bin/env bash
# Restart and logout detection under real systemd, one container per distro.
# Each container boots systemd as PID 1, check.py logs sessions out and checks
# the ↻ group, the container reboots (a new boot ID, see boot.sh), and
# check.py checks the restart. usage: run.sh [IMAGE...] (all in parallel)
# KEEP=1 leaves the containers (claude-launcher-systemd-<distro>) to look at.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
work=$(cd "$here/.." && pwd)/.work
mkdir -p "$work"

# One per systemd version the distros ship.
DISTROS=(ubuntu:22.04 debian:12 ubuntu:24.04 debian:13 fedora:42 fedora:43 ubuntu:26.04 archlinux:latest)
# Less than --privileged: systemd needs its own cgroup tree, writable
# (boot.sh remounts it), and boot.sh's mounts need SYS_ADMIN and no AppArmor
# profile (Docker's default one denies mount). Override with RUN_OPTS="...".
if [[ -n ${RUN_OPTS:-} ]]; then
  read -r -a RUN_OPTS <<< "$RUN_OPTS"
else
  RUN_OPTS=(--cgroupns=private --cap-add SYS_ADMIN --security-opt apparmor=unconfined --tmpfs /run --tmpfs /run/lock)
fi

slug() { tr ':/' '--' <<< "$1"; }

booted() {  # wait until systemd has finished starting up
  local name=$1 state
  for _ in $(seq 100); do
    state=$(docker exec "$name" systemctl is-system-running 2>/dev/null || true)
    [[ $state == running || $state == degraded ]] && return 0
    sleep 0.2
  done
  echo "systemd didn't finish booting ($state)"; return 1
}

one() {
  local image=$1 tag name status=0
  tag=claude-launcher-systemd:$(slug "$image")
  name=claude-launcher-systemd-$(slug "$image")
  docker build -q --build-arg "BASE=$image" -t "$tag" "$here" >/dev/null || { echo "image build failed"; return 1; }
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" "${RUN_OPTS[@]}" -v "$repo:/src:ro" -v "$here:/check:ro" "$tag" >/dev/null
  booted "$name" || { docker rm -f "$name" >/dev/null; return 1; }
  docker exec "$name" python3 /check/check.py before || status=1
  docker exec "$name" systemctl reboot || true
  docker wait "$name" >/dev/null
  docker start "$name" >/dev/null
  booted "$name" && { docker exec "$name" python3 /check/check.py after || status=1; } || status=1
  [[ ${KEEP:-} == 1 ]] || docker rm -f "$name" >/dev/null
  return $status
}

images=("$@")
(( ${#images[@]} )) || images=("${DISTROS[@]}")
# All at once: each spends most of its time waiting out the logout windows.
pids=()
for image in "${images[@]}"; do
  one "$image" > "$work/systemd-$(slug "$image").log" 2>&1 &
  pids+=($!)
done
failed=()
for i in "${!images[@]}"; do
  printf '%-18s ' "${images[i]}"
  if wait "${pids[i]}"; then echo pass; else echo "FAIL (see $work/systemd-$(slug "${images[i]}").log)"; failed+=("${images[i]}"); fi
done

# One row per check, one column per distro.
python3 - "$work" "${images[@]}" <<'PY'
import sys
from pathlib import Path
work, images = Path(sys.argv[1]), sys.argv[2:]
cols, names = {}, []
for image in images:
    rows = {}
    log = work / f"systemd-{image.replace(':', '-').replace('/', '-')}.log"
    for line in log.read_text(errors="replace").splitlines() if log.exists() else []:
        if line.startswith("RESULT "):
            _, name, status, *rest = line.split(" ", 3) + [""]
            if status == "INFO" and not name.startswith(("systemd", "journal-dir", "list-boots", "logind-uses")):
                continue
            rows[name] = rest[0] if status == "INFO" else status
            if name not in names:
                names.append(name)
    cols[image] = rows
short = [i.split(":")[0][:6] + ":" + i.split(":")[1][:6] for i in images]
print("\n" + "check".ljust(34) + "".join(s.ljust(15) for s in short))
for name in names:
    print(name.ljust(34) + "".join(cols[i].get(name, "-")[:14].ljust(15) for i in images))
PY
(( ${#failed[@]} == 0 )) || { echo "failed: ${failed[*]}"; exit 1; }
