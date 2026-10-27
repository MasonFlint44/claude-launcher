#!/usr/bin/env bash
# Runs inside the terminals image as `tester`, with the repo mounted
# read-only at /src: a virtual X display with a window manager, a session bus,
# then check.py (the checks) for the terminals named in "$@".
set -euo pipefail
repo=$HOME/claude-launcher
mkdir -p "$repo"
tar -C /src --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=.ruff_cache --exclude=tests/docker/.work -cf - . | tar -C "$repo" -xf -
export PATH=$HOME/.local/bin:$PATH DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 100); do xdpyinfo >/dev/null 2>&1 && break; xprop -root >/dev/null 2>&1 && break; sleep 0.1; done
openbox >/tmp/openbox.log 2>&1 &
# The bus's chatter goes to a log; the checks' own errors stay on stderr.
mkdir -p /tmp/logs
exec 3>&2
exec dbus-run-session -- sh -c 'exec python3 /usr/local/lib/terminals-check.py "$@" 2>&3' sh "$repo" "$@" \
  2>/tmp/logs/session.log
