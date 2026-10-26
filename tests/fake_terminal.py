"""A terminal's color handling, on the far side of a pty, for theme tests."""

from __future__ import annotations

import contextlib
import os
import pty
import re
import threading

_SEQ = re.compile(rb"\x1b\]([^\x07\x1b]*)(?:\x07|\x1b\\)|\x1b\[c")
DEFAULTS = {"4;1": "#cc0000", "4;8": "#555555", "10": "#eeeeec", "11": "#300a24", "12": "#bbbbbb"}


class FakeTerminal:
    """Answers color queries (as 16-bit rgb:, like xterm), sets and resets
    colors, and answers the device-attributes request.

    answers: whether it reports colors at all. reset_dynamic: whether it has
    the OSC 110-119 resets (Konsole doesn't). da1: whether it answers the
    device-attributes request.
    """

    def __init__(self, answers=True, reset_dynamic=True, da1=True, only=None):
        self.defaults = dict(DEFAULTS if only is None else {k: DEFAULTS[k] for k in only})
        self.colors = dict(self.defaults)
        self.answers, self.reset_dynamic, self.da1 = answers, reset_dynamic, da1
        self.received: list[str] = []
        self.master, self.slave = pty.openpty()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _reply(self, data: bytes) -> None:
        os.write(self.master, data)

    def _run(self) -> None:
        buf = b""
        while True:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            last = 0
            for m in _SEQ.finditer(buf):
                last = m.end()
                if m.group(1) is None:
                    self.received.append("DA1")
                    if self.da1:
                        self._reply(b"\x1b[?62;22c")
                    continue
                self._handle(m.group(1).decode())
            buf = buf[last:]

    def _handle(self, body: str) -> None:
        self.received.append(body)
        parts = body.split(";")
        if parts[0] == "4" and len(parts) == 3:
            slot, value = f"4;{parts[1]}", parts[2]
        elif parts[0] in ("10", "11", "12", "17", "19") and len(parts) == 2:
            slot, value = parts[0], parts[1]
        elif parts[0] == "104":
            for k in [k for k in self.colors if k.startswith("4;") and (len(parts) == 1 or k == f"4;{parts[1]}")]:
                self.colors[k] = self.defaults[k]
            return
        elif parts[0] in ("110", "111", "112", "117", "119"):
            slot = str(int(parts[0]) - 100)
            if self.reset_dynamic and slot in self.defaults:
                self.colors[slot] = self.defaults[slot]
            return
        else:
            return
        if value == "?":
            if self.answers and slot in self.colors:
                c = self.colors[slot]
                rgb = "/".join(c[i : i + 2] * 2 for i in (1, 3, 5))
                self._reply(f"\x1b]{slot};rgb:{rgb}\x1b\\".encode())
        elif slot in self.colors and re.fullmatch(r"#[0-9a-f]{6}", value):
            self.colors[slot] = value

    def close(self) -> None:
        for fd in (self.slave, self.master):
            with contextlib.suppress(OSError):
                os.close(fd)
