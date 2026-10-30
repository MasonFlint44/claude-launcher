"""The launcher: pick a project directory or a recent session, start Claude
Code there, and come back to the picker when Claude exits.
bin/claude-launcher runs main().

    claude-launcher [claude options]   pick, then run claude with those options
    claude-launcher --resume <id>      skip the picker: resume that session in its directory

The terminal is left as it was found: its colors and title go back on exit,
unless the terminal itself has gone (a hang-up). Ctrl-C belongs to fzf and to
Claude, never to the launcher: while either runs, the launcher ignores it.
A hang-up or TERM that arrives while Claude or fzf runs is acted on once they
exit, as a shell would, so Claude is never left running without the launcher.
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from . import logs, text, theme, title
from .paths import Paths
from .picker import FzfTooOld, Pick, Picker, resume_dir

HUNG_UP = 129
TERMINATED = 143
QUIT = 130  # fzf's exit code for Esc, Ctrl-C or Ctrl-D


class _Exit(Exception):
    def __init__(self, code: int):
        self.code = code


class _Signals:
    """Hang-ups and TERM end the launcher, but only once the child that's
    running (fzf or Claude) has exited; Ctrl-C is ignored while one runs."""

    def __init__(self) -> None:
        self.pending: int | None = None
        self.in_child = False
        if hasattr(signal, "SIGHUP"):  # not on Windows, where the console closing is its own event
            signal.signal(signal.SIGHUP, self._on(HUNG_UP))
        signal.signal(signal.SIGTERM, self._on(TERMINATED))

    def _on(self, code: int):
        def handler(signum, frame):
            if self.pending is None or code == HUNG_UP:
                self.pending = code
            if not self.in_child:
                raise _Exit(self.pending)

        return handler

    def run(self, argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        """Run a child in the foreground. A handler, not SIG_IGN, for Ctrl-C:
        an ignored signal stays ignored in the child, a handler doesn't."""
        old = signal.signal(signal.SIGINT, lambda *_: None)
        self.in_child = True
        try:
            return subprocess.run(list(argv), **kwargs)
        finally:
            self.in_child = False
            signal.signal(signal.SIGINT, old)
            if self.pending is not None:
                raise _Exit(self.pending)


def _resume_dir(args: Sequence[str]) -> str | int | None:
    """`--resume <id>` / `-r <id>`: the directory to resume that session in.
    None if the arguments don't ask to resume one; 1 if there's no such session."""
    for flag, value in zip(args, args[1:], strict=False):
        if flag in ("--resume", "-r") and logs.is_session_id(value):
            paths = Paths.from_env()
            directory = logs.session_dir(paths, value)
            if directory is None:
                print(f"No session with ID {value}", file=sys.stderr)
                return 1
            return resume_dir(directory, paths)
    return None


def _tty() -> int | None:
    from .platforms import current

    return current().host.open_tty()


def _theme(run: Path, action: str) -> None:
    """Switch this terminal to Claude Dark and save its title ("apply"), or
    put both back ("restore"). Quietly does nothing when there's no terminal,
    and leaves the colors alone when it won't say what they are."""
    colors = action == "restore" or theme.enabled()
    titled = title.enabled()
    if not (colors or titled) or (fd := _tty()) is None:
        return
    try:
        if action == "apply":
            if titled:
                os.write(fd, title.PUSH)
            if colors:
                theme.apply(fd, theme.Theme.load(), run / "colors.json")
        else:
            theme.restore(fd, run / "colors.json")
            if titled:
                os.write(fd, title.POP)
    except OSError:
        pass  # the terminal went away
    finally:
        os.close(fd)


def _set_title() -> None:
    """Name the tab while the picker shows; Claude leaves its own title behind."""
    if not title.enabled() or (fd := _tty()) is None:
        return
    try:
        os.write(fd, title.set_name())
    except OSError:
        pass
    finally:
        os.close(fd)


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def _columns() -> int:
    """The columns fzf draws in."""
    from .platforms import current

    return shutil.get_terminal_size((80, 24)).columns - current().columns_lost


def _batch_arg(arg: str) -> str:
    """`arg` quoted for a .cmd or .bat file, which cmd reads a second time when
    it expands %* inside (BatBadBut). A quote is doubled, so cmd never takes
    what follows as unquoted, and the program's own parsing reads "" as one
    quote; backslashes are doubled before a quote. %cd:~,% expands to
    nothing, so a % can't start a variable."""
    out, slashes = ['"'], 0
    for ch in arg:
        if ch == "\\":
            slashes += 1
        else:
            if ch == '"':
                out.append("\\" * slashes + '"')
            slashes = 0
        out.append("%%cd:~,%" if ch == "%" else ch)
    out.append("\\" * slashes + '"')
    return "".join(out)


