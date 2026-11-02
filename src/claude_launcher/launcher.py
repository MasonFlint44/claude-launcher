"""The launcher: pick a project directory or a recent session, start Claude
Code there, and come back to the picker when Claude exits.
bin/claude-launcher runs main(); USAGE is what --help shows.

The terminal is left as it was found: its colors and title go back on exit,
unless the terminal itself has gone (a hang-up). Ctrl-C belongs to fzf and to
Claude, never to the launcher: while either runs, the launcher ignores it.
A hang-up or TERM that arrives while Claude or fzf runs is acted on once they
exit, as a shell would, so Claude is never left running without the launcher.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from . import fzf, logs, text, theme, title
from .paths import Paths
from .paths import which as _which
from .picker import FzfTooOld, Pick, Picker, group_of, resume_dir, ungroup_keys
from .restart import RestartState

USAGE = """\
Pick a project directory or a recent session, then start Claude Code there.
When Claude exits, the picker comes back.

  claude-launcher [claude options]   pick, then run claude with those options
  claude-launcher --resume <id>      skip the picker: resume that session in its directory
  claude-launcher -r <id>            the same
  claude-launcher --help, -h         show this
  claude-launcher --version          show the launcher's version

--help, -h and --version count only as the first argument; after another one
they go to claude. See README.md for the keys."""

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

    def run(
        self, argv: Sequence[str] | str, started: Callable[[], None] | None = None, **kwargs
    ) -> subprocess.CompletedProcess:
        """Run a child in the foreground, calling `started` once it has. A
        handler, not SIG_IGN, for Ctrl-C: an ignored signal stays ignored in
        the child, a handler doesn't. A str is a Windows command line, passed
        on as it is (npm's claude.cmd through cmd)."""
        argv = argv if isinstance(argv, str) else list(argv)
        old = signal.signal(signal.SIGINT, lambda *_: None)
        self.in_child = True
        try:
            if started is None:
                return subprocess.run(argv, **kwargs)
            with subprocess.Popen(argv, **kwargs) as child:
                started()
                return subprocess.CompletedProcess(child.args, child.wait())
        finally:
            self.in_child = False
            signal.signal(signal.SIGINT, old)
            if self.pending is not None:
                raise _Exit(self.pending)


def _resume(args: Sequence[str]) -> tuple[str, list[str]] | int | None:
    """`--resume <id>` / `-r <id>`: the directory to resume that session in,
    and what to take out of the restart group once Claude has started, as
    Enter in the picker does. None if the arguments don't ask to resume one;
    1 if there's no such session."""
    pairs = zip(args, args[1:], strict=False)
    session_id = next((v for f, v in pairs if f in ("--resume", "-r") and logs.is_session_id(v)), None)
    if session_id is None:
        return None
    paths = Paths.from_env()
    directory = logs.session_dir(paths, session_id)
    if directory is None:
        print(f"No session with ID {session_id}", file=sys.stderr)
        return 1
    # Sorted first, as opening the picker does: a session a restart closed
    # joins the group now, and so leaves it, instead of coming back as a ↻
    # row at the next picker although it was resumed.
    state = RestartState(paths)
    try:
        group = state.sort()
    except OSError:  # can't write the group: resume anyway, from what's there
        group = state.load()

    def continued_from() -> dict[str, str]:
        log = paths.log_for(session_id)  # continuations never leave their log directory
        return logs.scan_sessions([log.parent] if log else [])[1]

    key = group_of(session_id, group, continued_from)
    return resume_dir(directory, paths), ungroup_keys(session_id, key)


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
    args: Sequence[str],
    which: Callable[[str], str | None] | None = None,
    windows: bool = os.name == "nt",
    comspec: str | None = None,
) -> list[str] | str:
    """How to run claude with `args`. On Windows, npm installs it as
    claude.cmd, which CreateProcess can't start by name (it doesn't try
    PATHEXT), so it's found on PATH and run through cmd. Never by its bare
    name there: CreateProcess would look in the current directory first."""
    if not windows:
        return ["claude", *args]
    found = which("claude") if which else _which("claude", windows=True)
    if not found:
        raise FileNotFoundError(errno.ENOENT, "claude isn't on your PATH", "claude")
    if not found.lower().endswith((".cmd", ".bat")):
        return [found, *args]
    if any(c in a for a in args for c in "\r\n\0"):
        raise ValueError("Can't pass a line break to claude.cmd")
    line = " ".join([f'"{found}"', *(_batch_arg(a) for a in args)])
    # /s: cmd takes off just the outer quotes; /v:off, so ! is never expanded.
    # System32's cmd.exe, not %ComSpec%, which can be another shell (TCC)
    # that reads these switches its own way.
    if comspec is None:
        from .platforms.windows import system32

        comspec = system32(os.environ, "cmd.exe")
    return f'"{comspec}" /d /e:on /v:off /s /c "{line}"'


