#!/usr/bin/env bash
# Runs inside a distro image as `tester`, with the repo mounted read-only at
# /src. Installs as a new user would, checks what install.sh set up, runs the
# test suite on the distro's own Python, then uninstalls and checks it's gone.
set -euo pipefail

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

# A copy, so pytest and the installer can write next to the code.
repo=$HOME/claude-launcher
mkdir -p "$repo"
tar -C /src --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=.ruff_cache --exclude=tests/docker/.work -cf - . | tar -C "$repo" -xf -
core=$repo/libexec/claude-launcher-core
export PATH=$HOME/.local/bin:$PATH

step "Versions"
# shellcheck source=/dev/null
. /etc/os-release
system_fzf=$(command -v fzf >/dev/null && fzf --version | awk '{print $1}' || echo none)
systemd_v=$( (systemctl --version 2>/dev/null || true) | awk 'NR==1{print $2}')
printf 'distro  %s\npython  %s\nbash    %s\nfzf     %s\nsystemd %s\n' \
  "$PRETTY_NAME" "$(python3 -c 'import platform; print(platform.python_version())')" \
  "${BASH_VERSION%%(*}" "$system_fzf" "${systemd_v:-none}"

step "install.sh"
"$repo/install.sh" --terminal default
[[ -L $HOME/.local/bin/claude-launcher ]] || fail "claude-launcher isn't linked"
[[ -L $HOME/.local/bin/claude-session-hook ]] || fail "claude-session-hook isn't linked"
grep -q claude-session-hook "$HOME/.claude/settings.json" || fail "the hook isn't in settings.json"
entry=$HOME/.local/share/applications/io.github.masonflint44.ClaudeLauncher.desktop
[[ -f $entry ]] || fail "no desktop entry"
desktop-file-validate "$entry" || fail "the desktop entry doesn't validate"
read -r fzf_ok fzf_have fzf_path < <("$core" install fzf-status)
[[ $fzf_ok == ok ]] || fail "fzf still not usable after install ($fzf_have)"
own_fzf=$(dirname "$("$core" install fzf-dir)")
if [[ $fzf_path == "$own_fzf"/* ]]; then
  echo "using the launcher's own fzf $fzf_have (system: $system_fzf)"
else
  echo "using the system's fzf $fzf_have"
fi
"$repo/install.sh" --terminal default >/dev/null || fail "a second install.sh failed"

step "pytest"
# The tests run whichever fzf comes first, so put the launcher's first.
PATH=$(dirname "$fzf_path"):$PATH /opt/venv/bin/python -m pytest -q -p no:cacheprovider "$repo"

step "install.sh --uninstall"
"$repo/install.sh" --uninstall
[[ ! -e $HOME/.local/bin/claude-launcher ]] || fail "claude-launcher is still linked"
[[ ! -e $entry ]] || fail "the desktop entry is still there"
! grep -q claude-session-hook "$HOME/.claude/settings.json" || fail "the hook is still in settings.json"
[[ ! -e $own_fzf ]] || fail "$own_fzf is still there"

printf '\nPASS %s\n' "$PRETTY_NAME"
