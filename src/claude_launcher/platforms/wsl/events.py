"""Restarts and logoffs in WSL. A record's boot is WSL's, which ends when
Windows restarts or logs off, and also when WSL itself stops (wsl --shutdown,
or WSL stopping when idle). All three close Claude:

- Windows' restarts and logoffs, from its System log through interop
  (platforms/windows/eventlog.py), as on native Windows.
- WSL, or this distro, stopping on its own ("wsl-stop"), at this distro's
  start (the host's boot_start: see platforms/linux/host.py):
  Claude was killed, so its record has no end, and a record without one is
  closed by the first event after it started. A tab closed before then has
  its end, long before this, and isn't counted.

Without interop (System32 isn't found, or Windows' programs don't start),
only WSL stopping is known, and nothing waits for Windows' log. With
interop, a query that failed or took too long is as on native Windows:
the record waits (None), except one with no end from an earlier boot,
which WSL stopping closed either way.

A record from two or more of the distro's boots back (the picker wasn't
opened in between) gets this boot's start too, though its WSL stopped
earlier: the stop itself leaves no trace (WSL kills everything, the hook
included), and the record knows only its own start. So its ↻ day can be
later than the stop, and it stays in the group up to that much longer
than KEEP; nothing is lost. A Windows restart or logoff in between is
still found at its own time, while the System log reaches back that far
(boot_lost: as on native Windows).
"""

from __future__ import annotations

import time

from ..base import Event
from ..windows.eventlog import EventLog, WindowsEvents
from .interop import Interop


class WslEvents:
    def __init__(self, host, windows: WindowsEvents | None = None, now=time.time, interop: Interop | None = None):
        self.host = host
        if windows is None:
            # By full path: wevtutil.exe and whoami.exe may not be on PATH.
            interop = interop or Interop()
            windows = WindowsEvents(EventLog(run=interop.wevtutil), sid=interop.user_sid)
        self.windows = windows
        # What tells "no interop" from "the query failed". Without one
        # (tests' own Windows events), there's no interop.
        self.interop = interop
        self.now = now
        self._stopped: float | None = None

    def events(self, record, same_boot: bool) -> list[Event] | None:
        # Windows' boots aren't WSL's: whether Windows restarted after the
        # record started is for the event log to say.
        found = self.windows.events(record, same_boot=False)
        if same_boot:
            return found
        # A tab closed by hand has an end; one WSL stopping killed has none,
        # and WSL stopping closed it whatever Windows' log would say.
        if found is None and self._interop_there() and record.ended is not None:
            return None
        if self._stopped is None:  # once: every record from an earlier boot ended here
            self._stopped = self.host.boot_start(self.now())
        stopped = self._stopped
        return [*(found or []), Event(stopped, stopped, "wsl-stop")]

    def boot_lost(self, record) -> bool | None:
        # A lost log matters only for Windows' restarts and logoffs. Windows
        # out of reach (no interop) isn't a reason to wait; a query that
        # failed is.
        lost = self.windows.boot_lost(record)
        if lost is None and self._interop_there():
            return None
        return bool(lost)

    def _interop_there(self) -> bool:
        """Asked after a query failed, so a Windows program that couldn't
        start has already said so."""
        return self.interop is not None and self.interop.reachable()
