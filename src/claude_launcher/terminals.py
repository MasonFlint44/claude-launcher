"""The terminals the "Claude Code" desktop launcher can open, and how.

Each terminal gets the launcher one of four ways:

- options: command-line options alone make every tab and split run the
  launcher, in a window of its own (Ghostty, kitty, WezTerm).
- profile: a "Claude Code" profile whose command is the launcher. New tabs
  made with the terminal's new-tab shortcut keep it, in GNOME Terminal and
  Ptyxis; Konsole opens new tabs with your default profile, but lists Claude
  Code in its New Tab menu.
- drop-down: a new tab in the drop-down terminal, with the launcher running
  in it (Guake).
- fallback: any other terminal, through the desktop's default-terminal
  setting (Terminal=true); the launcher runs in the first window only.

Profiles are the only changes made to a terminal's own settings, and
`uninstall()` takes them out again.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .paths import THEME_FILE

APP_ID = "io.github.masonflint44.ClaudeLauncher"
PROFILE_NAME = "Claude Code"
# The profile's ID, the same on every machine so a re-install finds it.
PROFILE_UUID = uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/masonflint44/claude-launcher#{APP_ID}")

Run = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=30)


@dataclass
class Context:
    launcher: Path  # bin/claude-launcher, as linked into ~/.local/bin
    core: Path  # libexec/claude-launcher-core
    home: Path
    data_home: Path  # ~/.local/share
    run: Run = _run
    theme: Path = THEME_FILE

    @classmethod
    def from_env(cls, launcher: Path, core: Path, env: Mapping[str, str] = os.environ) -> Context:
        home = Path(env.get("HOME") or Path.home())
        data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
        return cls(launcher=launcher, core=core, home=home, data_home=data)

    @property
    def command(self) -> str:
        """The launcher as one shell command: a login shell, for the PATH that
        finds claude, and a shell to fall back to if the launcher fails."""
        return "bash -lc " + shlex.quote(f"{shlex.quote(str(self.launcher))} || exec bash")

    @property
    def argv(self) -> list[str]:
        return shlex.split(self.command)


class Terminal:
    name = ""  # what --terminal takes
    label = ""
    program = ""  # what has to be on PATH
    how = ""  # "options", "profile", "drop-down" or "fallback"
    own_window = False  # its windows carry APP_ID, so the dock shows our icon for them

    def installed(self) -> bool:
        return shutil.which(self.program) is not None

    def exec_argv(self, ctx: Context) -> list[str]:
        """The desktop entry's Exec line, as arguments."""
        raise NotImplementedError

    def install(self, ctx: Context) -> None:
        """Make any changes the terminal needs (a profile, say)."""

    def uninstall(self, ctx: Context) -> None:
        """Undo install()."""


# --- Options ---------------------------------------------------------------


class Ghostty(Terminal):
    name, label, program, how, own_window = "ghostty", "Ghostty", "ghostty", "options", True

    def exec_argv(self, ctx: Context) -> list[str]:
        # `command`, unlike -e, applies to every new tab and split.
        return [
            "ghostty",
            f"--class={APP_ID}",
            f"--theme={ctx.theme}",
            f"--command={ctx.command}",
            "--quit-after-last-window-closed=true",
        ]


class Kitty(Terminal):
    name, label, program, how, own_window = "kitty", "kitty", "kitty", "options", True

    def exec_argv(self, ctx: Context) -> list[str]:
        return ["kitty", "--class", APP_ID, "-o", f"shell={ctx.command}"]


def _lua_string(text: str) -> str:
    return '"' + "".join(f"\\{c}" if c in '"\\' else c for c in text) + '"'


class WezTerm(Terminal):
    name, label, program, how, own_window = "wezterm", "WezTerm", "wezterm", "options", True

    def exec_argv(self, ctx: Context) -> list[str]:
        prog = "{" + ",".join(_lua_string(a) for a in ctx.argv) + "}"
        return ["wezterm", "--config", f"default_prog={prog}", "start", "--class", APP_ID, "--always-new-process"]


