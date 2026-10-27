#!/bin/sh
# PID 1 of the container. Each start of the container is a new boot: the
# kernel's boot ID is the host's, so a fresh one is mounted over it, as
# systemd-nspawn does. journald and the hook read it from there.
set -e
cat /proc/sys/kernel/random/uuid > /etc/boot-id
mount --bind /etc/boot-id /proc/sys/kernel/random/boot_id
# systemd needs to write its own cgroup tree (the container's, from
# --cgroupns=private); without --privileged, Docker mounts it read-only.
grep -q ' /sys/fs/cgroup cgroup2 rw' /proc/mounts || mount -o remount,rw /sys/fs/cgroup
exec /usr/lib/systemd/systemd
