"""WSL's installer and its reach into Windows (interop), with Windows faked:
a directory standing in for the C: drive, a fake /proc/mounts, and a runner
that answers for cmd.exe, wslpath, wevtutil.exe and whoami.exe. They run on
any Linux, so CI covers them."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_launcher import install
from claude_launcher.platforms.windows import terminal
from claude_launcher.platforms.windows.setup import DEFAULT_ICON
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.platforms.wsl.interop import Interop
from claude_launcher.platforms.wsl.setup import WslSetup
from conftest import FakeHost

LOCAL = "C:\\Users\\José\\AppData\\Local"


class FakeWindows:
    """Windows' side: C: mounted at `drive`, and what its programs say."""

    def __init__(self, tmp_path: Path, mount_at: str = "c"):
        self.drive = tmp_path / mount_at
        self.system32 = self.drive / "Windows" / "System32"
        self.system32.mkdir(parents=True)
        (self.system32 / "cmd.exe").touch()
        self.mounts = tmp_path / "mounts"
        escaped = str(self.drive).replace(" ", "\\040")
        self.mounts.write_text(
            "none /usr/lib/wsl/drivers 9p ro,aname=drivers 0 0\n"
            f"C:\\134 {escaped} 9p rw,noatime,aname=drvfs;path=C:\\134 0 0\n"
            "/dev/sdc / ext4 rw 0 0\n"
        )
        self.cmd_out = (LOCAL + "\r\n").encode("utf-16-le")
        self.fail: dict[str, BaseException] = {}
        self.calls: list[tuple[list[str], Path | None]] = []
        self.distro_root = "\\\\wsl.localhost\\Ubuntu\\"

    def local(self) -> Path:
        return self.drive / "Users" / "José" / "AppData" / "Local"

    def run(self, argv, cwd=None) -> bytes:
        argv = list(argv)
        self.calls.append((argv, cwd))
        program = Path(argv[0]).name
        if program in self.fail:
            raise self.fail[program]
        if program == "cmd.exe":
            assert argv[0] == str(self.system32 / "cmd.exe") and cwd == self.system32
            assert argv[1:] == ["/d", "/u", "/c", "echo", "%LOCALAPPDATA%"]
            return self.cmd_out
        if argv[:3] == ["wslpath", "-u", LOCAL]:
            return (str(self.local()) + "\n").encode()
        if argv[:3] == ["wslpath", "-w", "/"]:
            return (self.distro_root + "\n").encode()
        if argv[:2] == ["wslpath", "-w"]:
            path = argv[2]
            if path.startswith(str(self.drive)):
                return ("C:" + path[len(str(self.drive)) :].replace("/", "\\") + "\n").encode()
            return (self.distro_root + path.lstrip("/").replace("/", "\\") + "\n").encode()
        if program == "wevtutil.exe":
            return b""
        if program == "whoami.exe":
            return b'"desktop\\me","S-1-5-21-1-2-3-1001"\r\n'
        raise AssertionError(argv)


@pytest.fixture
def windows(tmp_path):
    return FakeWindows(tmp_path)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "users" / "me"
    home.mkdir(parents=True)
    return home


def make(windows, home, **env):
    base = {"WSL_DISTRO_NAME": "Ubuntu", "HOME": str(home), "PATH": f"{home}/.local/bin:/usr/bin:/bin"}
    env = {k: v for k, v in {**base, **env}.items() if v is not None}
    return WslSetup(env, windows.run, str(windows.mounts), user=lambda: "me")


def fragment_dir(windows, distro="Ubuntu"):
    return windows.local() / "Microsoft" / "Windows Terminal" / "Fragments" / f"claude-launcher-wsl-{distro}"


def profile_of(windows, distro="Ubuntu"):
    return json.loads((fragment_dir(windows, distro) / "claude-launcher.json").read_text())["profiles"][0]


# --- Finding Windows' programs ---------------------------------------------


