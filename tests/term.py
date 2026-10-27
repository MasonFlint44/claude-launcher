"""A pseudo-terminal for end-to-end tests: run a program, send keys, read the screen."""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time

import pyte

KEYS = {
    "enter": b"\r",
    "esc": b"\x1b",
    "tab": b"\t",
    "btab": b"\x1b[Z",
    "down": b"\x1b[B",
    "up": b"\x1b[A",
    "ctrl-c": b"\x03",
    "ctrl-d": b"\x04",
    "ctrl-s": b"\x13",
    "ctrl-u": b"\x15",
    "ctrl-x": b"\x18",
    "alt-x": b"\x1bx",
}


class Term:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: str, cols: int = 110, rows: int = 30):
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = bytearray()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        self.status: int | None = None

    def _pump(self, timeout: float) -> None:
        r, _, _ = select.select([self.fd], [], [], timeout)
        if r:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if data:
                self.raw += data
                self.stream.feed(data)
                self._answer(data)
                return
        if self.status is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(status)

    def _answer(self, data: bytes) -> None:
        """Answer what a terminal is asked: the cursor position (fzf), and
        color queries and device attributes (the launcher's color switch)."""
        if b"\x1b[6n" in data:
            os.write(self.fd, b"\x1b[1;1R")
        for slot in re.findall(rb"\x1b\]((?:4;\d+)|1\d);\?(?:\x07|\x1b\\)", data):
            os.write(self.fd, b"\x1b]" + slot + b";rgb:3030/0a0a/2424\x1b\\")
        if b"\x1b[c" in data:
            os.write(self.fd, b"\x1b[?62;22c")

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display)

    def wait_for(self, needle: str, timeout: float = 5.0) -> str:
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.text():
                return self.text()
            self._pump(0.05)
        raise AssertionError(f"{needle!r} not on screen after {timeout}s:\n{self.text()}")

    def wait_gone(self, needle: str, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if needle not in self.text():
                return
            self._pump(0.05)
        raise AssertionError(f"{needle!r} still on screen after {timeout}s:\n{self.text()}")

    def settle(self, seconds: float = 0.3) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._pump(0.02)

    def send(self, *keys: str) -> None:
        for key in keys:
            os.write(self.fd, KEYS.get(key, key.encode()))
            self.settle(0.15)

    def type(self, text: str, gap: float = 0.05) -> None:
        """Type one key at a time, a fast typist's pace. Writing the text in one
        go would be typing at thousands of keys a second: fzf would handle each
        key in turn, with the screen far behind."""
        for ch in text:
            os.write(self.fd, ch.encode())
            self.settle(gap)
        self.settle(0.3)

    def paste(self, text: str) -> None:
        """Paste the way terminals do, in bracketed-paste markers. fzf takes the
        whole paste as one edit."""
        os.write(self.fd, b"\x1b[200~" + text.encode() + b"\x1b[201~")
        self.settle(0.3)

    def wait_exit(self, timeout: float = 5.0) -> int:
        end = time.time() + timeout
        while time.time() < end and self.status is None:
            self._pump(0.05)
        if self.status is None:
            raise AssertionError(f"still running after {timeout}s:\n{self.text()}")
        return self.status

    def close(self) -> None:
        if self.status is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except OSError:
                pass
        os.close(self.fd)
