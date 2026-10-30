"""Restarts and logoffs in WSL. A record's boot is WSL's, which ends when
Windows restarts or logs off, and also when WSL itself stops (wsl --shutdown,
or WSL stopping when idle). All three close Claude:

- Windows' restarts and logoffs, from its System log through interop
  (platforms/windows/eventlog.py), as on native Windows.
- WSL stopping on its own ("wsl-stop"), at this WSL boot's start:
  Claude was killed, so its record has no end, and a record without one is
  closed by the first event after it started. A tab closed before then has
  its end, long before this, and isn't counted.
"""

from __future__ import annotations

import time

from ..base import Event
from ..windows.eventlog import EventLog, WindowsEvents


class WslEvents:
    def __init__(self, host, windows: WindowsEvents | None = None, now=time.time):
        self.host = host
        self.windows = windows or WindowsEvents(EventLog())
        self.now = now

    def events(self, record, same_boot: bool) -> list[Event] | None:
        # Windows' boots aren't WSL's: whether Windows restarted after the
        # record started is for the event log to say.
        found = self.windows.events(record, same_boot=False)
        if same_boot:
            return found
        stopped = self.host.boot_start(self.now())
        return [*(found or []), Event(stopped, stopped, "wsl-stop")]