def test_system32_from_path_first(windows, tmp_path):
    elsewhere = tmp_path / "w" / "WINDOWS" / "system32"
    elsewhere.mkdir(parents=True)
    (elsewhere / "cmd.exe").touch()
    (elsewhere / "cmd.exe").chmod(0o755)  # drvfs shows Windows' files as executable
    found = Interop({"PATH": f"/usr/bin:{elsewhere}"}, str(windows.mounts), windows.run).system32()
    assert found == elsewhere


def test_system32_from_the_mounts_without_windows_path(windows):
    # [interop] appendWindowsPath=false: no cmd.exe on PATH.
    assert Interop({"PATH": "/usr/bin:/bin"}, str(windows.mounts), windows.run).system32() == windows.system32


def test_system32_under_another_automount_root(tmp_path):
    # [automount] root=/windows/, and a space to be unescaped.
    other = FakeWindows(tmp_path, mount_at="win drives/c")
    assert Interop({"PATH": ""}, str(other.mounts), other.run).system32() == other.system32


def test_no_system32(tmp_path):
    (tmp_path / "mounts").write_text("/dev/sdc / ext4 rw 0 0\n")
    interop = Interop({"PATH": ""}, str(tmp_path / "mounts"), lambda *a, **k: b"")
    assert interop.system32() is None
    with pytest.raises(RuntimeError, match="System32"):
        interop.run("cmd.exe", [])


def test_events_run_wevtutil_and_whoami_by_full_path(windows):
    interop = Interop({"PATH": ""}, str(windows.mounts), windows.run)
    events = WslEvents(FakeHost(), interop=interop)
    assert events.windows._sid() == "S-1-5-21-1-2-3-1001"
    assert events.windows.log.since(0) == []
    ran = {argv[0]: cwd for argv, cwd in windows.calls}
    assert ran == {
        str(windows.system32 / "whoami.exe"): windows.system32,
        str(windows.system32 / "wevtutil.exe"): windows.system32,
    }


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError(2, "No such file"), OSError(8, "Exec format error"), subprocess.CalledProcessError(1, "x")],
)
def test_events_without_interop_are_unknown(windows, failure):
    windows.fail = {"wevtutil.exe": failure, "whoami.exe": failure}
    interop = Interop({"PATH": ""}, str(windows.mounts), windows.run)
    events = WslEvents(FakeHost(), interop=interop)
    assert events.windows.log.since(0) is None
    assert events.windows._sid() is None


# --- The profile ------------------------------------------------------------


def test_writes_a_profile_for_this_distro_and_user(windows, home):
    setup = make(windows, home)
    lines = list(setup.install(None, None))
    profile = profile_of(windows)
    assert profile["name"] == "Claude Code (Ubuntu)"
    assert profile["guid"] != terminal.PROFILE_GUID  # beside native Windows' profile
    assert profile["commandline"] == (
        'wsl.exe -d Ubuntu -u me --cd ~ -- bash -lc "~/.local/bin/claude-launcher || exec bash"'
    )
    assert profile["startingDirectory"] == "\\\\wsl.localhost\\Ubuntu\\" + str(home).lstrip("/").replace("/", "\\")
    assert profile["icon"] == "C:\\Users\\José\\AppData\\Local\\Microsoft\\Windows Terminal\\Fragments\\" + (
        "claude-launcher-wsl-Ubuntu\\icon.png"
    )
    assert (home / ".local" / "bin" / "claude-launcher").is_symlink()
    assert lines[-1].startswith('Added the "Claude Code (Ubuntu)" profile')
    assert setup.done.startswith("Restart Windows Terminal")


def test_localappdata_through_autorun_and_code_pages(windows, home):
    # /d keeps AutoRun quiet, but anything before the path is skipped anyway.
    windows.cmd_out = ("Clink v1.6 banner\r\n\r\n" + LOCAL + "\r\n").encode("utf-16-le")
    assert make(windows, home)._local_app_data() == LOCAL


