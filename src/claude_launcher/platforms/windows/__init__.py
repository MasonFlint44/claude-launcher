"""Windows: the boot counter and processes through the standard library, and
the System event log for restarts and logoffs (eventlog.py)."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from ...paths import CORE
from ..base import Platform
from .host import WindowsHost


def _events():
    from .eventlog import EventLog, WindowsEvents

    return WindowsEvents(EventLog())


def data_dir(env: Mapping[str, str]) -> Path:
    local = env.get("LOCALAPPDATA") or os.path.join(env.get("USERPROFILE") or str(Path.home()), "AppData", "Local")
    return Path(local) / "claude-launcher"


def _setup():
    raise NotImplementedError("setting up on Windows isn't in yet")


_CMD_SPECIAL = re.compile(r'[&|<>()^%!"]')


def cmd_quote(arg: str) -> str:
    """`arg` as one argument on a command line cmd runs: quoted the way
    programs split their command lines (backslashes only count before a
    quote), then with cmd's own special characters escaped with ^. fzf
    quotes its placeholders ({q}) for cmd the same way."""
    out, slashes = ['"'], 0
    for ch in arg:
        if ch == "\\":
            slashes += 1
        elif ch == '"':
            out.append("\\" * (slashes + 1))
            slashes = 0
        else:
            slashes = 0
        out.append(ch)
    out.append("\\" * slashes + '"')
    return _CMD_SPECIAL.sub(lambda m: "^" + m.group(), "".join(out))


def core_command(args: list[str], python: str = sys.executable) -> str:
    # The program in plain quotes: cmd finds it by them, and ^" isn't one.
    return " ".join([f'"{python}"', *(cmd_quote(a) for a in [str(CORE), *args])])


def platform() -> Platform:
    host = WindowsHost()
    return Platform(
        name="windows",
        host=host,
        events=_events,
        setup=_setup,
        fzf_os="windows",
        fzf_shell="",  # fzf's own: cmd, which it then quotes {q} for
        core_command=core_command,
        fzf_env={"SHELL": "cmd"},  # not OpenSSH's or Git Bash's
        columns_lost=1,
        fzf_listen="port",  # Windows' Python has no Unix sockets
        data_dir=data_dir,
    )
