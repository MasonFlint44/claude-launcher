"""WSL's installer and its reach into Windows (interop), with Windows faked:
a directory standing in for the C: drive, a fake /proc/mounts, and a runner
that answers for cmd.exe, wslpath, wevtutil.exe and whoami.exe. They run on
any Linux, so CI covers them."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_launcher import install
from claude_launcher.platforms import Event
from claude_launcher.platforms.windows import terminal
from claude_launcher.platforms.windows.setup import DEFAULT_ICON
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.platforms.wsl.interop import Interop
from claude_launcher.platforms.wsl.setup import WslSetup
from claude_launcher.restart import Record
from conftest import FakeHost, FakePlatform

LOCAL = "C:\\Users\\José\\AppData\\Local"
# User names cmd trips on, as in the native Windows tests (test_windows_setup.py).
HOSTILE = ["A&B", "a^b", "100%", "José", "x!y!", "Jo (work)", "Jo Smith (A&B) ^1 100% !x! [é]"]


def cmd_c(line: str, env: dict[str, str]) -> tuple[int, str]:
    """Enough of `cmd /c LINE` for these tests: %VAR% expanded first, then
    the line split at & and ^ taken out (outside quotes), then echo and set."""
    line = re.sub(r"%([^%]+)%", lambda m: env.get(m.group(1).upper(), m.group()), line)
    commands, current, quoted, i = [], "", False, 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            quoted = not quoted
        elif ch == "^" and not quoted and i + 1 < len(line):
            i += 1
            ch = line[i]
        elif ch == "&" and not quoted:
            commands.append(current.strip())
            current, i = "", i + 1
            continue
        current += ch
        i += 1
    commands.append(current.strip())
    out, code = "", 0
    for command in commands:
        word, _, rest = command.partition(" ")
        if word.lower() == "echo":
            out, code = out + rest + "\r\n", 0
        elif word.lower() == "set":
            found = sorted(f"{k}={v}" for k, v in env.items() if k.upper().startswith(rest.upper()))
            out, code = out + "".join(f + "\r\n" for f in found), 0 if found else 1
        else:
            code = 9009  # not a command
    return code, out


class FakeWindows:
    """Windows' side: C: mounted at `drive`, and what its programs say."""

    def __init__(self, tmp_path: Path, mount_at: str = "c", user: str = "José"):
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
        self.user = user
        self.local_app_data = f"C:\\Users\\{user}\\AppData\\Local"
        # What cmd.exe's environment holds (a name that starts the same, too),
        # or, if set, exactly what it prints.
        self.windows_env = {"LOCALAPPDATA": self.local_app_data, "LOCALAPPDATA_OLD": "D:\\old"}
        self.cmd_out: bytes | None = None
        self.fail: dict[str, BaseException] = {}
        self.calls: list[tuple[list[str], Path | None]] = []
        self.distro_root = "\\\\wsl.localhost\\Ubuntu\\"

    def local(self) -> Path:
        return self.drive / "Users" / self.user / "AppData" / "Local"

    def run(self, argv, cwd=None) -> bytes:
        argv = list(argv)
        self.calls.append((argv, cwd))
        program = Path(argv[0]).name
        if program in self.fail:
            raise self.fail[program]
        if program == "cmd.exe":
            assert argv[0] == str(self.system32 / "cmd.exe") and cwd == self.system32
            assert argv[1:4] == ["/d", "/u", "/c"]
            if self.cmd_out is not None:
                return self.cmd_out
            code, out = cmd_c(" ".join(argv[4:]), self.windows_env)
            if code:
                raise subprocess.CalledProcessError(code, argv, stderr=b"")
            return out.encode("utf-16-le")
        if argv[:2] == ["wslpath", "-u"] and argv[2].startswith("C:\\"):
            return (str(self.drive) + argv[2][2:].replace("\\", "/") + "\n").encode()
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


