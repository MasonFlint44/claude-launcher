"""Text helpers for terminal rows: display width, padding, colors, times."""

from __future__ import annotations

import re
import time
import unicodedata

RESET = "\x1b[0m"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def fg(hex_color: str) -> str:
    """The escape that sets the text color to `hex_color` (#rrggbb)."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[38;2;{r};{g};{b}m"


def color(text: str, hex_color: str) -> str:
    return f"{fg(hex_color)}{text}{RESET}" if text else ""


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# Escape sequences (CSI, OSC, and two-character ones), then any other control character.
_CONTROLS = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])|[\x00-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    """`text` without escape sequences or control characters, for text from
    logs and paths: pasted terminal output keeps its color codes, and fzf
    would show them."""
    return _CONTROLS.sub("", text)


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "‍️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def width(text: str) -> int:
    """How many columns `text` takes in a terminal, ignoring color escapes."""
    return sum(_char_width(ch) for ch in strip_ansi(text))


def truncate(text: str, columns: int) -> str:
    """`text` cut to fit in `columns`, ending in "…" if it was cut."""
    if width(text) <= columns:
        return text
    if columns <= 0:
        return ""
    out, used = [], 0
    for ch in text:
        w = _char_width(ch)
        if used + w > columns - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def truncate_left(text: str, columns: int) -> str:
    """`text` cut from the left to fit in `columns`, starting with "…" if it was cut."""
    if width(text) <= columns:
        return text
    if columns <= 0:
        return ""
    out, used = [], 0
    for ch in reversed(text):
        w = _char_width(ch)
        if used + w > columns - 1:
            break
        out.append(ch)
        used += w
    return "…" + "".join(reversed(out))


def pad(text: str, columns: int) -> str:
    """`text` followed by spaces to fill `columns` (nothing if it's wider)."""
    return text + " " * max(0, columns - width(text))


def age(then: float, now: float | None = None) -> str:
    """How long ago `then` was, like "now", "6m ago" or "2d ago"."""
    s = int((time.time() if now is None else now) - then)
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    if s < 7 * 86400:
        return f"{s // 86400}d ago"
    return f"{s // (7 * 86400)}w ago"


def clock(then: float, now: float | None = None) -> str:
    """`then` as "2:01 PM" today, "Sat 2:01 PM" within the week, else "Oct 18 2:01 PM"."""
    now = time.time() if now is None else now
    t, today = time.localtime(then), time.localtime(now)
    hm = time.strftime("%I:%M %p", t).lstrip("0")
    if t[:3] == today[:3]:
        return hm
    if now - then < 6 * 86400:
        return time.strftime("%a ", t) + hm
    return time.strftime("%b ", t) + str(t.tm_mday) + " " + hm