def _claude_command(
    args: Sequence[str], which=shutil.which, windows: bool = os.name == "nt", comspec: str | None = None
) -> list[str] | str:
    """How to run claude with `args`. On Windows, npm installs it as
    claude.cmd, which CreateProcess can't start by name (it doesn't try
    PATHEXT), so it's found on PATH and run through cmd."""
    if not windows:
        return ["claude", *args]
    found = which("claude")
    if not found or not found.lower().endswith((".cmd", ".bat")):
        return [found or "claude", *args]
    if any(c in a for a in args for c in "\r\n\0"):
        raise ValueError("Can't pass a line break to claude.cmd")
    line = " ".join([f'"{found}"', *(_batch_arg(a) for a in args)])
    # /s: cmd takes off just the outer quotes; /v:off, so ! is never expanded.
    # System32's cmd.exe, not %ComSpec%, which can be another shell (TCC)
    # that reads these switches its own way.
    if comspec is None:
        root = os.environ.get("SystemRoot")
        comspec = os.path.join(root, "System32", "cmd.exe") if root else "cmd.exe"
    return f'"{comspec}" /d /e:on /v:off /s /c "{line}"'


def _claude(signals: _Signals, directory: str, args: Sequence[str]) -> int | None:
    """Run claude in `directory`. Its exit code, or None if it couldn't start."""
    if not os.path.isdir(directory):  # Windows says "directory name is invalid", not "not found"
        print(f"No such directory: {directory}", file=sys.stderr)
        return None
    env = None
    if os.name != "nt":  # as a shell's cd would: cwd alone leaves PWD naming where the launcher started
        env = {**os.environ, "PWD": directory}
    try:
        return signals.run(_claude_command(args), cwd=directory, env=env).returncode
    except ValueError as e:
        print(e, file=sys.stderr)
    except FileNotFoundError as e:
        gone = e.filename == directory
        print(f"No such directory: {directory}" if gone else "claude isn't on your PATH", file=sys.stderr)
    except (NotADirectoryError, PermissionError) as e:
        print(f"Can't open {directory}: {e.strerror}", file=sys.stderr)
    return None


def _loop(signals: _Signals, run: Path, args: list[str]) -> int:
    from .platforms import current

    # Only key bindings run with this key, so nothing else can drive fzf.
    fzf_env = {**os.environ, **current().fzf_env, "FZF_API_KEY": secrets.token_hex(16)}
    again = False
    while True:
        try:
            opts = Picker(run).start(_columns(), again=again)
        except FzfTooOld as e:
            print(e, file=sys.stderr)
            return 1
        _set_title()
        with open(run / "rows", "rb") as rows:
            done = signals.run(opts, stdin=rows, stdout=subprocess.PIPE, env=fzf_env)  # opts[0] is the fzf to run
        if done.returncode == QUIT:
            return 0
        if done.returncode < 0:  # killed by a signal: exit as a shell would
            return 128 - done.returncode
        if done.returncode > 1:  # fzf itself failed
            return done.returncode
        # The query is fzf's first line of output; the picker works out the rest
        # from its own state, which the key bindings kept up to date on disk.
        query = done.stdout.decode(errors="surrogateescape").split("\n", 1)[0]
        result = Picker(run).pick(query)
        if not isinstance(result, Pick):
            print(result, file=sys.stderr)
            time.sleep(1)  # long enough to read why
            again = True
            continue
        again = False
        resume = ["--resume", result.session_id] if result.session_id else []
        if _claude(signals, result.dir, [*resume, *args]) is None:
            time.sleep(1)


def main(args: list[str]) -> int:
    text.utf8_out()
    from .platforms import Unsupported, current

    try:
        current()
    except Unsupported as e:
        print(e, file=sys.stderr)
        return 1
    # `--resume <id>`: straight to that session, in the directory it was started in.
    resume = _resume_dir(args)
    if isinstance(resume, int):  # an error, already shown
        return resume
    signals = _Signals()
    run = Path(tempfile.mkdtemp(prefix="claude-launcher.", dir=_runtime_dir()))
    hung_up = False
    try:
        # Claude Dark for this tab while the launcher runs, Claude included.
        _theme(run, "apply")
        if resume is not None:
            code = _claude(signals, resume, args)
            return 1 if code is None else code
        return _loop(signals, run, args)
    except _Exit as e:
        hung_up = e.code == HUNG_UP
        return e.code
    except KeyboardInterrupt:  # Ctrl-C while neither fzf nor Claude runs
        return QUIT
    finally:
        # Leaving anyway: a second signal mustn't cut the clean-up short.
        for name in ("SIGHUP", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
        if not hung_up:  # the terminal is gone: nothing to put back
            _theme(run, "restore")
        shutil.rmtree(run, ignore_errors=True)