@pytest.mark.parametrize(
    ("failure", "interop_there"),
    [
        (FileNotFoundError(2, "No such file"), False),
        (OSError(8, "Exec format error"), False),  # interop turned off
        (subprocess.CalledProcessError(1, "x"), True),
        (subprocess.TimeoutExpired("x", 20), True),  # slow after a restart
    ],
)
def test_a_failed_query_is_told_from_no_interop(windows, failure, interop_there):
    windows.fail = {"wevtutil.exe": failure}
    interop = Interop({"PATH": ""}, str(windows.mounts), windows.run)
    record = Record("s", 1, "2" * 32, "", 100.0, ended=200.0)
    events = WslEvents(FakeHost(started=lambda now: 1000.0), interop=interop, now=lambda: 2000.0)
    if interop_there:  # it waits, as on native Windows
        assert events.events(record, same_boot=False) is None
    else:
        assert events.events(record, same_boot=False) == [Event(1000.0, 1000.0, "wsl-stop")]
    assert interop.reachable() is interop_there


def test_no_system32_is_no_interop(tmp_path):
    (tmp_path / "mounts").write_text("/dev/sdc / ext4 rw 0 0\n")
    interop = Interop({"PATH": ""}, str(tmp_path / "mounts"), lambda *a, **k: b"")
    assert interop.reachable() is False


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
    windows.cmd_out = ("Clink v1.6 banner\r\n\r\nLOCALAPPDATA=" + LOCAL + "\r\n").encode("utf-16-le")
    assert make(windows, home)._local_app_data() == LOCAL


def test_localappdata_after_a_byte_order_mark(windows, home):
    windows.cmd_out = ("\ufeffLOCALAPPDATA=" + LOCAL + "\r\n").encode("utf-16-le")
    assert make(windows, home)._local_app_data() == LOCAL
    windows.cmd_out = ("\ufeffLOCALAPPDATA=" + LOCAL + "\r\n").encode("utf-8")
    assert make(windows, home)._local_app_data() == LOCAL


def test_localappdata_without_utf16(windows, home):
    # If cmd ever wrote its code page instead, ASCII still comes through.
    windows.cmd_out = b"LOCALAPPDATA=C:\\Users\\me\\AppData\\Local\r\n"
    assert make(windows, home)._local_app_data() == "C:\\Users\\me\\AppData\\Local"


def test_localappdata_is_that_variable_whatever_its_case(windows, home):
    # set LOCALAPPDATA prints every variable whose name starts with it.
    windows.cmd_out = "LocalAppData_x=D:\\no\r\nLocalAppData=E:\\Local\r\n".encode("utf-16-le")
    assert make(windows, home)._local_app_data() == "E:\\Local"


@pytest.mark.parametrize("user", HOSTILE)
def test_localappdata_for_any_user_name(tmp_path, home, user):
    # echo %LOCALAPPDATA% broke at the parse after expanding: & split it
    # (9009), ^ was dropped (another user's directory), % was refused.
    windows = FakeWindows(tmp_path, user=user)
    setup = make(windows, home)
    assert setup._local_app_data() == f"C:\\Users\\{user}\\AppData\\Local"
    lines = list(setup.install(None, None))
    assert lines[-1].startswith('Added the "Claude Code (Ubuntu)" profile'), lines
    assert profile_of(windows)["name"] == "Claude Code (Ubuntu)"


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


def test_localappdata_not_set(windows, home):
    windows.windows_env = {"LOCALAPPDATAX": "D:\\x"}  # only a name that starts the same
    lines = list(make(windows, home).install(None, None))
    assert lines[-1] == "warning: Windows didn't say where %LOCALAPPDATA% is; the Windows Terminal profile was skipped"
    windows.windows_env = {}  # set exits 1
    lines = list(make(windows, home).install(None, None))
    assert lines[-1] == "warning: cmd.exe failed; the Windows Terminal profile was skipped"


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


