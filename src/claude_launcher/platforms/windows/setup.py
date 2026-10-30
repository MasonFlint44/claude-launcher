"""What the installer sets up on Windows: claude-launcher.cmd on PATH, the
"Claude Code" Windows Terminal profile (terminal.py), with Claude Dark, and
the files those and the session hook run, in the launcher's data directory:

- claude-launcher.cmd and claude-session-hook.cmd find Python each time they
  run (`_RUN`), so a Python that's upgraded, moved or replaced just works.
  That costs about 20 ms for cmd to start (the hook takes about 57 ms, where
  a fixed python.exe took 36), once at each session's start and end.
- Each runs the entry script beside it (%~dp0), which checks Python's
  version and runs the repo's script. The .cmd files are ASCII and hold no
  paths: cmd reads a batch file in the console's code page, so a path with
  "é" in it would come out wrong. The entry scripts hold the repo's path,
  which Python reads as UTF-8 (and which is written ASCII-escaped anyway).

Nothing on a command line cmd parses holds the user's own text either; see
`hook_command` and `_SHIM`."""

from __future__ import annotations

import ntpath
import os
import re
import shutil
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import REPO_DIR, THEME_FILE
from ...theme import Theme
from . import terminal

LAUNCHER = REPO_DIR / "bin" / "claude-launcher"
HOOK = REPO_DIR / "bin" / "claude-session-hook.py"
DEFAULT_ICON = REPO_DIR / "share" / "icons" / "claude-launcher-256.png"
MIN_PYTHON = (3, 10)
# An entry script's exit code when its Python is too old: the .cmd tries the
# next Python. Unlike any code Claude or Python exits with.
TOO_OLD = 9310
# Set while there's another Python left to try, so a too-old one says nothing.
_FALLBACK = "CLAUDE_LAUNCHER_FALLBACK"
_SHIM_MARK = "rem claude-launcher: "

# Runs the entry script `name` beside it with Python 3.10 or newer, found each
# time: the first python.exe on PATH, unless that's in WindowsApps (the
# Microsoft Store's stand-in, which only offers to install Python, or a Store
# or install-manager Python's alias), then the py launcher, then that
# WindowsApps python.exe after all (whose message, if it's the stand-in, says
# how to get Python). A Python the entry script finds too old exits TOO_OLD,
# and the next one is tried; only that costs a second Python start. "-"
# stands for none: cmd can't take part of a variable that isn't set.
# DisableDelayedExpansion: it may be on by default (the registry), or from
# the hook's cmd /v:on, and would eat a "!" in a path.
_RUN = """@echo off
{mark}runs {name} beside it with Python {major}.{minor} or newer
setlocal DisableDelayedExpansion
set "A=-" & set "B=-" & set "C=-"
for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "A=%%~$PATH:P"
if /i not "%A:\\WindowsApps\\=%"=="%A%" (set "C=%A%" & set "A=-")
for %%P in (py.exe) do if not "%%~$PATH:P"=="" set "B=%%~$PATH:P"
set "{fallback}="
if not "%B%%C%"=="--" set "{fallback}=1"
if "%A%"=="-" goto py
"%A%" {options}"%~dp0{name}" %*
if not "%errorlevel%"=="{too_old}" exit /b %errorlevel%
:py
set "{fallback}="
if not "%C%"=="-" set "{fallback}=1"
if "%B%"=="-" goto store
"%B%" -3 {options}"%~dp0{name}" %*
if not "%errorlevel%"=="{too_old}" exit /b %errorlevel%
:store
set "{fallback}="
if "%C%"=="-" goto none
"%C%" {options}"%~dp0{name}" %*
exit /b %errorlevel%
:none
if not "%A%%B%"=="--" exit /b {too_old}
echo claude-launcher needs Python {major}.{minor} or newer: https://www.python.org/downloads/ 1>&2
exit /b 9009
"""

