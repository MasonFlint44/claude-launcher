"""The tab's title: "Claude Code" while the picker shows.

Claude sets its own title while it runs and leaves it there when it exits, so
the picker sets this one each time it comes back. The title the tab had
before is saved on the terminal's title stack (xterm's CSI 22/23 t) and put
back on exit. Ptyxis (VTE) keeps the stack; Ghostty has none, and xterm only
with allowWindowOps. Those ignore it and keep "Claude Code" until something
else sets a title: a tab opened from the desktop launcher closes on exit, and
a shell that sets the title at its prompt (Ubuntu's default bashrc, Ghostty's
shell integration) puts its own back.
"""

from __future__ import annotations

import os

NAME = "Claude Code"
PUSH = b"\x1b[22;0t"  # save the window and icon titles
POP = b"\x1b[23;0t"  # put them back


def set_name(name: str = NAME) -> bytes:
    return f"\x1b]0;{name}\x1b\\".encode()


def dumb(env=os.environ, windows: bool = os.name == "nt") -> bool:
    """A terminal that takes no escapes. Windows' consoles never set TERM, and
    take them; elsewhere an unset TERM means none."""
    term = env.get("TERM", "")
    return term == "dumb" or (not term and not windows)


def enabled(env=os.environ, windows: bool = os.name == "nt") -> bool:
    return not dumb(env, windows)
