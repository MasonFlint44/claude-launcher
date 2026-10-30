#!/usr/bin/env bash
# Set up claude-launcher, its Claude Code hook, and what this platform adds
# (on Linux: the Claude Dark Ghostty theme and the "Claude Code" desktop
# launcher). Safe to re-run: it updates whatever is already installed, and
# remembers --terminal and --icon from last time. The steps themselves are in
# src/claude_launcher/install.py.
usage() {
  local names
  # The platform's terminals, from the core; none on a platform without them.
  names=$("$core" terminal list 2>/dev/null) || names=
  cat <<'USAGE'
usage: install.sh [--terminal NAME[,NAME...]] [--icon FILE|default]
       install.sh --uninstall

USAGE
  option --terminal "the terminal(s) \"Claude Code\" opens; the first gets the plain\
 name.${names:+ One of: $names.} Without it: last time's choice, else the first of\
 those that's installed (drop-down ones only when asked for). \"auto\" goes back\
 to that. Linux desktops only."
  option --icon "your own app icon (.svg, or a square .png; .png for Windows\
 Terminal); \"default\" goes back to the one in share/icons."
}
# An option's help, wrapped to fit 80 columns.
option() {
  printf '%s\n' "$2" | fold -s -w 62 | sed 's/ *$//' | {
    IFS= read -r line && printf '  %-10s  %s\n' "$1" "$line"
    while IFS= read -r line; do printf '%14s%s\n' "" "$line"; done
  }
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
  # The steps go on past one that fails, and say so; if Python itself
  # failed, say that plainly too. Either way, as before, it exits 0.
  run undo || warn "the uninstall stopped partway (see above); some of claude-launcher may still be installed"
  exit 0
fi
if [[ -n $icon && $icon != default ]]; then
  icon=$(realpath "$icon")
fi
run run ${terminal:+--terminal "$terminal"} ${icon:+--icon "$icon"}
