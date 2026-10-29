"""The "Claude Code" profile for Windows Terminal, as a fragment: a JSON file
in %LOCALAPPDATA%\\Microsoft\\Windows Terminal\\Fragments\\<app>\\ that Windows
Terminal merges into its settings when it starts. Uninstalling is deleting
that directory; the user's settings.json is never edited. Native Windows and
WSL write the same profile, with their own command line.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...theme import Theme

APP = "claude-launcher"
PROFILE_NAME = "Claude Code"
SCHEME_NAME = "Claude Dark"
# Windows Terminal keys its settings for a profile (font, size, anything the
# user changes) by this GUID, so it never changes.
PROFILE_GUID = "{3b5e0c62-8a2d-4f7e-9c61-5d0f4e8b7a19}"
# Windows Terminal's names for the 16 colors, in order.
_NAMES = ("black", "red", "green", "yellow", "blue", "purple", "cyan", "white")
_ANSI = (*_NAMES, *(f"bright{n.capitalize()}" for n in _NAMES))


def fragments_dir(env: Mapping[str, str] = os.environ, app: str = APP) -> Path:
    local = env.get("LOCALAPPDATA") or os.path.join(env.get("USERPROFILE") or str(Path.home()), "AppData", "Local")
    return fragments_root(Path(local)) / app


def fragments_root(local_app_data: Path) -> Path:
    return local_app_data / "Microsoft" / "Windows Terminal" / "Fragments"


@dataclass(frozen=True)
class Profile:
    """Native Windows has one; WSL one per distro, beside it."""

    name: str = PROFILE_NAME
    guid: str = PROFILE_GUID


NATIVE = Profile()


def scheme(theme: Theme) -> dict[str, str]:
    """Claude Dark as a Windows Terminal color scheme, from themes/claude-dark."""
    out = {"name": SCHEME_NAME}
    for key, ours in (
        ("background", "background"),
        ("foreground", "foreground"),
        ("cursorColor", "cursor-color"),
        ("selectionBackground", "selection-background"),
    ):
        if ours in theme.colors:
            out[key] = theme.colors[ours]
    for i, name in enumerate(_ANSI):
        if i in theme.palette:
            out[name] = theme.palette[i]
    return out


def fragment(commandline: str, starting_directory: str, icon: str, theme: Theme, profile: Profile = NATIVE) -> dict:
    return {
        "profiles": [
            {
                "guid": profile.guid,
                "name": profile.name,
                "commandline": commandline,
                "startingDirectory": starting_directory,
                "icon": icon,
                "colorScheme": SCHEME_NAME,
                "cursorShape": "filledBox",
                # Quitting the picker closes the tab; if the launcher fails, the error stays up.
                "closeOnExit": "graceful",
                "hidden": False,
            }
        ],
        "schemes": [scheme(theme)],
    }


def write(
    directory: Path,
    commandline: str,
    starting_directory: str,
    icon: Path,
    theme: Theme,
    windows_path: Callable[[Path], str] = str,
    profile: Profile = NATIVE,
) -> Path:
    """Write the fragment and its icon into `directory`; returns the fragment.
    `windows_path` names a file the way Windows Terminal will (WSL writes
    through /mnt/c)."""
    directory.mkdir(parents=True, exist_ok=True)
    shown_icon = directory / ("icon" + icon.suffix.lower())
    for old in directory.glob("icon.*"):
        if old != shown_icon:
            old.unlink()
    shutil.copyfile(icon, shown_icon)
    path = directory / f"{APP}.json"
    data = fragment(commandline, starting_directory, windows_path(shown_icon), theme, profile)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8", newline="")
    os.replace(tmp, path)
    return path


def remove(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True
