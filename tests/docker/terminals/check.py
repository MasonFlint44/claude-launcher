#!/usr/bin/env python3
"""Each terminal, live on the virtual display. Run by entrypoint.sh:

    check.py REPO [TERMINAL...]

Per terminal (any of terminals.TERMINALS, plus xterm for "any tab"):

- any tab: in the terminal's own default setup, claude-launcher switches the
  tab to Claude Dark and, on exit, puts the colors back exactly as they were.
- install: install.sh --terminal NAME works, the desktop entry validates, and
  the profile is there (GSettings, or Konsole's profile file).
- picker: the desktop entry's Exec line opens the picker in Claude Dark.
- new tab: the terminal's new-tab shortcut (Guake: a second "Claude Code")
  does what terminals.py says it does.
- uninstall: install.sh --uninstall takes the profile out again.

Colors are read off screenshots: the commonest color in the window is its
background, and the next is the text, shown as a row of full blocks. The
expected ones come from themes/claude-dark. Exit status: 0 when every check
that ran passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "src"))

from claude_launcher.terminals import APP_ID, PROFILE_UUID, TERMINALS  # noqa: E402
from claude_launcher.theme import Theme  # noqa: E402

HOME = Path.home()
CORE = REPO / "libexec" / "claude-launcher-core"
INSTALL = REPO / "install.sh"
ENTRY = HOME / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
LOGS = Path("/tmp/logs")
THEME = Theme.load(REPO / "themes" / "claude-dark")
DARK_BG = THEME.colors["background"]
PTYXIS_PALETTE = HOME / ".local/share/org.gnome.Ptyxis/palettes" / f"{APP_ID}.palette"

ALL = ["ghostty", "kitty", "wezterm", "ptyxis", "gnome-terminal", "konsole", "guake", "xterm", "default"]
# A terminal added to the table has to be added here too.
assert set(ALL) - {"xterm"} == set(TERMINALS), "check.py doesn't know every terminal in terminals.TERMINALS"
COLUMNS = ["any tab", "install", "picker", "new tab", "uninstall"]
PROGRAMS = ["ghostty", "kitty", "wezterm", "wezterm-gui", "ptyxis", "ptyxis-agent", "gnome-terminal-server",
            "konsole", "guake", "xterm", "fzf"]  # fmt: skip

# The window classes each terminal's windows have (ours, or its own).
CLASSES = {
    "ghostty": f"ghostty|{APP_ID}",
    "kitty": f"kitty|{APP_ID}",
    "wezterm": f"wezterm|{APP_ID}",
    "ptyxis": "ptyxis",
    "gnome-terminal": "Gnome-terminal",
    "konsole": "konsole",
    "guake": "Guake",
    "xterm": "XTerm",
}
# How to run a command in a new window, in the terminal's default setup.
RUN_IN = {
    "ghostty": ["ghostty", "-e"],
    "kitty": ["kitty"],
    "wezterm": ["wezterm", "start", "--always-new-process", "--"],
    "ptyxis": ["ptyxis", "--new-window", "--"],
    "gnome-terminal": ["gnome-terminal", "--window", "--"],
    "konsole": ["konsole", "-e"],
    "guake": ["guake", "--show", "-e"],  # types it into Guake's first tab
    "xterm": ["xterm", "-e"],
}
NEW_TAB_KEY = "ctrl+shift+t"
# Window titles: Konsole's default tab title ("%d : %n") leaves out the title
# programs set, and Guake's window is always "Guake!".
SHOWS_TITLE = {"ghostty", "kitty", "wezterm", "ptyxis", "gnome-terminal", "xterm"}


class Fail(Exception):
    pass


class Skip(Exception):
    pass


def run(*argv: str | Path, check: bool = False) -> subprocess.CompletedProcess:
    done = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=60)
    if check and done.returncode != 0:
        raise Fail(f"{' '.join(map(str, argv[:3]))} failed: {(done.stderr or done.stdout).strip()[-300:]}")
    return done


_spawned = 0


def spawn(argv: list[str]) -> subprocess.Popen:
    global _spawned
    _spawned += 1
    log = open(LOGS / f"{_spawned:02d}-{Path(argv[0]).name}.log", "w")  # noqa: SIM115
    log.write(" ".join(argv) + "\n")
    log.flush()
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=HOME)


def wait(what: str, cond: Callable[[], object], timeout: float = 20) -> object:
    end = time.monotonic() + timeout
    while True:
        value = cond()
        if value:
            return value
        if time.monotonic() > end:
            raise Fail(f"timed out waiting for {what}")
        time.sleep(0.1)


def settle(cond: Callable[[], object], hold: float = 1.5, timeout: float = 20) -> None:
    """Wait until cond() has held for `hold` seconds (a screen that's done drawing)."""
    end, since = time.monotonic() + timeout, None
    while time.monotonic() < end:
        if cond():
            since = since or time.monotonic()
            if time.monotonic() - since >= hold:
                return
        else:
            since = None
        time.sleep(0.1)
    raise Fail("the screen never settled")


def windows(pattern: str) -> list[str]:
    return run("xdotool", "search", "--onlyvisible", "--class", pattern).stdout.split()


def geometry(win: str) -> dict[str, int]:
    out = run("xdotool", "getwindowgeometry", "--shell", win).stdout
    return {k: int(v) for k, v in (line.split("=") for line in out.split()) if k in "XYWIDTHEIGHT"}


def main_window(term: str) -> str | None:
    """The terminal's biggest visible window."""
    found = [(g["WIDTH"] * g["HEIGHT"], w) for w in windows(CLASSES[term]) if (g := geometry(w))]
    return max(found)[1] if found else None


def colors(win: str) -> list[str]:
    """The window's colors, commonest first, below where any title bar is."""
    g = geometry(win)
    top = g["HEIGHT"] // 6
    crop = f"{g['WIDTH']}x{g['HEIGHT'] - top}+{g['X']}+{g['Y'] + top}"
    out = run("import", "-window", "root", "-crop", crop, "+repage", "-format", "%c", "histogram:info:-").stdout
    counted = []
    for line in out.splitlines():
        count, _, rest = line.strip().partition(":")
        hexes = [w for w in rest.split() if w.startswith("#")]
        if count.isdigit() and hexes:
            counted.append((int(count), "#" + hexes[0][1:7].lower()))
    return [c for _, c in sorted(counted, reverse=True)]


def close(a: str, b: str) -> bool:
    return all(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) <= 2 for i in (1, 3, 5))


