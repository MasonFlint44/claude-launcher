"""What the installer sets up on Windows: claude-launcher.cmd on PATH, the
"Claude Code" Windows Terminal profile (terminal.py), with Claude Dark, and
the .cmd files those and the session hook run.

Nothing names a python.exe: each .cmd finds Python when it runs (`_RUN`), so
a Python that's upgraded, moved or replaced just works. That costs about 20 ms
for cmd to start (the hook takes about 57 ms, where a fixed python.exe took
36), once at each session's start and end."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import REPO_DIR, THEME_FILE
from ...theme import Theme
from . import terminal

LAUNCHER = REPO_DIR / "bin" / "claude-launcher"
HOOK = REPO_DIR / "bin" / "claude-session-hook.py"
DEFAULT_ICON = REPO_DIR / "share" / "icons" / "claude-launcher-256.png"
_SHIM_MARK = "rem claude-launcher: "
# Runs a Python script with Python 3, found each time: the first python.exe on
# PATH, unless that's the Microsoft Store's stand-in (in WindowsApps, which
# only offers to install Python), then the py launcher, then the stand-in
# after all (whose message says how to get Python). "-" stands for unset:
# cmd can't take part of a variable that isn't set. Checked in a VM with each
# of those on PATH, alone and together.
_RUN = """@echo off
{mark}
setlocal
set "PY=-" & set "ARG=" & set "STUB=-"
for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if /i not "%PY:\\WindowsApps\\=%"=="%PY%" (set "STUB=%PY%" & set "PY=-")
if "%PY%"=="-" for %%P in (py.exe) do if not "%%~$PATH:P"=="" (set "PY=%%~$PATH:P" & set "ARG=-3")
if "%PY%"=="-" set "PY=%STUB%"
if "%PY%"=="-" (echo claude-launcher needs Python 3.10 or newer: https://www.python.org/downloads/ 1>&2 & exit /b 9009)
"%PY%" %ARG% {script} %*
"""


def _bin_dir(env: Mapping[str, str] = os.environ) -> Path:
    """Where Claude Code's own installer puts claude.exe, and adds to PATH."""
    return Path(env.get("USERPROFILE") or str(Path.home())) / ".local" / "bin"


def _cmd_path(path: Path) -> str:
    """A path in a .cmd: quoted, with % doubled so cmd doesn't expand it."""
    return '"' + str(path).replace("%", "%%") + '"'


def run_cmd(script: str) -> str:
    """A .cmd that runs `script` (Python's options and the script) with
    Python 3. Marked as ours, so uninstall only removes ours."""
    text = _RUN.format(mark=f"{_SHIM_MARK}{str(REPO_DIR).replace('%', '%%')}", script=script)
    return text.replace("\n", "\r\n")


class WindowsSetup:
    what = "the Windows Terminal profile"
    done = 'Restart Windows Terminal and open a "Claude Code" tab, or run claude-launcher in any terminal.'

    def __init__(self, data_dir: Path, env: Mapping[str, str] = os.environ):
        self.data_dir = data_dir
        self.env = env

    @property
    def _hook_cmd(self) -> Path:
        return self.data_dir / "claude-session-hook.cmd"  # the name tells settings.py it's ours

    @property
    def _launcher_cmd(self) -> Path:
        return self.data_dir / "claude-launcher.cmd"

    def hook_command(self) -> list[str]:
        """cmd running the hook's .cmd, with nothing after it but "start" or
        "end": cmd /c keeps one quoted path's quotes, but not more than one."""
        system = os.path.join(self.env.get("SystemRoot", "C:\\Windows"), "System32", "cmd.exe")
        comspec = self.env.get("ComSpec") or system
        return [comspec, "/d", "/c", str(self._hook_cmd)]

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        if terminal_names:
            yield "warning: --terminal is for Linux; on Windows the launcher opens in Windows Terminal"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        launcher = run_cmd(_cmd_path(LAUNCHER))
        self._hook_cmd.write_text(run_cmd("-I " + _cmd_path(HOOK)), encoding="utf-8", newline="")
        self._launcher_cmd.write_text(launcher, encoding="utf-8", newline="")
        shim = _bin_dir(self.env) / "claude-launcher.cmd"
        if shim.exists() and _SHIM_MARK not in shim.read_text(encoding="utf-8", errors="replace"):
            yield f"warning: {shim} isn't one of ours; leaving it alone"
        else:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text(launcher, encoding="utf-8", newline="")
            yield f"Wrote {shim}"
        path = os.environ.get("PATH", "").lower().split(os.pathsep)
        if str(shim.parent).lower() not in path:
            yield f"warning: {shim.parent} is not on your PATH"
        shown = self._icon(icon)
        written = terminal.write(
            terminal.fragments_dir(self.env),
            _cmd_path(self._launcher_cmd),
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
