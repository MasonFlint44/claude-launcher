"""Windows: the boot counter and processes through the standard library, and
the System event log for restarts and logoffs (eventlog.py)."""

from __future__ import annotations

import ntpath
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from ...paths import CORE
from ..base import Platform


def _events():
    from .eventlog import EventLog, WindowsEvents

    return WindowsEvents(EventLog())


def getenv(env: Mapping[str, str], name: str) -> str | None:
    """Windows' environment ignores case (SystemRoot, SYSTEMROOT); a plain
    mapping, as in tests, doesn't."""
    if name in env:
        return env[name]
    folded = name.upper()
    return next((v for k, v in env.items() if k.upper() == folded), None)


def system32(env: Mapping[str, str], program: str) -> str:
    """`program` in System32, by full path: by its bare name CreateProcess
    looks in the current directory first, which is the user's project.
    Without SystemRoot (a scrubbed environment), the bare name, as for cmd."""
    root = getenv(env, "SystemRoot")
    return ntpath.join(root, "System32", program) if root else program


_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def on_path(env: Mapping[str, str], program: str, cwd: str | None = None) -> str | None:
    """`program` (a bare name) by its full path on PATH, trying PATHEXT's
    extensions as shutil.which does, but never in the current directory:
    shutil.which looks there first on Windows, and a cloned repo could plant
    a fzf.bat or claude.cmd. PATH entries that are relative, or are the
    current directory, are skipped too."""
    here = _folded(cwd or os.getcwd())
    # Lower case, as programs are usually named; Windows ignores case anyway.
    exts = [e.lower() for e in (getenv(env, "PATHEXT") or _PATHEXT).split(";") if e]
    names = [program] if program.lower().endswith(tuple(exts)) else [program + e for e in exts]
    for entry in (getenv(env, "PATH") or "").split(os.pathsep):
        if not os.path.isabs(entry) or _folded(entry) == here:
            continue
        for name in names:
            path = os.path.join(entry, name)
            if os.path.isfile(path):
                return path
    return None


def _folded(path: str) -> str:
    return os.path.normcase(os.path.realpath(path)).casefold()


def local_app_data(env: Mapping[str, str]) -> Path:
    """%LOCALAPPDATA%, or where it is by default when it isn't set."""
    home = getenv(env, "USERPROFILE") or str(Path.home())
    return Path(getenv(env, "LOCALAPPDATA") or os.path.join(home, "AppData", "Local"))


def data_dir(env: Mapping[str, str]) -> Path:
    return local_app_data(env) / "claude-launcher"


def _setup():
    from .setup import WindowsSetup

    return WindowsSetup(data_dir(os.environ))


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
    # Here, not at the top: WSL imports eventlog.py and terminal.py from this
    # package, and has no use for Windows' own host (ctypes).
    from .host import WindowsHost

    return Platform(
        name="windows",
        host=WindowsHost(),
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