def wait_dark(win: str) -> None:
    try:
        wait("Claude Dark", lambda: close(colors(win)[0], DARK_BG), timeout=10)
    except Fail:
        raise Fail(f"the background stayed {colors(win)[0]}, not Claude Dark's {DARK_BG}") from None


def pickers() -> int:
    return len(run("pgrep", "-x", "fzf").stdout.split())


def titled(text: str) -> bool:
    return bool(run("xdotool", "search", "--onlyvisible", "--name", text).stdout.strip())


def press(win: str, *keys: str) -> None:
    run("xdotool", "windowactivate", "--sync", win)
    for key in keys:
        run("xdotool", "key", "--clearmodifiers", key)
        time.sleep(0.3)


def cleanup() -> None:
    for name in PROGRAMS:
        run("pkill", "-x", name[:15])  # process names are cut to 15 characters
    run("pkill", "-f", "/bin/claude-launcher")  # the launcher, not this script
    wait("the terminals to close", lambda: not any(windows(p) for p in CLASSES.values()), timeout=10)


def gsettings(schema: str, key: str) -> str:
    return run("gsettings", "get", schema, key).stdout.strip()


# --- The checks -------------------------------------------------------------

LOGS.mkdir(exist_ok=True)
ANY_TAB = Path("/tmp/any-tab.sh")
GO, EXITED = Path("/tmp/go"), Path("/tmp/exited")
ANY_TAB.write_text(
    "#!/bin/bash\n"
    # The same screen before and after: cleared, then three rows of text.
    "blocks() { printf '\\e[H\\e[2J\\n'; for _ in 1 2 3; do printf '%.0s\\u2588' {1..60}; printf '\\n'; done; }\n"
    "printf '\\e]0;any-tab\\a'; blocks\n"
    f"while [ ! -e {GO} ]; do sleep 0.1; done\n"
    f"{REPO}/bin/claude-launcher; echo $? > {EXITED}; blocks\n"
    "exec sleep 600\n"
)
ANY_TAB.chmod(0o755)


