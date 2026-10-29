"""Linux: /proc for process facts, the systemd journal for restarts and logouts."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path

from ...paths import CORE
from ..base import Platform
from .host import LinuxHost


def _events(host: LinuxHost):
    from .journal import Journal, JournalEvents

    return JournalEvents(Journal(), host.user)


def _setup():
    from .setup import LinuxSetup

    return LinuxSetup()


def data_dir(env: Mapping[str, str]) -> Path:
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or str(Path.home()), ".local", "share")
    return Path(data) / "claude-launcher"


def platform() -> Platform:
    host = LinuxHost()
    return Platform(
        name="linux",
        host=host,
        events=lambda: _events(host),
        setup=_setup,
        fzf_os="linux",
        fzf_shell="bash -c",
        core_command=lambda args: shlex.join([str(CORE), *args]),
        fzf_listen="socket",
        data_dir=data_dir,
    )
