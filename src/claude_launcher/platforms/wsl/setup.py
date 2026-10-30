"""What the installer sets up in WSL: claude-launcher and its hook in
~/.local/bin, as on Linux, and a "Claude Code (<distro>)" profile in Windows
Terminal, written through Windows' drive, that opens the launcher in this
distro as this user. Native Windows' profile, if there is one, stays beside
it. The launcher works in any terminal, so when Windows can't be reached the
profile is skipped with a warning rather than failing the install."""

from __future__ import annotations

import os
import pwd
import shutil
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ...install import FAILURES
from ...paths import THEME_FILE
from ...theme import Theme
from ..linux import data_dir as linux_data_dir
from ..linux.setup import bin_dir, link_commands, unlink_commands
from ..windows import terminal
from ..windows.setup import DEFAULT_ICON
from . import interop


def _user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


class WslSetup:
    what = "the Windows Terminal profile"

    def __init__(
        self,
        env: Mapping[str, str] = os.environ,
        run: Callable[..., bytes] = interop.run,
        mounts: str = "/proc/mounts",
        user: Callable[[], str] = _user,
    ):
        self.env = env
        self.run = run  # a program's output; raises if it fails
        self.windows = interop.Interop(env, mounts, run)
        self.user = user
        self.data_dir = linux_data_dir(env)
        self._distro: str | None = None
        self.skipped = False  # the profile, this run

    @property
    def done(self) -> str:
        if self.skipped:
            return "Run claude-launcher in any terminal."
        return f'Restart Windows Terminal and open a "{self.profile.name}" tab, or run claude-launcher in any terminal.'

    @property
    def distro(self) -> str:
        """This distro's name: WSL_DISTRO_NAME, which sudo leaves out, else
        from where Windows sees / (\\\\wsl.localhost\\<distro>\\, or \\\\wsl$\\<distro>\\
        on older WSL). "" if neither says."""
        if self._distro is None:
            name = self.env.get("WSL_DISTRO_NAME", "")
            if not name:
                try:
                    root = self._wslpath("-w", "/")
                except FAILURES:
                    root = ""
                parts = root[2:].split("\\") if root.startswith("\\\\") else []
                name = parts[1] if len(parts) > 1 else ""
            self._distro = name
        return self._distro

    @property
    def profile(self) -> terminal.Profile:
        guid = uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/MasonFlint44/claude-launcher/wsl/{self.distro}")
        return terminal.Profile(name=f"{terminal.PROFILE_NAME} ({self.distro})", guid="{" + str(guid) + "}")

    def hook_command(self) -> Path:
        return bin_dir(self.env) / "claude-session-hook"

    def check(self) -> None:
        """Before the installer does anything: the profile is per distro."""
        if not self.distro:
            raise RuntimeError("can't tell which WSL distro this is; run install.sh in a WSL terminal, without sudo")

    def _wslpath(self, flag: str, path: str) -> str:
        return self.run(["wslpath", flag, path]).decode("utf-8", errors="surrogateescape").strip()

    def _local_app_data(self) -> str:
        # /d: no AutoRun commands (Clink, conda), whose output would come
        # first. /u: echo writes UTF-16, so a name like José comes through
        # whatever the console's code page is.
        out = self.windows.run("cmd.exe", ["/d", "/u", "/c", "echo", "%LOCALAPPDATA%"])
        text = out.decode("utf-16-le", errors="replace") if b"\0" in out else out.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or "%" in lines[-1]:
            raise RuntimeError("Windows didn't say where %LOCALAPPDATA% is")
        return lines[-1]

    def _fragments(self) -> Path:
        local = Path(self._wslpath("-u", self._local_app_data()))
        return terminal.fragments_root(local) / f"{terminal.APP}-wsl-{self.distro}"

    def _commandline(self) -> str:
        # Windows Terminal hands this to wsl.exe, which splits it as Windows
        # programs do: double quotes. Distro and user names can't hold one.
        return (
            f'wsl.exe -d "{self.distro}" -u "{self.user()}" --cd ~ -- '
            'bash -lc "~/.local/bin/claude-launcher || exec bash"'
        )

    def _icon(self, icon: str | None) -> Path:
        """The icon to show: --icon's (kept, so it can move), last time's, or
        ours. A .png, as on Windows."""
        kept = self.data_dir / "icon.png"
        if icon == "default":
            kept.unlink(missing_ok=True)
        elif icon:
            if not icon.lower().endswith(".png"):
                raise ValueError("for Windows Terminal, --icon takes a .png")
            if not os.path.isfile(icon):
                raise ValueError(f"not a file: {icon}")
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon, kept)
        return kept if kept.is_file() else DEFAULT_ICON

    def install(self, terminal_names: str | None, icon: str | None) -> Iterator[str]:
        self.check()
        if terminal_names:
            yield "warning: --terminal is for Linux desktops; in WSL the launcher opens in Windows Terminal"
        shown = self._icon(icon)
        yield from link_commands(self.env)
        home = self.env.get("HOME") or str(Path.home())
        try:
            written = terminal.write(
                self._fragments(),
                self._commandline(),
                self._wslpath("-w", home),
                shown,
                Theme.load(THEME_FILE),
                windows_path=lambda p: self._wslpath("-w", str(p)),
                profile=self.profile,
            )
        except FAILURES as e:
            self.skipped = True
            yield f"warning: {e}; the Windows Terminal profile was skipped"
            return
        yield f'Added the "{self.profile.name}" profile to Windows Terminal ({written.parent})'

    def uninstall(self) -> Iterator[str]:
        yield from unlink_commands(self.env)
        if not self.distro:
            yield (
                "warning: can't tell which WSL distro this is, so its Windows Terminal profile is still there; "
                "run install.sh --uninstall in a WSL terminal, without sudo"
            )
            return
        try:
            directory = self._fragments()
            if terminal.remove(directory):
                yield f'Removed the "{self.profile.name}" Windows Terminal profile ({directory})'
        except FAILURES as e:
            yield f"warning: {e}; the Windows Terminal profile is still there"

    @property
    def commands(self) -> Mapping[str, Callable[[list[str]], int]]:
        return {}
