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


class LinuxHost:
    def boot_id(self) -> str:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()

    def boot_start(self, now: float) -> float:
        with open("/proc/uptime") as f:
            return now - float(f.read().split()[0])

    def claude_running(self, pid: object) -> bool:
        if not isinstance(pid, int):
            return False
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().decode(errors="replace").split("\0")
        except OSError:
            return False
        return is_claude([a for a in argv if a])

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