# --- Profiles --------------------------------------------------------------


def _gsettings_list(ctx: Context, schema: str, key: str) -> list[str] | None:
    done = ctx.run(["gsettings", "get", schema, key])
    if done.returncode != 0:
        return None
    text = done.stdout.strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    try:
        return json.loads(text.replace("'", '"'))
    except ValueError:
        return None


def _gsettings_set(ctx: Context, schema: str, key: str, value: object) -> None:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, list):
        text = "[" + ", ".join(_gvariant_string(v) for v in value) + "]"
    else:
        text = _gvariant_string(str(value))
    done = ctx.run(["gsettings", "set", schema, key, text])
    if done.returncode != 0:
        raise RuntimeError(f"gsettings set {schema} {key} failed: {done.stderr.strip()}")


def _gvariant_string(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


class _GSettingsProfile(Terminal):
    """A profile kept in GSettings: a list of profile IDs, plus one
    relocatable settings path per profile."""

    how = "profile"
    list_schema = ""
    list_key = ""
    profile_schema = ""  # with {id} where the profile's ID goes
    profile_id = ""

    def _profile(self) -> str:
        return self.profile_schema.format(id=self.profile_id)

    def profile_settings(self, ctx: Context) -> dict[str, object]:
        raise NotImplementedError

    def install(self, ctx: Context) -> None:
        ids = _gsettings_list(ctx, self.list_schema, self.list_key)
        if ids is None:
            raise RuntimeError(f"can't read {self.label}'s profiles (is {self.label} installed?)")
        for key, value in self.profile_settings(ctx).items():
            _gsettings_set(ctx, self._profile(), key, value)
        if self.profile_id not in ids:
            _gsettings_set(ctx, self.list_schema, self.list_key, [*ids, self.profile_id])

    def uninstall(self, ctx: Context) -> None:
        ids = _gsettings_list(ctx, self.list_schema, self.list_key)
        if ids and self.profile_id in ids:
            _gsettings_set(ctx, self.list_schema, self.list_key, [i for i in ids if i != self.profile_id])
        ctx.run(["gsettings", "reset-recursively", self._profile()])


class Ptyxis(_GSettingsProfile):
    name, label, program = "ptyxis", "Ptyxis", "ptyxis"
    list_schema, list_key = "org.gnome.Ptyxis", "profile-uuids"
    profile_schema = "org.gnome.Ptyxis.Profile:/org/gnome/Ptyxis/Profiles/{id}/"
    profile_id = PROFILE_UUID.hex

    def profile_settings(self, ctx: Context) -> dict[str, object]:
        return {"label": PROFILE_NAME, "use-custom-command": True, "custom-command": ctx.command}

    def install(self, ctx: Context) -> None:
        # Ptyxis uses the first profile when default-profile-uuid doesn't name
        # one, and a Ptyxis that's never been opened has none. Without a
        # default of its own, every Ptyxis window would open the launcher.
        ids = _gsettings_list(ctx, self.list_schema, self.list_key)
        if ids is None:
            raise RuntimeError("can't read Ptyxis's profiles (is Ptyxis installed?)")
        others = [i for i in ids if i != self.profile_id]
        done = ctx.run(["gsettings", "get", self.list_schema, "default-profile-uuid"])
        default = done.stdout.strip().strip("'") if done.returncode == 0 else ""
        if not others:
            others = [uuid.uuid4().hex]  # what Ptyxis would make on its first run
            _gsettings_set(ctx, self.list_schema, self.list_key, others)
        if default not in others:
            _gsettings_set(ctx, self.list_schema, "default-profile-uuid", others[0])
        super().install(ctx)

    def exec_argv(self, ctx: Context) -> list[str]:
        return ["ptyxis", "--new-window", f"--tab-with-profile={self.profile_id}"]


class GnomeTerminal(_GSettingsProfile):
    name, label, program = "gnome-terminal", "GNOME Terminal", "gnome-terminal"
    list_schema, list_key = "org.gnome.Terminal.ProfilesList", "list"
    profile_schema = "org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{id}/"
    profile_id = str(PROFILE_UUID)

    def profile_settings(self, ctx: Context) -> dict[str, object]:
        return {
            "visible-name": PROFILE_NAME,
            "use-custom-command": True,
            "custom-command": ctx.command,
            "exit-action": "close",
        }

    def exec_argv(self, ctx: Context) -> list[str]:
        return ["gnome-terminal", "--window", f"--profile={self.profile_id}"]


class Konsole(Terminal):
    name, label, program, how = "konsole", "Konsole", "konsole", "profile"

    def _file(self, ctx: Context) -> Path:
        return ctx.data_home / "konsole" / "ClaudeLauncher.profile"

    def install(self, ctx: Context) -> None:
        path = self._file(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[General]\nName={PROFILE_NAME}\nCommand={ctx.command}\nParent=FALLBACK/\n")

    def uninstall(self, ctx: Context) -> None:
        self._file(ctx).unlink(missing_ok=True)

    def exec_argv(self, ctx: Context) -> list[str]:
        return ["konsole", "--profile", str(self._file(ctx))]


# --- Drop-down ---------------------------------------------------------------


class Guake(Terminal):
    """A new Guake tab with the launcher in it. Guake's -e types a command into
    the current tab, and with -n it doesn't make the new tab at all, so it
    takes two calls. Run while Guake isn't, `guake` becomes Guake itself."""

    name, label, program, how = "guake", "Guake", "guake", "drop-down"
    bus_name = "org.guake3.RemoteControl"

    def exec_argv(self, ctx: Context) -> list[str]:
        return [str(ctx.core), "terminal", "open", self.name, str(ctx.launcher)]

    def running(self, ctx: Context) -> bool:
        done = ctx.run(
            [
                "gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.NameHasOwner", self.bus_name,
            ]
        )  # fmt: skip
        return done.returncode == 0 and "true" in done.stdout

    def open(self, ctx: Context, spawn: Callable[[Sequence[str]], object] | None = None) -> None:
        command = shlex.join(ctx.argv)
        if self.running(ctx):
            ctx.run(["guake", "-n", str(ctx.home)])
            ctx.run(["guake", "-e", command, "--show"])
            return
        # Guake's first tab takes the command. `guake` keeps running as Guake.
        (spawn or _spawn)(["guake", "-e", command, "--show"])
        for _ in range(50):  # wait for it, so a second click doesn't start another
            if self.running(ctx):
                return
            time.sleep(0.1)


def _spawn(argv: Sequence[str]) -> object:
    return subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# --- Fallback ----------------------------------------------------------------


class DefaultTerminal(Terminal):
    """Whatever terminal the desktop opens for Terminal=true entries."""

    name, label, program, how = "default", "your default terminal", "", "fallback"

    def installed(self) -> bool:
        return True

    def exec_argv(self, ctx: Context) -> list[str]:
        return ctx.argv


TERMINALS: dict[str, Terminal] = {
    t.name: t for t in (Ghostty(), Kitty(), WezTerm(), Ptyxis(), GnomeTerminal(), Konsole(), Guake(), DefaultTerminal())
}
# Picked when --terminal isn't given: the first that's installed. Drop-downs
# are only used when asked for.
AUTO_ORDER = ("ghostty", "kitty", "wezterm", "ptyxis", "gnome-terminal", "konsole", "default")


def pick(names: Sequence[str] | None) -> list[Terminal]:
    """The terminals to set up: `names` if given, else the first installed of AUTO_ORDER."""
    if names:
        unknown = [n for n in names if n not in TERMINALS]
        if unknown:
            raise ValueError(f"unknown terminal: {', '.join(unknown)} (one of: {', '.join(TERMINALS)})")
        return [TERMINALS[n] for n in dict.fromkeys(names)]
    return [next(TERMINALS[n] for n in AUTO_ORDER if TERMINALS[n].installed())]
