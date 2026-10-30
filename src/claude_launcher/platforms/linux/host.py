"""Linux's facts about this machine: /proc, flock, the controlling terminal."""

from __future__ import annotations

import fcntl
import os
import pwd
import re
import select
import termios
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def is_claude(argv: list[str]) -> bool:
    """Whether a command line is Claude Code's: the native `claude` binary, or
    the npm package run by node (whose process name is just "MainThread").
    claude-session-hook has the same test in bash."""
    if not argv:
        return False
    if os.path.basename(argv[0]) == "claude":
        return True
    return os.path.basename(argv[0]).startswith("node") and any(
        os.path.basename(a) == "claude" or "/@anthropic-ai/claude-code/" in a for a in argv[1:3]
    )


# How many hex digits of the boot ID a shared kernel's boot keeps; the rest
# are pid 1's start, in clock ticks (12 hex digits: millennia at 100 Hz).
# The whole stays 32 hex digits, the size of any other boot ID.
_KEPT = 20
# How long after its record's start a Claude may have started (the hook runs
# once Claude is up).
_SAME_PROCESS = 10


class LinuxHost:
    def __init__(self, proc: str = "/proc"):
        self.proc = proc  # another for tests

    def _read(self, name: str) -> str:
        with open(f"{self.proc}/{name}") as f:
            return f.read()

    def _init_start(self) -> int | None:
        """When pid 1 started, in clock ticks since the kernel did, under WSL's
        kernel; else None. Every WSL distro (and Docker Desktop's containers)
        shares one VM and so its boot ID and uptime, but a distro can stop
        and start again (wsl --terminate) while the VM runs on: its own
        start is its pid 1's. claude-session-hook does the same."""
        try:
            if "microsoft" not in self._read("sys/kernel/osrelease").lower():
                return None
            fields = self._read("1/stat").rpartition(") ")[2].split()
            return int(fields[19])  # field 22, starttime; the first after "(comm) " is 3
        except (OSError, IndexError, ValueError):
            return None

    def boot_id(self) -> str:
        boot = self._read("sys/kernel/random/boot_id").strip()
        start = self._init_start()
        if start is None:
            return boot
        return f"{boot.replace('-', '')[:_KEPT]}{start:012x}"

    def boot_start(self, now: float) -> float:
        start = now - float(self._read("uptime").split()[0])
        ticks = self._init_start()
        return start if ticks is None else start + ticks / os.sysconf("SC_CLK_TCK")

    def claude_running(self, pid: object, started: float | None = None) -> bool:
        if not isinstance(pid, int):
            return False
        try:
            with open(f"{self.proc}/{pid}/cmdline", "rb") as f:
                argv = f.read().decode(errors="replace").split("\0")
        except OSError:
            return False
        if not is_claude([a for a in argv if a]):
            return False
        # A Claude that started well after the record did got the pid since.
        began = self._process_start(pid)
        return started is None or began is None or began <= started + _SAME_PROCESS

    def _process_start(self, pid: int) -> float | None:
        """When process `pid` started, as a Unix time."""
        try:
            ticks = int(self._read(f"{pid}/stat").rpartition(") ")[2].split()[19])
            uptime = float(self._read("uptime").split()[0])
        except (OSError, IndexError, ValueError):
            return None
        return time.time() - uptime + ticks / os.sysconf("SC_CLK_TCK")

    def user(self) -> str:
        return pwd.getpwuid(os.getuid()).pw_name

    @contextmanager
    def lock(self, path: Path) -> Iterator[None]:
        with open(path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def open_tty(self) -> int | None:
        try:
            return os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError:
            return None

    def terminal_reply(self, fd: int, query: bytes, done: re.Pattern[bytes], timeout: float) -> bytes | None:
        # Without echo or line buffering, so the replies can be read as they
        # come and aren't shown.
        old = termios.tcgetattr(fd)
        new = list(old)
        new[3] &= ~(termios.ECHO | termios.ICANON)
        new[6] = list(new[6])
        new[6][termios.VMIN], new[6][termios.VTIME] = 0, 0
        termios.tcsetattr(fd, termios.TCSANOW, new)
        try:
            os.write(fd, query)
            buf = b""
            end = time.monotonic() + timeout
            while not done.search(buf):
                left = end - time.monotonic()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    return None
                chunk = os.read(fd, 4096)
                if not chunk:
                    return None
                buf += chunk
            return buf
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
