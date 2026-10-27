#!/bin/sh
# systemd with logind and pam_systemd, D-Bus, Python 3 for the checks, and
# util-linux for setpriv.
set -eux
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends systemd dbus libpam-systemd libpam-modules python3 procps util-linux
  rm -rf /var/lib/apt/lists/*
elif command -v dnf >/dev/null; then
  dnf -y install --setopt=install_weak_deps=False systemd systemd-pam dbus-broker python3 procps-ng util-linux \
    pam shadow-utils
  dnf clean all
elif command -v pacman >/dev/null; then
  pacman -Syu --noconfirm --needed systemd python procps-ng util-linux pam
  pacman -Scc --noconfirm
else
  echo "unknown package manager" >&2
  exit 1
fi
