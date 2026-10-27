"""Linux: /proc for process facts, the systemd journal for restarts and logouts."""

from __future__ import annotations

from ..base import Platform
from .host import LinuxHost


def _events(host: LinuxHost):
    from .journal import Journal, JournalEvents

    return JournalEvents(Journal(), host.user)


def platform() -> Platform:
    host = LinuxHost()
    return Platform(name="linux", host=host, events=lambda: _events(host))