def _claude(
    signals: _Signals, directory: str, args: Sequence[str], started: Callable[[], None] | None = None
) -> int | None:
    """Run claude in `directory`, calling `started` once it has. Its exit
    code as a shell gives it (128 + the signal, if one killed it), or None if
    it couldn't start."""
    if not os.path.isdir(directory):  # Windows says "directory name is invalid", not "not found"
        print(f"No such directory: {directory}", file=sys.stderr)
        return None
    env = None
    if os.name != "nt":  # as a shell's cd would: cwd alone leaves PWD naming where the launcher started
        env = {**os.environ, "PWD": directory}
    try:
        code = signals.run(_claude_command(args), started=started, cwd=directory, env=env).returncode
        return 128 - code if code < 0 else code
    except ValueError as e:
        print(e, file=sys.stderr)
    except FileNotFoundError as e:
        gone = e.filename == directory
        print(f"No such directory: {directory}" if gone else "claude isn't on your PATH", file=sys.stderr)
    except (NotADirectoryError, PermissionError) as e:
        print(f"Can't open {directory}: {e.strerror}", file=sys.stderr)
    return None


def _ungroup(keys: list[str]) -> Callable[[], None] | None:
    """What takes a resumed session out of the restart group, once Claude is running."""
    if not keys:
        return None

    def ungroup() -> None:
        with contextlib.suppress(OSError):  # Claude is running; the row staying is all that's lost
            RestartState(Paths.from_env()).drop(keys)

    return ungroup


def _loop(signals: _Signals, run: Path, args: list[str]) -> int:
    from .platforms import current

    # Only key bindings run with this key, so nothing else can drive fzf.
    # The key presses fzf runs inherit this, without the user's fzf defaults.
    fzf_env = {**fzf.environ(), **current().fzf_env, "FZF_API_KEY": secrets.token_hex(16)}
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
        if _claude(signals, result.dir, [*resume, *args], _ungroup(result.ungroup)) is None:
            time.sleep(1)


def version(windows: bool = os.name == "nt") -> str:
    """The version, with the commit when the launcher runs from a git clone
    (how it's installed). Leaves the commit out if git can't say."""
    from . import __version__
    from .paths import REPO_DIR

    if not (REPO_DIR / ".git").exists():  # git would look in the directories above
        return __version__
    git = _which("git", windows=windows)
    if not git:
        return __version__
    try:
        done = subprocess.run(
            [git, "-C", str(REPO_DIR), "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return __version__
    commit = done.stdout.strip()
    if done.returncode != 0 or not commit or commit == f"v{__version__}":  # exactly the release
        return __version__
    return f"{__version__} ({commit})"


def main(args: list[str]) -> int:
    text.utf8_out()
    # Only first: later, "--help" may be a claude option's value.
    if args[:1] in (["--help"], ["-h"]):
        print(USAGE)
        return 0
    if args[:1] == ["--version"]:
        print(f"claude-launcher {version()}")
        return 0
    from .platforms import Unsupported, current

    try:
        current()
    except Unsupported as e:
        print(e, file=sys.stderr)
        return 1
    # `--resume <id>`: straight to that session, in the directory it was started in.
    try:
        resume = _resume(args)
    except KeyboardInterrupt:  # Ctrl-C in the pause before resuming from ~
        return QUIT
    if isinstance(resume, int):  # an error, already shown
        return resume
    signals = _Signals()
    run = Path(tempfile.mkdtemp(prefix="claude-launcher.", dir=_runtime_dir()))
    hung_up = False
    try:
        # Claude Dark for this tab while the launcher runs, Claude included.
        _theme(run, "apply")
        if resume is not None:
            code = _claude(signals, resume[0], args, _ungroup(resume[1]))
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