def check_any_tab(term: str) -> str:
    """Claude Dark while the launcher runs, then exactly the old colors back."""
    GO.unlink(missing_ok=True)
    EXITED.unlink(missing_ok=True)
    spawn([*RUN_IN[term], str(ANY_TAB)])
    win = wait("the window", lambda: main_window(term))
    settle(lambda: len(colors(win)) >= 2)
    before = colors(win)[:2]
    run("import", "-window", "root", LOGS / f"{term}-before.png")
    if close(before[0], DARK_BG):
        raise Skip("the default background is already Claude Dark's")
    GO.touch()
    wait("the picker", lambda: pickers() == 1)
    if term == "ptyxis":
        # Ptyxis paints its own background; only the text colors change (README).
        time.sleep(1)
        if close(colors(win)[0], DARK_BG):
            raise Fail("Ptyxis shows the launcher's background now; update the README and this check")
    else:
        wait_dark(win)
    if term in SHOWS_TITLE:
        wait("the title Claude Code", lambda: titled("^Claude Code$"), timeout=5)
    press(win, "Escape", "Escape")
    wait("the launcher to exit", EXITED.exists)
    if EXITED.read_text().strip() != "0":
        raise Fail(f"the launcher exited {EXITED.read_text().strip()}")
    try:
        settle(lambda: colors(win)[:2] == before, hold=0.5, timeout=8)
    except Fail:
        raise Fail(f"colors after exit {colors(win)[:2]}, before {before}") from None
    kept = " (background kept, as documented)" if term == "ptyxis" else ""
    return f"pass{kept} (back to {before[0]}, {before[1]})"


def check_install(term: str) -> str:
    run(INSTALL, "--terminal", term, check=True)
    if not ENTRY.exists():
        raise Fail("no desktop entry")
    run("desktop-file-validate", ENTRY, check=True)
    if term == "ptyxis":
        if PROFILE_UUID.hex not in gsettings("org.gnome.Ptyxis", "profile-uuids"):
            raise Fail("the profile isn't in profile-uuids")
        if PROFILE_UUID.hex in gsettings("org.gnome.Ptyxis", "default-profile-uuid"):
            raise Fail("ours became the default profile")
        if not PTYXIS_PALETTE.exists():
            raise Fail("no Claude Dark palette file")
        profile = f"org.gnome.Ptyxis.Profile:/org/gnome/Ptyxis/Profiles/{PROFILE_UUID.hex}/"
        if APP_ID not in gsettings(profile, "palette"):
            raise Fail("the profile doesn't use the Claude Dark palette")
    elif term == "gnome-terminal":
        if str(PROFILE_UUID) not in gsettings("org.gnome.Terminal.ProfilesList", "list"):
            raise Fail("the profile isn't in the profiles list")
    elif term == "konsole" and not (HOME / ".local/share/konsole/ClaudeLauncher.profile").exists():
        raise Fail("no Konsole profile file")
    return "pass"


def exec_line() -> list[str]:
    from gi.repository import GLib

    keys = GLib.KeyFile()
    keys.load_from_file(str(ENTRY), GLib.KeyFileFlags.NONE)
    return GLib.shell_parse_argv(keys.get_string("Desktop Entry", "Exec"))[1]


OPENED: set[str] = set()  # terminals whose desktop entry opened the picker


def check_picker(term: str) -> str:
    """The entry opens the picker, in Claude Dark."""
    spawn(exec_line())
    wait("the picker", lambda: pickers() == 1)
    OPENED.add(term)
    win = wait("the window", lambda: main_window(term))
    wait_dark(win)
    if term in SHOWS_TITLE:
        wait("the title Claude Code", lambda: titled("^Claude Code$"), timeout=5)
    return "pass" if term in SHOWS_TITLE else "pass (title not shown by this terminal)"


def check_new_tab(term: str) -> str:
    if term == "guake":
        # The second click: Guake is running now, so this is the two-call path.
        spawn(exec_line())
        wait("a second picker", lambda: pickers() == 2)
        return "pass (second launch adds a tab)"
    win = main_window(term)
    tabs = len(run("pgrep", "-P", konsole_pid()).stdout.split()) if term == "konsole" else 0
    press(win, NEW_TAB_KEY)
    if term == "konsole":
        # Konsole opens new tabs with your default profile (see terminals.py).
        wait("a new tab", lambda: len(run("pgrep", "-P", konsole_pid()).stdout.split()) > tabs)
        time.sleep(1)
        if pickers() != 1:
            raise Fail("Konsole's new tab ran the launcher")
        return "pass (opens the default profile, as documented)"
    wait("a picker in the new tab", lambda: pickers() == 2)
    return "pass"