def test_localappdata_without_utf16(windows, home):
    # If cmd ever wrote its code page instead, ASCII still comes through.
    windows.cmd_out = b"C:\\Users\\me\\AppData\\Local\r\n"
    assert make(windows, home)._local_app_data() == "C:\\Users\\me\\AppData\\Local"


def test_profile_id_is_the_distros_own_and_stable(windows, home):
    a, b = make(windows, home), make(windows, home)
    other = make(windows, home, WSL_DISTRO_NAME="Debian")
    assert a.profile.guid == b.profile.guid != other.profile.guid


def test_uninstall(windows, home):
    setup = make(windows, home)
    list(setup.install(None, None))
    lines = list(make(windows, home).uninstall())
    assert not fragment_dir(windows).exists()
    assert not (home / ".local" / "bin" / "claude-launcher").exists()
    assert lines[-1].startswith('Removed the "Claude Code (Ubuntu)" Windows Terminal profile')


def test_the_hook_is_linuxs(windows, home):
    assert make(windows, home).hook_command() == home / ".local" / "bin" / "claude-session-hook"


# --- Without interop: a warning, and the rest installed ---------------------


@pytest.mark.parametrize(
    ("program", "failure"),
    [
        ("cmd.exe", FileNotFoundError(2, "No such file or directory")),
        ("cmd.exe", OSError(8, "Exec format error")),  # interop off
        ("cmd.exe", subprocess.CalledProcessError(1, "cmd.exe", stderr=b"oops")),
        ("cmd.exe", subprocess.TimeoutExpired("cmd.exe", 20)),
        ("wslpath", subprocess.CalledProcessError(1, "wslpath")),
    ],
)
def test_without_windows_the_profile_is_skipped(windows, home, program, failure):
    windows.fail = {program: failure}
    setup = make(windows, home)
    lines = list(setup.install(None, None))
    assert lines[-1].startswith("warning: ") and lines[-1].endswith("; the Windows Terminal profile was skipped")
    assert (home / ".local" / "bin" / "claude-launcher").is_symlink()
    assert setup.done == "Run claude-launcher in any terminal."
    assert not fragment_dir(windows).exists()


def test_localappdata_left_unexpanded(windows, home):
    windows.cmd_out = "%LOCALAPPDATA%\r\n".encode("utf-16-le")
    lines = list(make(windows, home).install(None, None))
    assert lines[-1] == "warning: Windows didn't say where %LOCALAPPDATA% is; the Windows Terminal profile was skipped"


def test_no_drive_found(tmp_path, home):
    (tmp_path / "mounts").write_text("")
    windows = FakeWindows(tmp_path / "x")
    windows.mounts = tmp_path / "mounts"
    lines = list(make(windows, home).install(None, None))
    assert "System32" in lines[-1] and lines[-1].endswith("was skipped")


def test_uninstall_without_windows_warns_and_goes_on(windows, home):
    list(make(windows, home).install(None, None))
    windows.fail = {"cmd.exe": OSError(8, "Exec format error")}
    lines = list(make(windows, home).uninstall())
    assert not (home / ".local" / "bin" / "claude-launcher").exists()
    assert lines[-1].endswith("; the Windows Terminal profile is still there")


# --- Which distro ------------------------------------------------------------


@pytest.mark.parametrize("root", ["\\\\wsl.localhost\\Ubuntu-24.04\\", "\\\\wsl$\\Ubuntu-24.04\\"])
def test_distro_without_wsl_distro_name(windows, home, root):
    # sudo resets the environment.
    windows.distro_root = root
    setup = make(windows, home, WSL_DISTRO_NAME=None)
    assert setup.distro == "Ubuntu-24.04"
    list(setup.install(None, None))
    assert profile_of(windows, "Ubuntu-24.04")["commandline"].startswith("wsl.exe -d Ubuntu-24.04 ")


def test_no_distro_stops_before_anything(windows, home):
    windows.fail = {"wslpath": FileNotFoundError(2, "No such file")}
    setup = make(windows, home, WSL_DISTRO_NAME=None)
    with pytest.raises(RuntimeError, match="without sudo"):
        list(setup.install(None, None))
    assert not (home / ".local" / "bin").exists()


