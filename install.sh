#!/usr/bin/env bash
# Set up claude-launcher, its Claude Code hook, the Claude Dark Ghostty theme
# and the "Claude Code" desktop launcher. Safe to re-run: it updates whatever
# is already installed, and remembers --terminal and --icon from last time.
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
bin_dir=$HOME/.local/bin
themes_dir=${XDG_CONFIG_HOME:-$HOME/.config}/ghostty/themes
claude_settings=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json

say()  { printf '\e[1;38;2;215;119;87m==>\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33mwarning:\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31merror:\e[0m %s\n' "$*" >&2; exit 1; }

# Link $2 to $1, unless something that isn't a link is already there.
link() {
  if [[ -e $2 && ! -L $2 ]]; then
    warn "$2 exists and isn't a link; leaving it alone"
    return
  fi
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
  say "Linked $2"
}

# Remove $1 if it's a link into this repo.
unlink_ours() {
  if [[ -L $1 && $(readlink "$1") == "$repo"/* ]]; then
    rm -f "$1"
    say "Removed $1"
  fi
}

# Run a Python step, showing its lines as progress; die if it fails.
step() {
  local line
  "$core" "$@" | while IFS= read -r line; do say "$line"; done
  (( PIPESTATUS[0] == 0 )) || die "could not finish: $*"
}

uninstall() {
  unlink_ours "$bin_dir/claude-launcher"
  unlink_ours "$bin_dir/claude-session-hook"
  unlink_ours "$themes_dir/claude-dark"
  step install remove-terminals
  "$core" install remove-icon
  if [[ -f $claude_settings ]]; then
    status=0
    "$core" install remove-hook "$claude_settings" || status=$?
    case $status in
      0) ;;
      4) say "Removed the session hook from $claude_settings" ;;
      *) warn "could not remove the session hook from $claude_settings" ;;
    esac
  fi
  local own_dir
  own_dir=$(dirname "$("$core" install fzf-dir)")
  if [[ -d $own_dir ]]; then
    rm -rf "$own_dir"
    say "Removed $own_dir (the launcher's own fzf, your icon, and its settings)"
  fi
  local state=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/state/claude-launcher
  [[ -d $state ]] && say "Left the launcher's state in $state; remove it with: rm -r '$state'"
  return 0
}

install() {
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "claude-launcher needs Python 3.10 or newer"
  command -v claude >/dev/null || warn "claude is not on your PATH; install Claude Code before using the launcher"

  # 1. fzf 0.67 or newer. If the system's is older (Ubuntu 24.04, Debian),
  # fetch a pinned, checksum-verified one that only the launcher uses.
  read -r fzf_ok fzf_have fzf_path < <("$core" install fzf-status)
  if [[ $fzf_ok == ok ]]; then
    say "Using fzf $fzf_have ($fzf_path)"
  else
    if [[ $fzf_have == none ]]; then
      say "fzf isn't installed; fetching one for claude-launcher to use"
    else
      say "fzf $fzf_have is older than claude-launcher needs; fetching a newer one for it alone (yours stays as it is)"
    fi
    fzf_path=$("$core" install fetch-fzf) || die "could not fetch fzf; install fzf 0.67 or newer, then re-run"
    say "Installed $("$fzf_path" --version | awk '{print $1}') to $fzf_path"
  fi

  # 2. The launcher and its hook: links, so `git pull` updates them.
  link "$repo/bin/claude-launcher" "$bin_dir/claude-launcher"
  link "$repo/bin/claude-session-hook" "$bin_dir/claude-session-hook"
  [[ :$PATH: == *":$bin_dir:"* ]] || warn "$bin_dir is not on your PATH"

  # 3. The hook that lets the launcher list the sessions that were open at a
  # restart, in Claude Code's user settings. Merged in; nothing else changes.
  status=0
  "$core" install add-hook "$claude_settings" "$bin_dir/claude-session-hook" || status=$?
  case $status in
    0) say "The session hook is already in $claude_settings" ;;
    4) say "Added the session hook to $claude_settings (sessions started from now on use it)" ;;
    *) warn "could not add the session hook to $claude_settings" ;;
  esac

  # 4. The theme, so any Ghostty window can use it: theme = claude-dark
  link "$repo/themes/claude-dark" "$themes_dir/claude-dark"

  # 5. The app icon, then the "Claude Code" desktop launcher(s), with a
  # Claude Code profile in terminals that use one. See terminals.py.
  step install icon ${icon:+"$icon"}
  step install terminals "$bin_dir/claude-launcher" ${terminal:+"$terminal"}

  say "Done. Open \"Claude Code\" from your apps, or run claude-launcher in any terminal."
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
if [[ $action == install && -n $icon && $icon != default ]]; then
  icon=$(realpath "$icon")
fi
"$action"
