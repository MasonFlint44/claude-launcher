"""What the installer sets up on Linux: claude-launcher and its hook in
~/.local/bin, the Claude Dark theme for Ghostty, the app icon, and the
"Claude Code" desktop entries (with terminal profiles where a terminal needs
one). See desktop.py and terminals.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import CORE, REPO_DIR, THEME_FILE
from . import desktop, terminals

# What a step that fails raises; the installer shows the message.
FAILURES = (ValueError, RuntimeError, OSError, subprocess.SubprocessError)


def _context(launcher: str | Path | None = None) -> terminals.Context:
    path = Path(launcher) if launcher else Path(shutil.which("claude-launcher") or REPO_DIR / "bin" / "claude-launcher")
    return terminals.Context.from_env(launcher=path, core=CORE)


def _ghostty_themes(env: Mapping[str, str] = os.environ) -> Path:
    config = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or str(Path.home()), ".config")
    return Path(config) / "ghostty" / "themes"


def _bin_dir(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("HOME") or str(Path.home())) / ".local" / "bin"


def _link(target: Path, link: Path) -> Iterator[str]:
    """Link `link` to `target`, unless something that isn't a link is there."""
    if link.exists() and not link.is_symlink():
        yield f"warning: {link} exists and isn't a link; leaving it alone"
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    yield f"Linked {link}"


def _unlink_ours(link: Path) -> Iterator[str]:
    if link.is_symlink() and os.readlink(link).startswith(str(REPO_DIR) + os.sep):
        link.unlink()
        yield f"Removed {link}"


_COMMANDS = ("claude-launcher", "claude-session-hook")


class LinuxSetup:
    what = "the desktop launcher"
    done = 'Open "Claude Code" from your apps, or run claude-launcher in any terminal.'

    def hook_command(self) -> Path:
        """What settings.json runs: the hook's link, so the repo can move."""
        return _bin_dir() / "claude-session-hook"

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        # The launcher and its hook: links, so `git pull` updates them.
        bin_dir = _bin_dir()
        for name in _COMMANDS:
            yield from _link(REPO_DIR / "bin" / name, bin_dir / name)
        if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
            yield f"warning: {bin_dir} is not on your PATH"
        # The theme, so any Ghostty window can use it: theme = claude-dark
        yield from _link(THEME_FILE, _ghostty_themes() / "claude-dark")
        ctx = _context(bin_dir / "claude-launcher")
        yield desktop.install_icon(ctx, icon)
        names = [n for n in terminal_names.split(",") if n] if terminal_names else None
        yield from desktop.install_terminals(ctx, names)

    def uninstall(self) -> Iterator[str]:
        """Takes out what install() set up, going on past a part that fails."""
        for name in _COMMANDS:
            yield from _unlink_ours(_bin_dir() / name)
        yield from _unlink_ours(_ghostty_themes() / "claude-dark")
        ctx = _context()
        try:
            for path in desktop.remove_terminals(ctx):
                yield f"Removed {path}"
        except FAILURES as e:
            yield f"warning: could not remove every desktop entry and profile: {e}"
        try:
            desktop.remove_icon(ctx)
        except FAILURES as e:
            yield f"warning: could not remove the app icon: {e}"

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {"terminal": _terminal}


def _terminal(args: list[str]) -> int:
    """terminal open NAME [LAUNCHER]: what a drop-down terminal's desktop entry runs."""
    term = terminals.TERMINALS.get(args[1]) if len(args) > 1 else None
    if args[:1] != ["open"] or not isinstance(term, terminals.Guake):
        print(f"can't open {' '.join(args[1:])}", file=sys.stderr)
        return 2
    term.open(_context(args[2] if len(args) > 2 else None))
    return 0
