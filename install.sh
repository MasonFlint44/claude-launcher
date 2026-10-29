#!/usr/bin/env bash
# Set up claude-launcher, its Claude Code hook, and what this platform adds
# (on Linux: the Claude Dark Ghostty theme and the "Claude Code" desktop
# launcher). Safe to re-run: it updates whatever is already installed, and
# remembers --terminal and --icon from last time. The steps themselves are in
# src/claude_launcher/install.py.
usage() {
  cat <<'USAGE'
usage: install.sh [--terminal NAME[,NAME...]] [--icon FILE|default]
       install.sh --uninstall

  --terminal  the terminal(s) "Claude Code" opens; the first gets the plain
              name. One of: ghostty kitty wezterm ptyxis gnome-terminal
              konsole guake default. Without it: last time's choice, else the
              first of those that's installed (guake only when asked for).
              "auto" goes back to that.
  --icon      your own app icon (.svg, or a square .png); "default" goes back
              to the one in share/icons.
USAGE
}
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
core=$repo/libexec/claude-launcher-core

say()  { printf '\e[1;38;2;215;119;87m==>\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33mwarning:\e[0m %s\n' "$*" >&2; }
error() { printf '\e[1;31merror:\e[0m %s\n' "$*" >&2; }

# The steps are in Python (src/claude_launcher/install.py), the same as
# install.ps1's. Show their lines as progress: "warning: ..." as a warning,
# and "error: ..." as the error that stopped them.
run() {
  local line status
  "$core" install "$@" | while IFS= read -r line; do
    case $line in
      "warning: "*) warn "${line#warning: }" ;;
      "error: "*) error "${line#error: }" ;;
      *) say "$line" ;;
    esac
  done
  status=${PIPESTATUS[0]}
  return "$status"
}

action=install terminal="" icon=""
while (( $# )); do
  case $1 in
    --uninstall) action=uninstall ;;
    --terminal) terminal=${2:?--terminal needs a name}; shift ;;
    --terminal=*) terminal=${1#*=} ;;
    --icon) icon=${2:?--icon needs a file}; shift ;;
    --icon=*) icon=${1#*=} ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null ||
  { error "claude-launcher needs Python 3.10 or newer"; exit 1; }
if [[ $action == uninstall ]]; then
  run undo
  exit 0  # goes on past a step that fails, and says so
fi
if [[ -n $icon && $icon != default ]]; then
  icon=$(realpath "$icon")
fi
run run ${terminal:+--terminal "$terminal"} ${icon:+--icon "$icon"}
