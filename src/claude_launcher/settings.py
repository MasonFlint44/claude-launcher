"""Adding claude-session-hook to Claude Code's settings.json, and taking it out."""

from __future__ import annotations

import json
import os
from pathlib import Path

HOOK_NAME = "claude-session-hook"
HOOK_EVENTS = (("SessionStart", "start"), ("SessionEnd", "end"))


def _read_settings(path: Path) -> tuple[str, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    settings = json.loads(text) if text.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path} is not a JSON object")
    return text, settings


def _write_settings(path: Path, settings: dict, old_text: str) -> bool:
    """Write `settings` to `path` if it changed. If `path` is a symlink (say,
    into a dotfiles repo), the file it points to is written, so the link stays."""
    new = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if new == old_text:
        return False
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".claude-launcher.tmp")
    tmp.write_text(new, encoding="utf-8")
    if target.exists():
        os.chmod(tmp, target.stat().st_mode & 0o7777)
    os.replace(tmp, target)
    return True


def _without_our_hook(groups: list) -> list:
    """The event's hook groups with our hook taken out, and any group it leaves empty."""
    kept = []
    for g in groups:
        if not isinstance(g, dict):
            kept.append(g)
            continue
        hooks = [h for h in g.get("hooks", []) if HOOK_NAME not in str(h.get("command", ""))]
        if hooks:
            kept.append({**g, "hooks": hooks})
        elif not g.get("hooks"):
            kept.append(g)  # an empty group that was there before us
    return kept


def add_hook(settings_path: Path, hook: Path) -> bool:
    """Add claude-session-hook as a SessionStart and SessionEnd hook, leaving
    every other setting and hook as it was. True if the file changed."""
    text, settings = _read_settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    for event, arg in HOOK_EVENTS:
        command = f"{hook} {arg}"
        groups = hooks.setdefault(event, [])
        ours = [
            h
            for g in groups
            if isinstance(g, dict)
            for h in g.get("hooks", [])
            if HOOK_NAME in str(h.get("command", ""))
        ]
        if len(ours) == 1 and ours[0].get("command") == command:
            continue
        # Drop old copies (say, from before the repo moved), then add it.
        groups[:] = _without_our_hook(groups)
        groups.append({"hooks": [{"type": "command", "command": command}]})
    return _write_settings(settings_path, settings, text)


def remove_hook(settings_path: Path) -> bool:
    text, settings = _read_settings(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event, _ in HOOK_EVENTS:
        if isinstance(hooks.get(event), list):
            hooks[event] = _without_our_hook(hooks[event])
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        del settings["hooks"]
    return _write_settings(settings_path, settings, text)
