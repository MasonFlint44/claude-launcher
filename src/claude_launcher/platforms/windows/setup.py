"""What the installer sets up on Windows: claude-launcher.cmd on PATH, and the
"Claude Code" Windows Terminal profile (terminal.py), with Claude Dark."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import REPO_DIR, THEME_FILE
from ...theme import Theme
from . import terminal

LAUNCHER = REPO_DIR / "bin" / "claude-launcher"
HOOK = REPO_DIR / "bin" / "claude-session-hook.py"
DEFAULT_ICON = REPO_DIR / "share" / "icons" / "claude-launcher-256.png"
_SHIM_MARK = "rem claude-launcher: "


def _bin_dir(env: Mapping[str, str] = os.environ) -> Path:
    """Where Claude Code's own installer puts claude.exe, and adds to PATH."""
    return Path(env.get("USERPROFILE") or str(Path.home())) / ".local" / "bin"


def launcher_command(python: str = sys.executable) -> str:
    return f'"{python}" "{LAUNCHER}"'


def _shim(python: str) -> str:
    # Marked as ours, so uninstall only removes this one.
    return f"@echo off\r\n{_SHIM_MARK}{REPO_DIR}\r\n{launcher_command(python)} %*\r\n"


class WindowsSetup:
    what = "the Windows Terminal profile"
    done = 'Restart Windows Terminal and open a "Claude Code" tab, or run claude-launcher in any terminal.'

    def __init__(self, data_dir: Path, env: Mapping[str, str] = os.environ, python: str = sys.executable):
        self.data_dir = data_dir
        self.env = env
        self.python = python

    def hook_command(self) -> list[str]:
        """Claude runs Python on the hook directly: no shell to start."""
        return [self.python, "-I", str(HOOK)]

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        if terminal_names:
            yield "warning: --terminal is for Linux; on Windows the launcher opens in Windows Terminal"
        shim = _bin_dir(self.env) / "claude-launcher.cmd"
        if shim.exists() and _SHIM_MARK not in shim.read_text(encoding="utf-8", errors="replace"):
            yield f"warning: {shim} isn't one of ours; leaving it alone"
        else:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text(_shim(self.python), encoding="utf-8", newline="")
            yield f"Wrote {shim}"
        path = os.environ.get("PATH", "").lower().split(os.pathsep)
        if str(shim.parent).lower() not in path:
            yield f"warning: {shim.parent} is not on your PATH"
        shown = self._icon(icon)
        written = terminal.write(
            terminal.fragments_dir(self.env),
            launcher_command(self.python),
            "%USERPROFILE%",
            shown,
            Theme.load(THEME_FILE),
        )
        yield f'Added the "Claude Code" profile to Windows Terminal ({written.parent})'

    def _icon(self, icon: str | None) -> Path:
        """The icon to show: --icon's (kept, so it can move), last time's, or ours."""
        kept = self.data_dir / "icon.png"
        if icon == "default":
            kept.unlink(missing_ok=True)
        elif icon:
            if not icon.lower().endswith(".png"):
                raise ValueError("on Windows, --icon takes a .png")
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon, kept)
        return kept if kept.is_file() else DEFAULT_ICON

    def uninstall(self) -> Iterator[str]:
        shim = _bin_dir(self.env) / "claude-launcher.cmd"
        if shim.is_file() and _SHIM_MARK in shim.read_text(encoding="utf-8", errors="replace"):
            shim.unlink()
            yield f"Removed {shim}"
        directory = terminal.fragments_dir(self.env)
        if terminal.remove(directory):
            yield f'Removed the "Claude Code" Windows Terminal profile ({directory})'

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {}
