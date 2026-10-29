"""Installing and uninstalling, for install.sh and install.ps1, which check
for Python and then hand over to this. The steps are the same everywhere:

1. fzf 0.67 or newer: the system's, else a pinned one fetched for the
   launcher alone (fzf.py).
2. The session hook, merged into Claude's settings.json (settings.py).
3. What the platform adds: the command on PATH, and the "Claude Code" app or
   terminal profile (the platform's Setup).

Each yields what it did, one line at a time: plain, "warning: ..." for a
problem that doesn't stop it, or "error: ..." for one that does, last.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from . import fzf, settings
from .paths import Paths

FAILURES = (ValueError, RuntimeError, OSError, subprocess.SubprocessError)


def _fzf(platform) -> Iterator[str]:
    own = fzf.private_dir(platform.data_dir(os.environ))
    path, version = fzf.find(own)
    if path:
        yield f"Using fzf {fzf.show(version)} ({path})"
        return
    if version is None:
        yield "fzf isn't installed; fetching one for claude-launcher to use"
    else:
        yield (
            f"fzf {fzf.show(version)} is older than claude-launcher needs; "
            "fetching a newer one for it alone (yours stays as it is)"
        )
    try:
        path = fzf.fetch(own, system=platform.fzf_os)
    except (*FAILURES, KeyError) as e:
        yield f"warning: {e}"
        yield "error: could not fetch fzf; install fzf 0.67 or newer, then re-run"
        return
    yield f"Installed {fzf.show(fzf.version(str(path)))} to {path}"


def _hook(setup, settings_path: Path) -> Iterator[str]:
    try:
        changed = settings.add_hook(settings_path, setup.hook_command())
    except (OSError, ValueError) as e:
        yield f"warning: could not add the session hook to {settings_path}: {e}"
        return
    if changed:
        yield f"Added the session hook to {settings_path} (sessions started from now on use it)"
    else:
        yield f"The session hook is already in {settings_path}"


def install(platform, terminal_names: str | None = None, icon: str | None = None) -> Iterator[str]:
    if not shutil.which("claude"):
        yield "warning: claude is not on your PATH; install Claude Code before using the launcher"
    for line in _fzf(platform):
        yield line
        if line.startswith("error: "):
            return
    setup = platform.setup()
    yield from _hook(setup, Paths.from_env().config / "settings.json")
    try:
        yield from setup.install(terminal_names, icon)
    except FAILURES as e:
        yield f"warning: {e}"
        yield f"error: could not set up {setup.what}"
        return
    yield f"Done. {setup.done}"


def uninstall(platform) -> Iterator[str]:
    """Takes everything out again, going on past a step that fails."""
    setup = platform.setup()
    try:
        yield from setup.uninstall()
    except FAILURES as e:
        yield f"warning: could not remove everything the installer set up: {e}"
    paths = Paths.from_env()
    settings_path = paths.config / "settings.json"
    if settings_path.is_file():
        try:
            if settings.remove_hook(settings_path):
                yield f"Removed the session hook from {settings_path}"
        except (OSError, ValueError) as e:
            yield f"warning: could not remove the session hook from {settings_path}: {e}"
    own = platform.data_dir(os.environ)
    if own.is_dir():
        shutil.rmtree(own, ignore_errors=True)
        yield f"Removed {own} (the launcher's own fzf, your icon, and its settings)"
    if paths.state.is_dir():
        yield f"Left the launcher's state in {paths.state}; delete that directory to remove it"
