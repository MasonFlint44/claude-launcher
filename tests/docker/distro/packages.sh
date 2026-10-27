#!/bin/sh
# The packages a user of each distro would have: bash, Python 3, the distro's
# fzf (often too old, which is the point), and what install.sh calls if it's
# there (update-desktop-database, gtk-update-icon-cache). For the tests:
# PyGObject (GLib reading our desktop entries) and a locale that writes a
# comma for the decimal point (the hook's timestamps).
set -eux
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends bash python3 fzf ca-certificates procps \
    desktop-file-utils gtk-update-icon-cache python3-gi locales
  sed -i 's/^# *de_DE.UTF-8/de_DE.UTF-8/' /etc/locale.gen
  locale-gen
  rm -rf /var/lib/apt/lists/*
elif command -v dnf >/dev/null; then
  dnf -y install --setopt=install_weak_deps=False bash python3 fzf ca-certificates procps-ng \
    desktop-file-utils gtk-update-icon-cache shadow-utils python3-gobject glibc-langpack-de
  dnf clean all
elif command -v pacman >/dev/null; then
  # The image leaves out locale sources; let glibc's be unpacked again.
  sed -i '/^NoExtract.*i18n/d' /etc/pacman.conf
  pacman -Syu --noconfirm --needed bash python fzf ca-certificates procps-ng desktop-file-utils \
    gtk-update-icon-cache python-gobject
  pacman -S --noconfirm glibc
  sed -i 's/^#de_DE.UTF-8/de_DE.UTF-8/' /etc/locale.gen
  locale-gen
  pacman -Scc --noconfirm
else
  echo "unknown package manager" >&2
  exit 1
fi
