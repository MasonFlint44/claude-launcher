"""What the launcher needs from the system it runs on.

The picker, the session logs, the restart rule and the colors are the same
everywhere. The parts here are not: each platform (platforms/linux, and any
other) provides them, and `platforms.current()` picks the one this machine
uses. tests/contract checks any platform's parts the same way.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..restart import Reason

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
    why: str  # a Reason's key: "restart", "logout", or one of the platform's own (Platform.reasons)


class EventSource(Protocol):
    """Where the restarts and logouts that close terminals come from."""

    def events(self, record: Record, same_boot: bool) -> list[Event] | None:
        """The restarts and logouts in the record's boot that could have closed
        its Claude (restarts only for an earlier boot), in any order. None if
        the system can't be asked yet; the record is then decided later."""
        ...


class Setup(Protocol):
    """What the installer (install.py) sets up for this platform beyond fzf
    and the hook in settings.json: on Linux, the links in ~/.local/bin, the
    desktop entries, terminal profiles and icon."""

    # What install() sets up, for "could not set up ...", and what to do next.
    what: str
    done: str

    def hook_command(self) -> Path | list[str]:
        """What settings.json runs for the session hook: a script, which
        Claude runs with its shell, or a program and its arguments."""
        ...

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        """Set it up, saying what was done one line at a time ("warning: ..."
        for a problem that doesn't stop it). Raises if it can't finish.
        `terminal_names` and `icon` are the installer's --terminal and --icon."""
        ...

    def uninstall(self) -> Iterator[str]:
        """Take out what install() set up, going on past a part that fails."""
        ...

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        """Commands of the core's own for this platform, by name, such as what
        a desktop entry runs. Each gets its arguments and returns an exit code."""
        ...


@dataclass(frozen=True)
class Platform:
    name: str
    host: Host
    # Made only when needed: reading the event log costs time, and most runs
    # of the core (one per key press) never do.
    events: Callable[[], EventSource]
    # Made only when needed, like events: only install.sh and the platform's
    # own commands use it.
    setup: Callable[[], Setup]
    # fzf's name for this OS in its release files ("linux"), for the build
    # install.sh fetches when the system's fzf is too old.
    fzf_os: str
    # The shell fzf runs key bindings with (its --with-shell, or "" for fzf's
    # own choice), and the core's command line with these arguments, quoted
    # for that shell. Key presses read both from the picker's state, so they
    # never load the platform.
    fzf_shell: str
    core_command: Callable[[list[str]], str]
    # More environment for fzf: on Windows, SHELL=cmd. fzf only quotes its
    # placeholders for cmd when it picks cmd itself; --with-shell would get
    # POSIX quoting.
    fzf_env: Mapping[str, str]
    # Columns fzf leaves unused at the right: 1 on Windows, where writing
    # the console's last column wraps the line.
    columns_lost: int
    # How the picker talks to fzf while it runs: "socket", a Unix socket in
    # the run directory, or "port", a localhost port fzf picks (Windows'
    # Python has no Unix sockets). Either way each request carries the run's
    # FZF_API_KEY.
    fzf_listen: str
    # Where the launcher keeps its own files (the fetched fzf, in bin/).
    data_dir: Callable[[Mapping[str, str]], Path]
    # What else, beyond a restart or a logout (restart.SHARED), this
    # platform's events can say closed Claude: WSL stopping, on WSL.
    reasons: tuple[Reason, ...] = ()
