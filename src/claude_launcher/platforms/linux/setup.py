"""What the installer sets up on Linux: claude-launcher and its hook in
~/.local/bin, the Claude Dark theme for Ghostty, the app icon, and the
"Claude Code" desktop entries (with terminal profiles where a terminal needs
one). See desktop.py and terminals.py."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import CORE, REPO_DIR, THEME_FILE
from . import desktop, terminals


def _context(launcher: str | Path | None = None) -> terminals.Context:
    path = Path(launcher) if launcher else Path(shutil.which("claude-launcher") or REPO_DIR / "bin" / "claude-launcher")
    return terminals.Context.from_env(launcher=path, core=CORE)


def _ghostty_themes(env: Mapping[str, str] = os.environ) -> Path:
    config = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or str(Path.home()), ".config")
    return Path(config) / "ghostty" / "themes"


def bin_dir(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("HOME") or str(Path.home())) / ".local" / "bin"


def link(target: Path, path: Path) -> Iterator[str]:
    """Link `path` to `target`, unless something that isn't a link is there."""
    if path.exists() and not path.is_symlink():
        yield f"warning: {path} exists and isn't a link; leaving it alone"
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    path.symlink_to(target)
    yield f"Linked {path}"


def unlink_ours(link: Path) -> Iterator[str]:
    """Remove `link` if it points into this repo, by whatever path: older
    installs linked to the repo as reached through a symlink to it."""
    if not link.is_symlink():
        return
    target = os.readlink(link)
    if target.startswith(str(REPO_DIR) + os.sep) or Path(os.path.realpath(link)).is_relative_to(REPO_DIR):
        link.unlink()
        yield f"Removed {link}"


COMMANDS = ("claude-launcher", "claude-session-hook")


def link_commands(env: Mapping[str, str] = os.environ) -> Iterator[str]:
    """The launcher and its hook in ~/.local/bin: links, so `git pull` updates them."""
    commands_dir = bin_dir(env)
    for name in COMMANDS:
        yield from link(REPO_DIR / "bin" / name, commands_dir / name)
    if str(commands_dir) not in env.get("PATH", "").split(os.pathsep):
        yield f"warning: {commands_dir} is not on your PATH"


def unlink_commands(env: Mapping[str, str] = os.environ) -> Iterator[str]:
    for name in COMMANDS:
        yield from unlink_ours(bin_dir(env) / name)


class LinuxSetup:
    what = "the desktop launcher"
    done = 'Open "Claude Code" from your apps, or run claude-launcher in any terminal.'

    def hook_command(self) -> Path:
        """What settings.json runs: the hook's link, so the repo can move."""
        return bin_dir() / "claude-session-hook"

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        yield from link_commands()
        # The theme, so any Ghostty window can use it: theme = claude-dark
        yield from link(THEME_FILE, _ghostty_themes() / "claude-dark")
        ctx = _context(bin_dir() / "claude-launcher")
        yield desktop.install_icon(ctx, icon)
        names = [n for n in terminal_names.split(",") if n] if terminal_names else None
        yield from desktop.install_terminals(ctx, names)

    def check(self) -> None:
        """Nothing to check before installing here."""

    def uninstall(self) -> Iterator[str]:
        """Takes out what install() set up, going on past a part that fails,
        for any reason: what's left is harder to find later."""
        yield from unlink_commands()
        yield from unlink_ours(_ghostty_themes() / "claude-dark")
        ctx = _context()
        try:
            for path in desktop.remove_terminals(ctx):
                yield f"Removed {path}"
        except Exception as e:
            yield f"warning: could not remove every desktop entry and profile: {e}"
        try:
            desktop.remove_icon(ctx)
        except Exception as e:
            yield f"warning: could not remove the app icon: {e}"

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {"terminal": _terminal}


def _terminal(args: list[str]) -> int:
    """terminal open NAME [LAUNCHER]: what a drop-down terminal's desktop entry
    runs. terminal list: the names --terminal takes, for install.sh --help."""
    if args == ["list"]:
        print(" ".join(terminals.TERMINALS))
        return 0
    term = terminals.TERMINALS.get(args[1]) if len(args) > 1 else None
    if args[:1] != ["open"] or not isinstance(term, terminals.Guake):
        print(f"can't open {' '.join(args[1:])}", file=sys.stderr)
        return 2
    term.open(_context(args[2] if len(args) > 2 else None))
    return 0