def konsole_pid() -> str:
    return run("pgrep", "-x", "konsole").stdout.split()[0]


def check_uninstall(term: str) -> str:
    run(INSTALL, "--uninstall", check=True)
    if ENTRY.exists():
        raise Fail("the desktop entry is still there")
    if term == "ptyxis":
        ids = gsettings("org.gnome.Ptyxis", "profile-uuids")
        default = gsettings("org.gnome.Ptyxis", "default-profile-uuid").strip("'")
        if PROFILE_UUID.hex in ids:
            raise Fail("the profile is still in profile-uuids")
        if not default or default not in ids:
            raise Fail(f"default profile {default!r} isn't one of {ids}")
        if PTYXIS_PALETTE.exists():
            raise Fail("the palette file is still there")
    elif term == "gnome-terminal":
        if str(PROFILE_UUID) in gsettings("org.gnome.Terminal.ProfilesList", "list"):
            raise Fail("the profile is still in the profiles list")
    elif term == "konsole" and (HOME / ".local/share/konsole/ClaudeLauncher.profile").exists():
        raise Fail("the Konsole profile file is still there")
    return "pass"


def check(term: str) -> dict[str, str]:
    results: dict[str, str] = {}
    if term == "guake":
        # Guake draws its background at 90% opacity by default, which would
        # blend Claude Dark with whatever is behind it.
        run("gsettings", "set", "guake.style.background", "transparency", "100", check=True)
    if term == "default":
        return dict.fromkeys(COLUMNS, "skip: opens the desktop's default terminal; there's no desktop here")
    steps = [("any tab", check_any_tab), ("install", check_install), ("picker", check_picker),
             ("new tab", check_new_tab), ("uninstall", check_uninstall)]  # fmt: skip
    if term == "xterm":  # not a launcher terminal, only "any tab"
        steps = steps[:1]
    for column, step in steps:
        print(f"-- {term}: {column}", flush=True)
        try:
            if column in ("any tab", "install", "uninstall"):
                cleanup()  # only this step's windows on screen
            if column in ("picker", "new tab") and results.get("install", "").startswith("FAIL"):
                raise Skip("install failed")
            if column == "new tab" and term not in OPENED:
                raise Skip("the picker didn't open")
            results[column] = step(term)
        except Skip as e:
            results[column] = f"skip: {e}"
        except Fail as e:
            results[column] = f"FAIL: {e}"
            run("import", "-window", "root", LOGS / f"{term}-{column.replace(' ', '-')}.png")
        except Exception as e:  # a broken check is a failure, not a crash
            results[column] = f"FAIL: {type(e).__name__}: {e}"
        print(f"   {results[column]}", flush=True)
    try:
        cleanup()
    except Fail as e:
        results["uninstall"] += f" (then {e})"
    for column in COLUMNS:
        results.setdefault(column, "-")
    return results


def main() -> int:
    names = sys.argv[2:] or ALL
    unknown = [n for n in names if n not in ALL]
    if unknown:
        print(f"unknown terminal: {', '.join(unknown)} (one of: {', '.join(ALL)})", file=sys.stderr)
        return 2
    os.environ["PATH"] = f"{HOME}/.local/bin:{os.environ['PATH']}"
    table = {name: check(name) for name in names}
    print()
    width = max(map(len, names))
    print(f"{'':{width}}  " + "  ".join(f"{c:9}" for c in COLUMNS))
    for name, results in table.items():
        print(f"{name:{width}}  " + "  ".join(f"{results[c].split(':')[0].split(' ')[0]:9}" for c in COLUMNS))
    notes = [(n, c, r) for n, rs in table.items() for c, r in rs.items() if r not in ("pass", "-")]
    if notes:
        print()
        for name, column, result in notes:
            print(f"{name} {column}: {result}")
    failed = any(r.startswith("FAIL") for rs in table.values() for r in rs.values())
    print("\nFAIL" if failed else "\nPASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
