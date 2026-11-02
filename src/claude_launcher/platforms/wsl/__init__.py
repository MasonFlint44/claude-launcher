"""WSL: Linux (its /proc, flock and bash hook), with Windows' restarts and
logoffs, read through interop (events.py), and a Windows Terminal profile
for the distro (setup.py)."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping

from ..base import Platform, Reason

# What WSL puts in a distro and not in a container on its kernel (Docker
# Desktop's, a devcontainer's): its interop's directory, and the binfmt entry
# that runs Windows programs (WSLInterop-late when systemd cleared the first).
_MARKERS = ("run/WSL", "proc/sys/fs/binfmt_misc/WSLInterop", "proc/sys/fs/binfmt_misc/WSLInterop-late")


def is_wsl(release: str | None = None, root: str = "/", env: Mapping[str, str] = os.environ) -> bool:
    """WSL's kernels say so (6.6.87.2-microsoft-standard-WSL2, or WSL 1's
    4.4.0-19041-Microsoft), but so does a container on Docker Desktop; a
    distro also has WSL's own files, or (not under sudo) WSL_DISTRO_NAME."""
    if "microsoft" not in (release if release is not None else os.uname().release).lower():
        return False
    return bool(env.get("WSL_DISTRO_NAME")) or any(os.path.exists(os.path.join(root, m)) for m in _MARKERS)


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
