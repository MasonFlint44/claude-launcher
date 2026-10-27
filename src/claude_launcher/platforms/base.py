"""What the launcher needs from the system it runs on.

The picker, the session logs, the restart rule and the colors are the same
everywhere. The parts here are not: each platform (platforms/linux, and any
other) provides them, and `platforms.current()` picks the one this machine
uses. tests/contract checks any platform's parts the same way.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..restart import Record


class Host(Protocol):
    """Facts about this machine and its processes."""

    def boot_id(self) -> str:
        """An ID for this boot, different after every restart. The session
        hook writes the same one into its records."""
        ...

    def boot_start(self, now: float) -> float:
        """When this boot began, as a Unix time."""
        ...

    def claude_running(self, pid: object) -> bool:
        """Whether process `pid` is a Claude Code that's still running."""
        ...

    def user(self) -> str:
        """The user's login name."""
        ...

    def lock(self, path: Path) -> AbstractContextManager[None]:
        """Hold an exclusive lock on `path` (created if missing) for the block.
        Restored tabs can all open the picker at once."""
        ...

    def open_tty(self) -> int | None:
        """A file descriptor for the controlling terminal, or None without one."""
        ...

    def terminal_reply(self, fd: int, query: bytes, done: re.Pattern[bytes], timeout: float) -> bytes | None:
        """Write `query` to the terminal and read its answer, unechoed, until
        `done` matches what came back. None if it hadn't by `timeout` seconds."""
        ...


@dataclass(frozen=True)
class Event:
    """A restart or logout: from when it began to when it was over."""

    start: float
    end: float
    why: str  # "restart" or "logout"


class EventSource(Protocol):
    """Where the restarts and logouts that close terminals come from."""

    def events(self, record: Record, same_boot: bool) -> list[Event] | None:
        """The restarts and logouts in the record's boot that could have closed
        its Claude (restarts only for an earlier boot), in any order. None if
        the system can't be asked yet; the record is then decided later."""
        ...


@dataclass(frozen=True)
class Platform:
    name: str
    host: Host
    # Made only when needed: reading the event log costs time, and most runs
    # of the core (one per key press) never do.
    events: Callable[[], EventSource]
