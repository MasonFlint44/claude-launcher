"""What install.sh changes outside the repo: Claude's settings, the desktop
entries (and terminal profiles, see terminals.py), and the app icon."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from .paths import REPO_DIR
from .terminals import APP_ID, TERMINALS, Context, Terminal, pick

ICONS_DIR = REPO_DIR / "share" / "icons"

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


# --- Desktop entries and the icon ----------------------------------------------


def _desktop_quote(arg: str) -> str:
    """One argument for a desktop entry's Exec line."""
    if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`%"):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg).replace("%", "%%")
    return f'"{escaped}"'


def desktop_entry(term: Terminal, ctx: Context, primary: bool) -> str:
    """The "Claude Code" desktop entry for `term`. The first terminal gets
    the plain name; any others say which terminal they open."""
    name = "Claude Code" if primary else f"Claude Code ({term.label})"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        "GenericName=Claude Code launcher",
        "Comment=Pick a project or session, then start Claude Code",
        "Exec=" + " ".join(_desktop_quote(a) for a in term.exec_argv(ctx)),
        f"Icon={APP_ID}",
        f"Terminal={'true' if term.how == 'fallback' else 'false'}",
        "Categories=Development;",
        "StartupNotify=true",
    ]
    if term.own_window:
        lines.append(f"StartupWMClass={APP_ID}")
    return "\n".join(lines) + "\n"


def entry_file(ctx: Context, term: Terminal, primary: bool) -> Path:
    # The primary entry's ID matches the window class, which is how the dock
    # knows which icon a window gets.
    name = f"{APP_ID}.desktop" if primary else f"{APP_ID}.{term.name}.desktop"
    return ctx.data_home / "applications" / name


def _state_file(ctx: Context) -> Path:
    return ctx.data_home / "claude-launcher" / "terminals.json"


def saved_terminals(ctx: Context) -> list[str] | None:
    try:
        names = json.loads(_state_file(ctx).read_text())
    except (OSError, ValueError):
        return None
    return [n for n in names if n in TERMINALS] if isinstance(names, list) else None


def install_terminals(ctx: Context, names: list[str] | None) -> list[str]:
    """Set up the desktop entries (and any profiles) for `names`, or the ones
    chosen last time, or the first installed terminal. Returns what it did."""
    chosen = pick(None) if names == ["auto"] else pick(names or saved_terminals(ctx))
    missing = [t.label for t in chosen if not t.installed()]
    if missing:
        raise ValueError(f"not installed: {', '.join(missing)}")
    done = []
    remove_terminals(ctx, keep={t.name for t in chosen})
    for i, term in enumerate(chosen):
        term.install(ctx)
        path = entry_file(ctx, term, primary=i == 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(desktop_entry(term, ctx, primary=i == 0))
        os.replace(tmp, path)
        how = {"profile": " (with a Claude Code profile)", "drop-down": " (a new tab in it)"}.get(term.how, "")
        done.append(f"{path.name}: opens {term.label}{how}")
    state = _state_file(ctx)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps([t.name for t in chosen]) + "\n")
    _refresh_desktop(ctx)
    return done


def remove_terminals(ctx: Context, keep: set[str] = frozenset()) -> list[str]:
    """Take out the desktop entries, and the profiles of terminals not in `keep`."""
    removed = []
    for path in sorted((ctx.data_home / "applications").glob(f"{APP_ID}*.desktop")):
        path.unlink()
        removed.append(str(path))
    for name in saved_terminals(ctx) or []:
        if name not in keep:
            with contextlib.suppress(OSError, RuntimeError, subprocess.SubprocessError):
                TERMINALS[name].uninstall(ctx)
    if not keep:
        _state_file(ctx).unlink(missing_ok=True)
    _refresh_desktop(ctx)
    return removed


def _refresh_desktop(ctx: Context) -> None:
    if shutil.which("update-desktop-database"):
        ctx.run(["update-desktop-database", "-q", str(ctx.data_home / "applications")])


ICON_SIZES = (48, 64, 128, 256, 512)


def _icon_targets(ctx: Context) -> list[Path]:
    theme = ctx.data_home / "icons" / "hicolor"
    return [theme / "scalable" / "apps" / f"{APP_ID}.svg"] + [
        theme / f"{s}x{s}" / "apps" / f"{APP_ID}.png" for s in (*ICON_SIZES, 16, 22, 24, 32, 96, 192, 384)
    ]


def _png_size(path: Path) -> int | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width if width == height else None


def install_icon(ctx: Context, icon: str | None) -> str:
    """Put the app icon in the icon theme: yours if you've given one (--icon
    FILE, kept until --icon default), else the one in share/icons."""
    own_dir = ctx.data_home / "claude-launcher"
    if icon == "default":
        for old in own_dir.glob("icon.*"):
            old.unlink()
    elif icon:
        src = Path(icon).expanduser()
        if src.suffix.lower() not in (".svg", ".png") or not src.is_file():
            raise ValueError(f"{icon}: the icon must be an .svg or .png file")
        if src.suffix.lower() == ".png" and _png_size(src) is None:
            raise ValueError(f"{icon}: a PNG icon must be square")
        own_dir.mkdir(parents=True, exist_ok=True)
        for old in own_dir.glob("icon.*"):
            old.unlink()
        shutil.copyfile(src, own_dir / f"icon{src.suffix.lower()}")
    remove_icon(ctx)
    theme = ctx.data_home / "icons" / "hicolor"
    own = next(iter(sorted(own_dir.glob("icon.*"))), None)
    if own is not None:
        if own.suffix == ".svg":
            pairs = [(own, theme / "scalable" / "apps" / f"{APP_ID}.svg")]
        else:
            size = _png_size(own)
            pairs = [(own, theme / f"{size}x{size}" / "apps" / f"{APP_ID}.png")]
        what = f"your icon ({own})"
    else:
        pairs = [(ICONS_DIR / "claude-launcher.svg", theme / "scalable" / "apps" / f"{APP_ID}.svg")]
        pairs += [
            (ICONS_DIR / f"claude-launcher-{s}.png", theme / f"{s}x{s}" / "apps" / f"{APP_ID}.png") for s in ICON_SIZES
        ]
        what = "the default icon"
    for src, dest in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src)
    _refresh_icons(ctx)
    return what


def remove_icon(ctx: Context) -> None:
    for path in _icon_targets(ctx):
        if path.is_symlink() or path.exists():
            path.unlink()
    _refresh_icons(ctx)


def _refresh_icons(ctx: Context) -> None:
    theme = ctx.data_home / "icons" / "hicolor"
    if shutil.which("gtk-update-icon-cache") and (theme / "index.theme").exists():
        ctx.run(["gtk-update-icon-cache", "-q", str(theme)])
