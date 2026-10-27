"""libexec/claude-launcher-core: the commands bin/claude-launcher, fzf's key
bindings and install.sh call. Not meant to be run by hand.

    resume-dir ARGS...       if ARGS has `--resume <id>`, print that session's directory
    --run DIR theme apply|restore   switch this terminal to Claude Dark and save
                             its title, or put both back
    --run DIR ui start ...   the picker (see picker.py)
    install add-hook|remove-hook|platform|remove-platform|fzf-... (see _install)
    Anything else is one of the platform's own commands (Linux: `terminal
    open guake [LAUNCHER]`, what the Guake desktop entry runs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import fzf, logs, theme, title
from .paths import Paths
from .picker import FzfTooOld, Pick, Picker, resume_dir

NOT_RESUMING = 3  # resume-dir: the arguments don't ask to resume a session


def _resume_dir(args: list[str]) -> int:
    """`--resume <id>` / `-r <id>`: the directory to resume that session in."""
    for flag, value in zip(args, args[1:], strict=False):
        if flag in ("--resume", "-r") and logs.is_session_id(value):
            paths = Paths.from_env()
            directory = logs.session_dir(paths, value)
            if directory is None:
                print(f"No session with ID {value}", file=sys.stderr)
                return 1
            print(resume_dir(directory, paths))
            return 0
    return NOT_RESUMING


def _tty() -> int | None:
    from .platforms import current

    return current().host.open_tty()


def _theme(run: Path, action: str) -> int:
    """Switch this terminal to Claude Dark and save its title, or put both
    back. Quietly does nothing when there's no terminal, and leaves the colors
    alone when it won't say what they are."""
    if action not in ("apply", "restore"):
        print(f"unknown theme action: {action}", file=sys.stderr)
        return 2
    colors = action == "restore" or theme.enabled()
    titled = title.enabled()
    if not (colors or titled):
        return 0
    fd = _tty()
    if fd is None:
        return 0
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
    return 0


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


def _ui(run: Path, action: str, args: list[str]) -> int:
    picker = Picker(run)
    arg = args[0] if args else ""
    out: str | None = None
    if action == "start":
        cols = int(args[0]) if args and args[0].isdigit() else 80
        try:
            opts = picker.start(cols, again="--again" in args)
        except FzfTooOld as e:
            print(e, file=sys.stderr)
            return 1
        _set_title()
        sys.stdout.write("".join(o + "\0" for o in opts))
        return 0
    if action == "menu":
        sys.stdout.write("".join(r + "\n" for r in picker.rows()))
        return 0
    if action == "pick":
        result = picker.pick(arg)
        if isinstance(result, Pick):
            print(f"{result.dir}\t{result.session_id}")
            return 0
        print(result, file=sys.stderr)
        return 1
    if action == "change":
        out = picker.on_change(arg)
    elif action == "enter":
        out = picker.on_enter(arg, args[1] if len(args) > 1 else "")
    elif action == "tab":
        out = picker.on_tab(arg)
    elif action == "sessions":
        out = picker.on_sessions()
    elif action == "result":
        out = picker.on_result(arg)
    elif action == "dismiss":
        out = picker.on_dismiss(arg)
    elif action == "key":
        out = picker.on_key(arg, args[1] if len(args) > 1 else "")
    else:
        print(f"unknown ui action: {action}", file=sys.stderr)
        return 2
    sys.stdout.write(out or "")
    return 0


def _install(action: str, args: list[str]) -> int:
    if action in ("add-hook", "remove-hook"):
        from . import settings

        try:
            if action == "add-hook":
                changed = settings.add_hook(Path(args[0]), Path(args[1]))
            else:
                changed = settings.remove_hook(Path(args[0]))
        except (OSError, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        return 4 if changed else 0  # install.sh says which
    if action in ("platform", "remove-platform"):
        return _platform_setup(action, args)
    if action == "fzf-status":
        path, version = fzf.find()
        print(f"ok {fzf.show(version)} {path}" if path else f"old {fzf.show(version)}")
        return 0
    if action == "fetch-fzf":
        try:
            path = fzf.fetch(fzf.private_dir())
        except (OSError, RuntimeError, KeyError) as e:
            print(e, file=sys.stderr)
            return 1
        print(path)
        return 0
    if action == "fzf-dir":
        print(fzf.private_dir())
        return 0
    print(f"unknown install action: {action}", file=sys.stderr)
    return 2


def _platform_setup(action: str, args: list[str]) -> int:
    """install platform LAUNCHER [--terminal NAMES] [--icon FILE] | remove-platform:
    what install.sh sets up beyond the shared steps, one line per thing done."""
    import subprocess

    from .platforms import current

    setup = current().setup()
    opts = dict(zip(args[1::2], args[2::2], strict=False))
    try:
        if action == "platform":
            lines = setup.install(Path(args[0]), opts.get("--terminal") or None, opts.get("--icon") or None)
        else:
            lines = setup.uninstall()
        for line in lines:
            print(line, flush=True)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


USAGE = __doc__


def main(argv: list[str] | None = None) -> int:
    # Parsed by hand: the arguments after the action are typed text (fzf's
    # query) or claude's own options, which may look like options themselves.
    argv = list(sys.argv[1:] if argv is None else argv)
    run = None
    if argv[:1] == ["--run"] and len(argv) >= 2:
        run, argv = Path(argv[1]), argv[2:]
    command, rest = (argv[0], argv[1:]) if argv else ("", [])
    if command == "resume-dir":
        return _resume_dir(rest)
    if command in ("theme", "install", "ui") and rest:
        if command == "theme" and run is not None:
            return _theme(run, rest[0])
        if command == "install":
            return _install(rest[0], rest[1:])
        if run is not None:
            return _ui(run, rest[0], rest[1:])
    if command and command not in ("theme", "install", "ui"):
        from .platforms import current

        platform_command = current().setup().commands.get(command)
        if platform_command is not None:
            return platform_command(rest)
    print(USAGE, file=sys.stderr)
    return 2