def test_uninstall_without_a_distro_still_unlinks(windows, home):
    list(make(windows, home).install(None, None))
    windows.fail = {"wslpath": FileNotFoundError(2, "No such file")}
    lines = list(make(windows, home, WSL_DISTRO_NAME=None).uninstall())
    assert not (home / ".local" / "bin" / "claude-launcher").exists()
    assert "without sudo" in lines[-1] and fragment_dir(windows).exists()


# --- --icon ------------------------------------------------------------------


def test_icon_is_kept_and_remembered(windows, home, tmp_path):
    mine = tmp_path / "mine.png"
    mine.write_bytes(DEFAULT_ICON.read_bytes()[:100] + b"mine")
    list(make(windows, home).install(None, str(mine)))
    shown = fragment_dir(windows) / "icon.png"
    assert shown.read_bytes() == mine.read_bytes()
    mine.unlink()  # moved away: the kept copy still shows
    list(make(windows, home).install(None, None))  # and a re-run without --icon keeps it
    assert shown.read_bytes().endswith(b"mine")
    list(make(windows, home).install(None, "default"))
    assert shown.read_bytes() == DEFAULT_ICON.read_bytes()


@pytest.mark.parametrize("name", ["mine.svg", "mine.ico", "missing.png"])
def test_icon_must_be_a_png_file(windows, home, tmp_path, name):
    icon = tmp_path / name
    if not name.startswith("missing"):
        icon.write_text("x")
    with pytest.raises(ValueError):
        list(make(windows, home).install(None, str(icon)))
    assert not (home / ".local" / "bin").exists()  # before anything


# --- Through the installer ---------------------------------------------------


class FakePlatform:
    def __init__(self, setup, data):
        self._setup, self.data = setup, data
        self.fzf_os = "linux"

    def setup(self):
        return self._setup

    def data_dir(self, env):
        return self.data


@pytest.fixture
def fzf_found(monkeypatch):
    monkeypatch.setattr(install.fzf, "find", lambda own: ("/usr/bin/fzf", (0, 70, 0)))
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, **kw: "/usr/bin/claude" if name == "claude" else which(name, **kw)
    )


def test_installer_without_interop_succeeds_with_a_warning(windows, home, claude_home, fzf_found, tmp_path):
    windows.fail = {"cmd.exe": OSError(8, "Exec format error")}
    lines = list(install.install(FakePlatform(make(windows, home), tmp_path / "data")))
    assert lines[-2].endswith("the Windows Terminal profile was skipped")
    assert lines[-1] == "Done. Run claude-launcher in any terminal."
    assert not [line for line in lines if line.startswith("error: ")]


def test_installer_without_a_distro_stops_first(windows, home, claude_home, fzf_found, tmp_path):
    windows.fail = {"wslpath": FileNotFoundError(2, "No such file")}
    lines = list(install.install(FakePlatform(make(windows, home, WSL_DISTRO_NAME=None), tmp_path / "data")))
    assert lines == [
        "error: can't tell which WSL distro this is; run install.sh in a WSL terminal, without sudo",
    ]
    assert not (claude_home.paths.config / "settings.json").exists()


def test_installer_round_trip(windows, home, claude_home, fzf_found, tmp_path):
    platform = FakePlatform(make(windows, home), tmp_path / "data")
    lines = list(install.install(platform))
    assert lines[-1].startswith('Done. Restart Windows Terminal and open a "Claude Code (Ubuntu)" tab')
    settings = json.loads((claude_home.paths.config / "settings.json").read_text())
    assert str(home / ".local" / "bin" / "claude-session-hook") in json.dumps(settings)
    list(install.uninstall(FakePlatform(make(windows, home), tmp_path / "data")))
    assert "hooks" not in json.loads((claude_home.paths.config / "settings.json").read_text())
    assert not fragment_dir(windows).exists()
