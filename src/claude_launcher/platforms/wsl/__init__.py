"""WSL: Linux (its /proc, flock and bash hook), with Windows' restarts and
logoffs, read through interop (events.py), and a Windows Terminal profile
for the distro (setup.py)."""

from __future__ import annotations

import dataclasses
import os

from ..base import Platform


def is_wsl(release: str | None = None) -> bool:
    """WSL's kernels say so: 6.6.87.2-microsoft-standard-WSL2, or WSL 1's 4.4.0-19041-Microsoft."""
    return "microsoft" in (release if release is not None else os.uname().release).lower()


def _events(host):
    from .events import WslEvents

    return WslEvents(host)


def _setup():
    from .setup import WslSetup

    return WslSetup()


def platform() -> Platform:
    from ..linux import platform as linux

    base = linux()
    return dataclasses.replace(base, name="wsl", events=lambda: _events(base.host), setup=_setup)