# The entry script: as old a Python as can run it says what's wrong (or lets
# the .cmd try the next one), then the repo's script runs as __main__ with its
# own __file__, as if run directly.
_ENTRY = """# claude-launcher: runs {target_name} from the repo, with Python {major}.{minor} or newer
import os
import sys

if sys.version_info < ({major}, {minor}):
    if not os.environ.get("{fallback}"):
        sys.stderr.write(
            "claude-launcher needs Python {major}.{minor} or newer (found %d.%d at %s)\\n"
            % (sys.version_info[0], sys.version_info[1], sys.executable)
        )
    sys.exit({too_old})
os.environ.pop("{fallback}", None)
TARGET = {target}
with open(TARGET, "rb") as f:
    code = compile(f.read(), TARGET, "exec")
exec(code, {{"__name__": "__main__", "__file__": TARGET}})
"""

# claude-launcher.cmd on PATH: hands over to the one in the data directory,
# found the way the launcher finds that directory (data_dir), so it holds no
# path. Without `call`, which would expand a % in the path a second time;
# a batch file run without it doesn't come back, so nothing after it runs.
_SHIM = """@echo off
{mark}runs the launcher from its data directory
setlocal DisableDelayedExpansion
if not defined LOCALAPPDATA goto profile
if exist "%LOCALAPPDATA%\\{cmd}" "%LOCALAPPDATA%\\{cmd}" %*
goto missing
:profile
if exist "%USERPROFILE%\\AppData\\Local\\{cmd}" "%USERPROFILE%\\AppData\\Local\\{cmd}" %*
:missing
echo claude-launcher isn't set up for this user; run install.ps1 again 1>&2
exit /b 9009
"""
# What may follow an environment variable in a command line: nothing cmd or
# Windows Terminal would treat specially, and no spaces.
_PLAIN_TAIL = re.compile(r"(\\[A-Za-z0-9._-]+)+")


def run_cmd(name: str, options: str = "") -> str:
    """A .cmd that runs the entry script `name` beside it with Python 3.10+.
    Python's `options` (like "-I") go before the script. Marked as ours."""
    text = _RUN.format(
        mark=_SHIM_MARK,
        name=name,
        options=options + " " if options else "",
        major=MIN_PYTHON[0],
        minor=MIN_PYTHON[1],
        fallback=_FALLBACK,
        too_old=TOO_OLD,
    )
    return text.replace("\n", "\r\n")


def entry_script(target: Path) -> str:
    """The entry script that runs `target`, ASCII only."""
    return _ENTRY.format(
        target_name=target.name,
        target=ascii(str(target)),
        major=MIN_PYTHON[0],
        minor=MIN_PYTHON[1],
        fallback=_FALLBACK,
        too_old=TOO_OLD,
    )


def shim_cmd() -> str:
    return _SHIM.format(mark=_SHIM_MARK, cmd="claude-launcher\\claude-launcher.cmd").replace("\n", "\r\n")


def _getenv(env: Mapping[str, str], name: str) -> str | None:
    """Windows' environment ignores case (SystemRoot, SYSTEMROOT); a plain
    mapping, as in tests, doesn't."""
    if name in env:
        return env[name]
    folded = name.upper()
    return next((v for k, v in env.items() if k.upper() == folded), None)


def env_form(path: Path, env: Mapping[str, str]) -> tuple[str, str] | None:
    """`path` as (an environment variable, the rest), when it's under
    %LOCALAPPDATA% (or %USERPROFILE%, which it defaults to being under) and
    the rest is plain: so a command line can name it without the user's own
    text (a name with & or é in it, say)."""
    text = str(path).replace("/", "\\")
    for name in ("LOCALAPPDATA", "USERPROFILE"):
        value = _getenv(env, name)
        if not value:
            continue
        base = value.replace("/", "\\").rstrip("\\")
        if text.lower().startswith(base.lower() + "\\"):
            rest = text[len(base) :]
            if _PLAIN_TAIL.fullmatch(rest):
                return name, rest
    return None


def _bin_dir(env: Mapping[str, str] = os.environ) -> Path:
    """Where Claude Code's own installer puts claude.exe, and adds to PATH."""
    return Path(env.get("USERPROFILE") or str(Path.home())) / ".local" / "bin"


def _same_dir(a: str, b: str) -> bool:
    return a.replace("/", "\\").rstrip("\\").lower() == b.replace("/", "\\").rstrip("\\").lower()