def test_icon_can_be_the_copy_already_made(windows, home, tmp_path):
    # Run again with --icon naming the installed copy.
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"png")
    setup = make(windows, home)
    list(setup.install(None, str(mine)))
    list(setup.install(None, str(setup.data_dir / "icon.png")))
    assert (setup.data_dir / "icon.png").read_bytes() == b"png"


@pytest.mark.parametrize("name", ["mine.svg", "mine.ico", "missing.png"])
def test_icon_must_be_a_png_file(windows, home, tmp_path, name):
    icon = tmp_path / name
    if not name.startswith("missing"):
        icon.write_text("x")
    with pytest.raises(ValueError):
        make(windows, home).check(None, str(icon))
    with pytest.raises(ValueError):
        list(make(windows, home).install(None, str(icon)))
    assert not (home / ".local" / "bin").exists()  # before anything


# --- Through the installer ---------------------------------------------------


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


def test_installer_with_a_bad_icon_stops_first(windows, home, claude_home, fzf_found, tmp_path):
    icon = tmp_path / "mine.svg"
    icon.write_text("<svg/>")
    lines = list(install.install(FakePlatform(make(windows, home), tmp_path / "data"), None, str(icon)))
    assert lines == ["error: the icon for Windows Terminal must be a .png"]
    assert not (claude_home.paths.config / "settings.json").exists()
    assert not (home / ".local" / "bin").exists()


def test_installer_round_trip(windows, home, claude_home, fzf_found, tmp_path):
    platform = FakePlatform(make(windows, home), tmp_path / "data")
    lines = list(install.install(platform))
    assert lines[-1].startswith('Done. Restart Windows Terminal and open a "Claude Code (Ubuntu)" tab')
    settings = json.loads((claude_home.paths.config / "settings.json").read_text())
    assert str(home / ".local" / "bin" / "claude-session-hook") in json.dumps(settings)
    list(install.uninstall(FakePlatform(make(windows, home), tmp_path / "data")))
    assert "hooks" not in json.loads((claude_home.paths.config / "settings.json").read_text())
    assert not fragment_dir(windows).exists()


def test_the_profiles_user_is_the_hosts(tmp_path):
    from claude_launcher.platforms.linux.host import LinuxHost

    assert WslSetup({"HOME": str(tmp_path)}).user() == LinuxHost().user()


# --- Under sudo ----------------------------------------------------------------


@pytest.mark.parametrize("variable", ["SUDO_USER", "DOAS_USER"])
def test_sudo_stops_before_anything(windows, home, claude_home, fzf_found, tmp_path, monkeypatch, variable):
    # The profile would open as root, and the links would go in /root. doas
    # sets DOAS_USER instead.
    monkeypatch.setattr("os.geteuid", lambda: 0)
    lines = list(install.install(FakePlatform(make(windows, home, **{variable: "me"}), tmp_path / "data")))
    assert lines == [
        "error: run install.sh as yourself, without sudo or doas: the profile opens as the user who installs it",
    ]
    assert not (home / ".local" / "bin").exists()
    assert not (claude_home.paths.config / "settings.json").exists()
    assert not fragment_dir(windows).exists()


@pytest.mark.parametrize("by", [{}, {"SUDO_USER": "root"}, {"DOAS_USER": "root"}])
def test_root_itself_can_install(windows, home, monkeypatch, by):
    # A distro whose user is root (its default, or sudo or doas from root).
    monkeypatch.setattr("os.geteuid", lambda: 0)
    setup = WslSetup(
        {"WSL_DISTRO_NAME": "Ubuntu", "HOME": str(home), **by},
        windows.run,
        str(windows.mounts),
        user=lambda: "root",
    )
    setup.check(None, None)
    lines = list(setup.install(None, None))
    assert profile_of(windows)["commandline"].startswith("wsl.exe -d Ubuntu -u root ")
    assert lines[-1].startswith('Added the "Claude Code (Ubuntu)" profile')
