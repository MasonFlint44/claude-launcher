"""What the installer sets up in WSL: claude-launcher and its hook in
~/.local/bin, as on Linux, and a "Claude Code (<distro>)" profile in Windows
Terminal, written through /mnt/c, that opens the launcher in this distro.
Native Windows' profile, if there is one, stays beside it."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...paths import REPO_DIR, THEME_FILE
from ...theme import Theme
from ..linux.setup import COMMANDS, bin_dir, link, unlink_ours
from ..windows import terminal
from ..windows.setup import DEFAULT_ICON


def _run(argv: list[str]) -> str:
    # From /mnt/c, so cmd.exe doesn't complain about starting in a UNC path.
    done = subprocess.run(argv, capture_output=True, text=True, timeout=20, cwd="/mnt/c", check=True)
    return done.stdout.strip()


class WslSetup:
    what = "the Windows Terminal profile"

    def __init__(self, env: Mapping[str, str] = os.environ, run: Callable[[list[str]], str] = _run):
        self.env = env
        self.run = run
        self.distro = env.get("WSL_DISTRO_NAME", "")  # WSL sets it in every distro's processes

    @property
    def done(self) -> str:
        return f'Restart Windows Terminal and open a "{self.profile.name}" tab, or run claude-launcher in any terminal.'

    @property
    def profile(self) -> terminal.Profile:
        guid = uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/MasonFlint44/claude-launcher/wsl/{self.distro}")
        return terminal.Profile(name=f"{terminal.PROFILE_NAME} ({self.distro})", guid="{" + str(guid) + "}")

    def hook_command(self) -> Path:
        return bin_dir(self.env) / "claude-session-hook"

    def _fragments(self) -> Path:
        local = self.run(["cmd.exe", "/c", "echo %LOCALAPPDATA%"])
        if not local or "%" in local:
            raise RuntimeError("could not find %LOCALAPPDATA% (is WSL interop on?)")
        return terminal.fragments_root(Path(self.run(["wslpath", "-u", local]))) / f"{terminal.APP}-wsl-{self.distro}"

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        if not self.distro:
            raise RuntimeError("WSL_DISTRO_NAME isn't set; run this inside WSL")
        if terminal_names:
            yield "warning: --terminal is for Linux desktops; in WSL the launcher opens in Windows Terminal"
        commands_dir = bin_dir(self.env)
        for name in COMMANDS:
            yield from link(REPO_DIR / "bin" / name, commands_dir / name)
        if str(commands_dir) not in self.env.get("PATH", "").split(os.pathsep):
            yield f"warning: {commands_dir} is not on your PATH"
        home = self.env.get("HOME") or str(Path.home())
        written = terminal.write(
            self._fragments(),
            f'wsl.exe -d {self.distro} --cd ~ -- bash -lc "~/.local/bin/claude-launcher || exec bash"',
            "\\\\wsl.localhost\\" + self.distro + home.replace("/", "\\"),
            Path(icon) if icon and icon != "default" else DEFAULT_ICON,
            Theme.load(THEME_FILE),
            windows_path=lambda p: self.run(["wslpath", "-w", str(p)]),
            profile=self.profile,
        )
        yield f'Added the "{self.profile.name}" profile to Windows Terminal ({written.parent})'

    def uninstall(self) -> Iterator[str]:
        for name in COMMANDS:
            yield from unlink_ours(bin_dir(self.env) / name)
        directory = self._fragments()
        if terminal.remove(directory):
            yield f'Removed the "{self.profile.name}" Windows Terminal profile ({directory})'

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {}
