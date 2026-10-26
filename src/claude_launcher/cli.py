"""libexec/claude-launcher-core: the commands bin/claude-launcher, fzf's key
bindings and install.sh call. Not meant to be run by hand.

    resume-dir ARGS...       if ARGS has `--resume <id>`, print that session's directory
    --run DIR theme apply|restore   switch this terminal to Claude Dark, or back
    --run DIR ui start ...   the picker (see picker.py)
    install add-hook|remove-hook|terminals|icon|... (see _install)
    terminal open guake [LAUNCHER]   what the Guake desktop entry runs
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import fzf, install, logs, terminals, theme
from .paths import CORE, REPO_DIR, Paths
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


def _theme(run: Path, action: str) -> int:
    """Switch this terminal to Claude Dark, or put its colors back. Quietly
    does nothing when there's no terminal, or it won't say what its colors are."""
    if action == "apply" and not theme.enabled():
        return 0
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return 0
    try:
        if action == "apply":
            theme.apply(fd, theme.Theme.load(), run / "colors.json")
        elif action == "restore":
            theme.restore(fd, run / "colors.json")
        else:
            print(f"unknown theme action: {action}", file=sys.stderr)
            return 2
    except OSError:
        pass  # the terminal went away
    finally:
        os.close(fd)
    return 0


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
    if action == "accept":
        picker.on_accept(arg)
        return 0
    if action == "change":
        out = picker.on_change(arg)
    elif action == "tab":
        out = picker.on_tab()
    elif action == "shift-tab":
        out = picker.on_shift_tab(arg)
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
    if action == "add-hook":
        changed = install.add_hook(Path(args[0]), Path(args[1]))
    elif action == "remove-hook":
        changed = install.remove_hook(Path(args[0]))
    elif action in ("terminals", "remove-terminals", "icon", "remove-icon", "list-terminals"):
        return _install_desktop(action, args)
    elif action == "fzf-status":
        path, version = fzf.find()
        print(f"ok {fzf.show(version)} {path}" if path else f"old {fzf.show(version)}")
        return 0
    elif action == "fetch-fzf":
        try:
            path = fzf.fetch(fzf.private_dir())
        except (OSError, RuntimeError, KeyError) as e:
            print(e, file=sys.stderr)
            return 1
        print(path)
        return 0
    elif action == "fzf-dir":
        print(fzf.private_dir())
        return 0
    else:
        print(f"unknown install action: {action}", file=sys.stderr)
        return 2
    return 4 if changed else 0  # install.sh says which


def _context(launcher: str | None = None) -> terminals.Context:
    path = Path(launcher) if launcher else Path(shutil.which("claude-launcher") or REPO_DIR / "bin" / "claude-launcher")
    return terminals.Context.from_env(launcher=path, core=CORE)


def _install_desktop(action: str, args: list[str]) -> int:
    """install terminals LAUNCHER [NAME,...] | remove-terminals | icon [FILE|default] | remove-icon | list-terminals"""
    try:
        if action == "terminals":
            names = [n for n in args[1].split(",") if n] if len(args) > 1 else None
            for line in install.install_terminals(_context(args[0]), names):
                print(line)
        elif action == "remove-terminals":
            for path in install.remove_terminals(_context()):
                print(f"Removed {path}")
        elif action == "icon":
            print(install.install_icon(_context(), args[0] if args else None))
        elif action == "remove-icon":
            install.remove_icon(_context())
        else:
            for t in terminals.TERMINALS.values():
                state = "installed" if t.installed() and t.how != "fallback" else ""
                print(f"{t.name:15} {t.how:10} {state}")
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def _terminal(action: str, args: list[str]) -> int:
    """terminal open NAME [LAUNCHER]: what a drop-down terminal's desktop entry runs."""
    term = terminals.TERMINALS.get(args[0]) if args else None
    if action != "open" or not isinstance(term, terminals.Guake):
        print(f"can't open {' '.join(args)}", file=sys.stderr)
        return 2
    term.open(_context(args[1] if len(args) > 1 else None))
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
    if command == "terminal" and rest:
        return _terminal(rest[0], rest[1:])
    if command in ("theme", "install", "ui") and rest:
        if command == "theme" and run is not None:
            return _theme(run, rest[0])
        if command == "install":
            return _install(rest[0], rest[1:])
        if run is not None:
            return _ui(run, rest[0], rest[1:])
    print(USAGE, file=sys.stderr)
    return 2
