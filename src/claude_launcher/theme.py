"""The Claude Dark colors: read from the Ghostty theme file, and switched on
in the terminal the launcher runs in.

Switching colors asks the terminal for its own first (OSC 4/10/11/12/17/19
queries, then a device-attributes request whose answer marks the end of the
replies) and changes only what it reported, so a terminal that can't put its
colors back is left alone. Putting them back sends the resets (OSC 104/110-119),
asks again, and sets anything still not right back to what it was: Konsole, for
one, has no reset for its text and background colors. Colors are written as
#rrggbb, the one form every terminal tested here reads (Konsole doesn't read
rgb:rr/gg/bb).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import THEME_FILE

if TYPE_CHECKING:
    from .platforms import Host

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_ST = "\x1b\\"  # string terminator

# Ghostty theme key -> the OSC number for that dynamic color.
_DYNAMIC = {
    "foreground": 10,
    "background": 11,
    "cursor-color": 12,
    "selection-background": 17,
    "selection-foreground": 19,
}


def _hex(value: str) -> str | None:
    m = _HEX.match(value.strip())
    return "#" + m.group(1).lower() if m else None


@dataclass(frozen=True)
class Theme:
    colors: dict[str, str] = field(default_factory=dict)  # key -> #rrggbb
    palette: dict[int, str] = field(default_factory=dict)  # 0..255 -> #rrggbb

    @classmethod
    def parse(cls, text: str) -> Theme:
        colors: dict[str, str] = {}
        palette: dict[int, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key == "palette":
                index, _, color = value.partition("=")
                c = _hex(color)
                if index.strip().isdigit() and c and int(index) < 256:
                    palette[int(index)] = c
            else:
                c = _hex(value)
                if c:
                    colors[key] = c
        return cls(colors, palette)

    @classmethod
    def load(cls, path: Path = THEME_FILE) -> Theme:
        try:
            return cls.parse(path.read_text(encoding="utf-8"))
        except OSError:
            return cls()

    def get(self, key: str, default: str) -> str:
        return self.colors.get(key, default)

    def ansi(self, index: int, default: str) -> str:
        return self.palette.get(index, default)

    # The picker's colors, taken from the theme.
    @property
    def accent(self) -> str:
        return self.get("cursor-color", "#d77757")

    @property
    def text(self) -> str:
        return self.get("foreground", "#edebe5")

    @property
    def bright(self) -> str:
        return self.ansi(15, "#faf9f5")

    @property
    def dim(self) -> str:
        return self.ansi(8, "#8a887f")

    @property
    def line(self) -> str:
        return self.ansi(0, "#3a3936")

    def slots(self) -> dict[str, str]:
        """What the theme sets, as {OSC slot: #rrggbb}: "4;N" for palette
        color N, "10", "11", ... for the dynamic colors."""
        out = {f"4;{i}": c for i, c in sorted(self.palette.items())}
        out.update({str(osc): self.colors[key] for key, osc in _DYNAMIC.items() if key in self.colors})
        return out


# --- The terminal ------------------------------------------------------------

_REPLY = re.compile(rb"\x1b\](4;\d+|1\d);([^\x07\x1b]*)(?:\x07|\x1b\\)")
_DA1_REPLY = re.compile(rb"\x1b\[\?[0-9;]*c")
REPLY_TIMEOUT = 1.0  # seconds to wait for the terminal's answers


def _osc(body: str) -> bytes:
    return f"\x1b]{body}{_ST}".encode()


def _set(slot: str, color: str) -> bytes:
    return _osc(f"{slot};{color}")


def _reset(slot: str) -> bytes:
    if slot.startswith("4;"):
        return _osc(f"104;{slot[2:]}")
    return _osc(str(int(slot) + 100))


def normalize(value: str) -> str | None:
    """A color as a terminal reports it (rgb:rrrr/gggg/bbbb, rgb:rr/gg/bb or
    #rrggbb) as #rrggbb."""
    value = value.strip()
    if value.startswith("#"):
        return _hex(value)
    m = re.fullmatch(r"rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})(?:/[0-9a-fA-F]{1,4})?", value)
    if not m:
        return None
    channels = (round(int(h, 16) * 255 / (16 ** len(h) - 1)) for h in m.groups())
    return "#" + "".join(f"{c:02x}" for c in channels)


def ask(fd: int, slots, host: Host | None = None) -> dict[str, str] | None:
    """The terminal's current colors for `slots`, as {slot: #rrggbb}, leaving
    out any it didn't report. None if it didn't answer at all."""
    if host is None:
        from .platforms import current

        host = current().host
    query = b"".join(_osc(f"{slot};?") for slot in slots) + b"\x1b[c"
    buf = host.terminal_reply(fd, query, _DA1_REPLY, REPLY_TIMEOUT)
    if buf is None:
        return None
    found = {}
    for slot, value in _REPLY.findall(buf):
        color = normalize(value.decode(errors="replace"))
        if color:
            found[slot.decode()] = color
    return found


def enabled(env=os.environ) -> bool:
    """Whether to switch colors: CLAUDE_LAUNCHER_COLORS=0 turns it off, =1 on
    even inside GNU screen.

    tmux keeps colors per pane and answers the queries (3.3 and later for all
    of them, 3.2 for text and background), so it's handled like any terminal.
    screen ignores most of them and passes the background straight to the
    outer terminal, for every window at once, with no way to reset it.
    """
    setting = env.get("CLAUDE_LAUNCHER_COLORS", "")
    if setting in ("0", "1"):
        return setting == "1"
    return not (env.get("STY") or env.get("TERM", "") in ("", "dumb"))


def apply(fd: int, theme: Theme, saved: Path) -> bool:
    """Switch the terminal to `theme`, keeping its own colors in `saved` for
    restore(). Only the colors it reports are changed."""
    targets = theme.slots()
    originals = ask(fd, targets)
    if not originals:
        return False
    saved.write_text(json.dumps(originals), encoding="utf-8", newline="")
    os.write(fd, b"".join(_set(slot, targets[slot]) for slot in originals))
    return True


def restore(fd: int, saved: Path) -> None:
    """Put back the colors apply() changed."""
    try:
        originals = json.loads(saved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    os.write(fd, b"".join(_reset(slot) for slot in originals))
    now = ask(fd, originals) or {}
    wrong = [slot for slot, color in originals.items() if now.get(slot) != color]
    if wrong:
        os.write(fd, b"".join(_set(slot, originals[slot]) for slot in wrong))
    saved.unlink(missing_ok=True)
