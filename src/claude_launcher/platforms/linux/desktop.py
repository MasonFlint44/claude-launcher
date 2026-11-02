"""The desktop entries (and terminal profiles, see terminals.py) and the app
icon: what install.sh sets up on a Linux desktop."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from ...paths import REPO_DIR
from .terminals import APP_ID, TERMINALS, Context, Terminal, pick

ICONS_DIR = REPO_DIR / "share" / "icons"


def _desktop_quote(arg: str) -> str:
    """One argument for a desktop entry's Exec line. Readers undo two layers:
    the string escapes every desktop-entry value has (so a backslash is
    written twice), then the Exec line's own quoting."""
    if any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`%"):
        arg = '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
    arg = arg.replace("%", "%%")
    return arg.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


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
    chosen last time, or the first installed terminal. Returns what it did.
    The old entries stay until the new ones are in, so a failed run leaves
    a working launcher."""
    saved = saved_terminals(ctx)
    chosen = pick(None) if names == ["auto"] else pick(names or saved)
    missing = [t.label for t in chosen if not t.installed()]
    if missing:
        raise ValueError(f"not installed: {', '.join(missing)}")
    profiled = []
    try:
        for term in chosen:
            term.install(ctx)
            profiled.append(term.name)
    except BaseException:
        # Note what has a profile now, so uninstall takes it out.
        _save_terminals(ctx, list(dict.fromkeys([*(saved or []), *profiled])))
        raise
    done, written = [], set()
    for i, term in enumerate(chosen):
        path = entry_file(ctx, term, primary=i == 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(desktop_entry(term, ctx, primary=i == 0))
        os.replace(tmp, path)
        written.add(path)
        how = {"profile": " (with a Claude Code profile)", "drop-down": " (a new tab in it)"}.get(term.how, "")
        done.append(f"{path.name}: opens {term.label}{how}")
    _remove_entries(ctx, keep=written)
    _remove_profiles(saved or [], ctx, keep={t.name for t in chosen})
    _save_terminals(ctx, [t.name for t in chosen])
    _refresh_desktop(ctx)
    return done


def _save_terminals(ctx: Context, names: list[str]) -> None:
    state = _state_file(ctx)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(names) + "\n")


def _remove_entries(ctx: Context, keep: set[Path] = frozenset()) -> list[str]:
    removed = []
    for path in sorted((ctx.data_home / "applications").glob(f"{APP_ID}*.desktop")):
        if path not in keep:
            path.unlink()
            removed.append(str(path))
    return removed


def _remove_profiles(names: list[str], ctx: Context, keep: set[str] = frozenset()) -> None:
    for name in names:
        if name not in keep:
            with contextlib.suppress(OSError, RuntimeError, subprocess.SubprocessError):
                TERMINALS[name].uninstall(ctx)


def remove_terminals(ctx: Context) -> list[str]:
    """Take out the desktop entries and the profiles."""
    removed = _remove_entries(ctx)
    _remove_profiles(saved_terminals(ctx) or [], ctx)
    _state_file(ctx).unlink(missing_ok=True)
    _refresh_desktop(ctx)
    return removed


def _refresh_desktop(ctx: Context) -> None:
    if shutil.which("update-desktop-database"):
        ctx.run(["update-desktop-database", "-q", str(ctx.data_home / "applications")])


ICON_SIZES = (48, 64, 128, 256, 512)


def _icon_targets(ctx: Context) -> list[Path]:
    """The app icon, at every size it's in (a PNG of yours goes in by its size)."""
    return sorted((ctx.data_home / "icons" / "hicolor").glob(f"*/apps/{APP_ID}.*"))


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
        what = f"Installed your icon ({own})"
    else:
        pairs = [(ICONS_DIR / "claude-launcher.svg", theme / "scalable" / "apps" / f"{APP_ID}.svg")]
        pairs += [
            (ICONS_DIR / f"claude-launcher-{s}.png", theme / f"{s}x{s}" / "apps" / f"{APP_ID}.png") for s in ICON_SIZES
        ]
        what = "Installed the default icon"
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
