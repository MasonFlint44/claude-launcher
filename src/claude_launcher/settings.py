"""Adding claude-session-hook to Claude Code's settings.json, and taking it out."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from . import files

HOOK_NAME = "claude-session-hook"
# The names the hook is run by: the script (Linux, WSL), Windows' entry
# script, and the .cmd that runs it.
HOOK_FILES = (HOOK_NAME, HOOK_NAME + ".py", HOOK_NAME + ".cmd")
HOOK_EVENTS = (("SessionStart", "start"), ("SessionEnd", "end"))
# Windows can refuse to replace settings.json for a moment while another
# process has it open (files.replace tries again); elsewhere it's os.replace.
_WINDOWS = os.name == "nt"


def _read_settings(path: Path) -> tuple[str, dict]:
    try:
        # utf-8-sig: Windows editors (Notepad, PowerShell 5.1) may add a BOM.
        # It's written back without one.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        text = ""
    settings = json.loads(text) if text.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path} is not a JSON object")
    return text, settings


def _hooks(settings: dict, create: bool) -> dict | None:
    """settings.json's "hooks", checked to be the shape Claude reads, so an
    unusual file is reported rather than half-changed."""
    if "hooks" not in settings:
        if not create:
            return None
        settings["hooks"] = {}
    hooks = settings["hooks"]
    if not isinstance(hooks, dict):
        raise ValueError("settings.json's hooks isn't an object")
    for event, _ in HOOK_EVENTS:
        if event not in hooks:
            continue
        groups = hooks[event]
        if not isinstance(groups, list):
            raise ValueError(f"settings.json's hooks.{event} isn't a list")
        for g in groups:
            if isinstance(g, dict) and "hooks" in g and not isinstance(g["hooks"], list):
                raise ValueError(f"a hooks.{event} entry in settings.json has hooks that aren't a list")
    return hooks


def _write_settings(path: Path, settings: dict, old_text: str) -> bool:
    """Write `settings` to `path` if it changed. If `path` is a symlink (say,
    into a dotfiles repo), the file it points to is written, so the link stays."""
    new = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if new == old_text:
        return False
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".claude-launcher.tmp")
    tmp.write_text(new, encoding="utf-8", newline="")
    if target.exists():
        os.chmod(tmp, target.stat().st_mode & 0o7777)
    (files.replace if _WINDOWS else os.replace)(tmp, target)
    return True


def _names_ours(path: object) -> bool:
    """Whether `path`'s last part is one of the hook's names: a wrapper
    called my-claude-session-hook isn't ours."""
    return isinstance(path, str) and re.split(r"[/\\]", path)[-1] in HOOK_FILES


def _is_ours(hook: object) -> bool:
    """Our hook, in either form: a shell command line whose program is the
    hook, or a program and its "args" (how Windows runs it, with no shell:
    cmd /c the .cmd, or before that python the script) where either is."""
    if not isinstance(hook, dict):
        return False
    command, args = hook.get("command"), hook.get("args")
    if isinstance(args, list):
        return any(_names_ours(p) for p in [command, *args])
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    if words and _names_ours(words[0]):
        return True
    # From before the quoting: a path with a space in it, unquoted.
    program, _, arg = command.rpartition(" ")
    return arg in {a for _, a in HOOK_EVENTS} and _names_ours(program)


def hook_entry(argv: list[str], arg: str) -> dict:
    """The settings.json entry that runs `argv` with `arg` ("start" or "end").
    One word is a script, run by Claude's shell (quoted, for a home like
    /home/John Doe); more is a program and its arguments, run with no shell.
    Entries from before the quoting are still ours (`_is_ours`), so they're
    replaced or taken out like any other."""
    if len(argv) == 1:
        return {"type": "command", "command": f"{shlex.quote(argv[0])} {arg}"}
    return {"type": "command", "command": argv[0], "args": [*argv[1:], arg]}


def _without_our_hook(groups: list) -> list:
    """The event's hook groups with our hook taken out, and any group it leaves empty."""
    kept = []
    for g in groups:
        if not isinstance(g, dict):
            kept.append(g)
            continue
        hooks = [h for h in g.get("hooks", []) if not _is_ours(h)]
        if hooks:
            kept.append({**g, "hooks": hooks})
        elif not g.get("hooks"):
            kept.append(g)  # an empty group that was there before us
    return kept


def add_hook(settings_path: Path, hook: Path | list[str]) -> bool:
    """Add claude-session-hook as a SessionStart and SessionEnd hook, leaving
    every other setting and hook as it was. `hook` is the script, or on
    Windows the program and arguments that run it. True if the file changed."""
    argv = [str(hook)] if isinstance(hook, Path) else [str(a) for a in hook]
    text, settings = _read_settings(settings_path)
    hooks = _hooks(settings, create=True)
    for event, arg in HOOK_EVENTS:
        entry = hook_entry(argv, arg)
        groups = hooks.setdefault(event, [])
        ours = [h for g in groups if isinstance(g, dict) for h in g.get("hooks", []) if _is_ours(h)]
        if len(ours) == 1 and ours[0] == entry:
            continue
        # Drop old copies (say, from before the repo moved), then add it.
        groups[:] = _without_our_hook(groups)
        groups.append({"hooks": [entry]})
    return _write_settings(settings_path, settings, text)


def remove_hook(settings_path: Path) -> bool:
    text, settings = _read_settings(settings_path)
    hooks = _hooks(settings, create=False)
    if hooks is None:
        return False
    for event, _ in HOOK_EVENTS:
        if event in hooks:
            hooks[event] = _without_our_hook(hooks[event])
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        del settings["hooks"]
    return _write_settings(settings_path, settings, text)
