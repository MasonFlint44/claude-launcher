"""libexec/claude-launcher-core: the commands fzf's key bindings and the installers
call. Not meant to be run by hand. (bin/claude-launcher itself is launcher.py.)

    --run DIR ui ACTION ...  what a key does in the picker (see picker.py)
    install run|undo|fzf-status|fetch-fzf|fzf-dir (see _install)
    Anything else is one of the platform's own commands (Linux: `terminal
    open guake [LAUNCHER]`, what the Guake desktop entry runs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import fzf, text
from .picker import Picker


def _ui(run: Path, action: str, args: list[str]) -> int:
    if action == "restore-header":  # the background half of a first press, where there's no fork
        import json

        from .picker import restore_header

        restore_header(run, json.loads(args[0]) if args else [])
        return 0
    picker = Picker(run)
    arg = args[0] if args else ""
    out: str | None = None
    if action == "menu":
        sys.stdout.write("".join(r + "\n" for r in picker.rows()))
        return 0
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
    """install run|undo (the installers), or fzf-status|fetch-fzf|fzf-dir
    (CI and the Docker checks)."""
    if action in ("run", "undo"):
        return _run_install(action, args)
    if action in ("fzf-status", "fetch-fzf", "fzf-dir"):
        from .platforms import current

        platform = current()
        own = fzf.private_dir(platform.data_dir(os.environ))
        if action == "fzf-dir":
            print(own)
            return 0
        if action == "fzf-status":
            path, version = fzf.find(own)
            print(f"ok {fzf.show(version)} {path}" if path else f"old {fzf.show(version)}")
            return 0
        try:
            print(fzf.fetch(own, system=platform.fzf_os))
        except (OSError, RuntimeError, KeyError) as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    print(f"unknown install action: {action}", file=sys.stderr)
    return 2


def _run_install(action: str, args: list[str]) -> int:
    """install run [--terminal NAMES] [--icon FILE] | install undo: everything
    the installer does after checking for Python, one line per thing done.
    Exits 1 after an "error: " line."""
    from . import install
    from .platforms import current

    opts = dict(zip(args[::2], args[1::2], strict=False))
    platform = current()
    if action == "run":
        lines = install.install(platform, opts.get("--terminal") or None, opts.get("--icon") or None)
    else:
        lines = install.uninstall(platform)
    failed = False
    for line in lines:
        print(line, flush=True)
        failed = failed or line.startswith("error: ")
    return 1 if failed else 0


USAGE = __doc__


def main(argv: list[str] | None = None) -> int:
    # Parsed by hand: the arguments after the action are typed text (fzf's
    # query) or claude's own options, which may look like options themselves.
    argv = list(sys.argv[1:] if argv is None else argv)
    text.utf8_out()
    run = None
    if argv[:1] == ["--run"] and len(argv) >= 2:
        run, argv = Path(argv[1]), argv[2:]
    command, rest = (argv[0], argv[1:]) if argv else ("", [])
    if command == "ui" and rest and run is not None:
        return _ui(run, rest[0], rest[1:])
    from .platforms import Unsupported, current  # not before: key presses never load the platform

    try:
        if command == "install" and rest:
            return _install(rest[0], rest[1:])
        if command and command not in ("install", "ui"):
            platform_command = current().setup().commands.get(command)
            if platform_command is not None:
                return platform_command(rest)
    except Unsupported as e:
        print(e, file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2
