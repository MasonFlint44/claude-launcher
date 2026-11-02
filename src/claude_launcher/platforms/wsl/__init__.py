"""WSL: Linux (its /proc, flock and bash hook), with Windows' restarts and
logoffs, read through interop (events.py), and a Windows Terminal profile
for the distro (setup.py)."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping

from ..base import Platform, Reason


def is_wsl(release: str | None = None, root: str = "/", env: Mapping[str, str] = os.environ) -> bool:
    """WSL's own files (`wsl_files`) say so, whatever the kernel (one from
    .wslconfig needn't say "microsoft"); a container on WSL's kernel (Docker
    Desktop's) doesn't have them. WSL_DISTRO_NAME (not under sudo) counts
    too, but only with WSL's kernel: a container can be given it."""
    from ..linux.host import wsl_files

    if wsl_files(os.path.join(root, "proc")):
        return True
    release = release if release is not None else os.uname().release
    return bool(env.get("WSL_DISTRO_NAME")) and "microsoft" in release.lower()


def _events(host):
    from .events import WslEvents

    return WslEvents(host)


def _setup():
    from .setup import WslSetup

    return WslSetup()


def platform() -> Platform:
    from ..linux import platform as linux

    base = linux()
    stopped = Reason("wsl-stop", "WSL stopped", "WSL stop", 30)  # after a Windows restart or logoff, which stop it too
    return dataclasses.replace(
        base, name="wsl", events=lambda: _events(base.host), setup=_setup, reasons=(*base.reasons, stopped)
    )