class WindowsSetup:
    what = "the Windows Terminal profile"
    done = 'Restart Windows Terminal and open a "Claude Code" tab, or run claude-launcher in any terminal.'

    def __init__(self, data_dir: Path, env: Mapping[str, str] = os.environ):
        self.data_dir = data_dir
        self.env = env

    @property
    def _hook_cmd(self) -> Path:
        return self.data_dir / "claude-session-hook.cmd"  # the name tells settings.py it's ours

    @property
    def _launcher_cmd(self) -> Path:
        return self.data_dir / "claude-launcher.cmd"

    def hook_command(self) -> list[str]:
        """cmd running the hook's .cmd, named as !LOCALAPPDATA!\\claude-launcher\\
        claude-session-hook.cmd, so the command line holds none of the user's
        own text. Claude quotes an argument only when it has a space, tab or
        quote, and cmd would split a path with & or ^ in it at its parse,
        which comes before delayed expansion (/v:on) puts the path in. A
        command that starts with a drive (C:) isn't split at its spaces
        after that, and the value isn't parsed again, so % and ! in it are
        kept too. System32's cmd.exe, not %ComSpec%, which can be another
        shell (TCC) that reads /d and /c its own way."""
        root = _getenv(self.env, "SystemRoot")
        cmd = ntpath.join(root, "System32", "cmd.exe") if root else "cmd.exe"
        form = env_form(self._hook_cmd, self.env)
        if form:
            name, rest = form
            return [cmd, "/d", "/v:on", "/c", f"!{name}!{rest}"]
        # Somewhere else (only tests put it there): the path, which cmd /c
        # takes as it is if Claude quotes it, and it's the only quoted part.
        return [cmd, "/d", "/c", str(self._hook_cmd)]

    def _profile_command(self) -> str:
        """The profile's command line. Windows Terminal expands %VARIABLES%
        in it, so the path is named by its variable, as the hook's is."""
        form = env_form(self._launcher_cmd, self.env)
        return f'"%{form[0]}%{form[1]}"' if form else f'"{self._launcher_cmd}"'

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        if terminal_names:
            yield "warning: --terminal is for Linux; on Windows the launcher opens in Windows Terminal"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "claude-session-hook.py": entry_script(HOOK),
            "claude-launcher.py": entry_script(LAUNCHER),
            self._hook_cmd.name: run_cmd("claude-session-hook.py", "-I"),
            self._launcher_cmd.name: run_cmd("claude-launcher.py"),
        }
        for name, text in files.items():
            (self.data_dir / name).write_text(text, encoding="ascii", newline="")
        shim = _bin_dir(self.env) / "claude-launcher.cmd"
        if shim.exists() and _SHIM_MARK not in shim.read_text(encoding="utf-8", errors="replace"):
            yield f"warning: {shim} isn't one of ours; leaving it alone"
        else:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text(shim_cmd(), encoding="ascii", newline="")
            yield f"Wrote {shim}"
        path = (_getenv(self.env, "PATH") or "").split(";")
        if not any(_same_dir(str(shim.parent), p) for p in path):
            yield f"warning: {shim.parent} is not on your PATH"
        shown = self._icon(icon)
        written = terminal.write(
            terminal.fragments_dir(self.env),
            self._profile_command(),
            "%USERPROFILE%",
            shown,
            Theme.load(THEME_FILE),
        )
        yield f'Added the "Claude Code" profile to Windows Terminal ({written.parent})'

    def _icon(self, icon: str | None) -> Path:
        """The icon to show: --icon's (kept, so it can move), last time's, or ours."""
        kept = self.data_dir / "icon.png"
        if icon and icon.lower() == "default":
            kept.unlink(missing_ok=True)
        elif icon:
            if not icon.lower().endswith(".png"):
                raise ValueError("on Windows, --icon takes a .png")
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon, kept)
        return kept if kept.is_file() else DEFAULT_ICON

    def uninstall(self) -> Iterator[str]:
        shim = _bin_dir(self.env) / "claude-launcher.cmd"
        if shim.is_file() and _SHIM_MARK in shim.read_text(encoding="utf-8", errors="replace"):
            shim.unlink()
            yield f"Removed {shim}"
        directory = terminal.fragments_dir(self.env)
        if terminal.remove(directory):
            yield f'Removed the "Claude Code" Windows Terminal profile ({directory})'

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {}
