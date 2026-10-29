"""WSL: Linux with Windows' restarts and logoffs, and WSL stopping on its
own counted as a restart. WSL is Linux, so these are with Linux's tests;
they use fakes for Windows' side."""

import json
from pathlib import Path

import pytest

from claude_launcher import platforms
from claude_launcher.platforms import Event
from claude_launcher.platforms.windows import terminal
from claude_launcher.platforms.wsl import is_wsl
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.platforms.wsl.setup import WslSetup
from claude_launcher.restart import RestartState
from conftest import FakeHost, conversation

NOW = 1_800_000_000.0
WSL_STARTED = NOW - 300  # this WSL boot
THIS_BOOT, LAST_BOOT = "1" * 32, "2" * 32


@pytest.mark.parametrize(
    ("release", "wsl"),
    [("6.6.87.2-microsoft-standard-WSL2", True), ("4.4.0-19041-Microsoft", True), ("6.8.0-31-generic", False)],
)
def test_is_wsl(release, wsl):
    assert is_wsl(release) is wsl


def test_wsl_is_picked_inside_wsl(monkeypatch):
    monkeypatch.setattr(platforms, "_current", None)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("claude_launcher.platforms.wsl.is_wsl", lambda release=None: True)
    assert platforms.current().name == "wsl"


class FakeWindows:
    def __init__(self, found):
        self.found, self.asked = found, []

    def events(self, record, same_boot):
        self.asked.append(same_boot)
        return None if self.found is None else list(self.found)


def source(found):
    host = FakeHost(boot=THIS_BOOT, started=lambda now: WSL_STARTED)
    return WslEvents(host, FakeWindows(found), now=lambda: NOW)


WINDOWS_RESTART = Event(NOW - 900, NOW - 895, "restart")


def test_same_boot_is_windows_logoffs_only():
    logoff = Event(NOW - 100, NOW - 100, "logout")
    s = source([logoff])
    assert s.events(None, same_boot=True) == [logoff]
    assert s.windows.asked == [False]  # Windows' boots aren't WSL's


def test_another_boot_adds_wsl_stopping():
    assert source([WINDOWS_RESTART]).events(None, same_boot=False) == [
        WINDOWS_RESTART,
        Event(WSL_STARTED, WSL_STARTED, "restart"),
    ]


def test_without_interop_wsl_stopping_still_counts():
    assert source(None).events(None, same_boot=False) == [Event(WSL_STARTED, WSL_STARTED, "restart")]
    assert source(None).events(None, same_boot=True) is None


@pytest.fixture
def sort(claude_home):
    def run(*records):
        claude_home.paths.open_records.mkdir(parents=True, exist_ok=True)
        ids = []
        for i, ended in enumerate(records):
            sid = claude_home.log("/tmp", conversation(f"s{i}", "/tmp"))
            rec = {
                "session_id": sid,
                "pid": 100 + i,
                "boot_id": LAST_BOOT,
                "login_session": "",
                "started_at": NOW - 3600,
            }
            if ended is not None:
                rec.update(end_reason="other", ended_at=ended)
            (claude_home.paths.open_records / f"{LAST_BOOT}_{100 + i}").write_text(json.dumps(rec))
            ids.append(sid)
        s = source([])
        state = RestartState(claude_home.paths, s, s.host, now=lambda: NOW)
        return ids, state.sort()

    return run


def test_wsl_shutdown_brings_back_what_was_running(sort):
    # wsl --shutdown kills Claude, so the record never got an end.
    (killed,), group = sort(None)
    assert killed in group.sessions and group.sessions[killed].why == "restart"


def test_a_tab_closed_before_isnt_brought_back(sort):
    (closed,), group = sort(NOW - 3000)
    assert closed not in group.sessions


@pytest.fixture
def wsl_setup(tmp_path):
    windows = tmp_path / "c" / "Users" / "me" / "AppData" / "Local"
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "cmd.exe":
            return "C:\\Users\\me\\AppData\\Local"
        if argv[:2] == ["wslpath", "-u"]:
            return str(windows)
        if argv[:2] == ["wslpath", "-w"]:
            return "C:" + argv[2][len(str(tmp_path / "c")) :].replace("/", "\\")
        raise AssertionError(argv)

    home = tmp_path / "home" / "me"
    env = {"WSL_DISTRO_NAME": "Ubuntu", "HOME": str(home), "PATH": str(home / ".local" / "bin")}
    s = WslSetup(env, run)
    s.windows, s.home = windows, home
    return s


def test_setup_writes_a_profile_for_this_distro(wsl_setup):
    lines = list(wsl_setup.install(None, None))
    fragment = wsl_setup.windows / "Microsoft" / "Windows Terminal" / "Fragments" / "claude-launcher-wsl-Ubuntu"
    profile = json.loads((fragment / "claude-launcher.json").read_text())["profiles"][0]
    assert profile["name"] == "Claude Code (Ubuntu)"
    assert profile["guid"] != terminal.PROFILE_GUID  # beside native Windows' profile
    assert profile["commandline"].startswith("wsl.exe -d Ubuntu --cd ~ -- bash -lc ")
    assert profile["icon"].startswith("C:\\Users\\me\\AppData\\Local\\Microsoft\\Windows Terminal\\Fragments\\")
    assert profile["startingDirectory"] == "\\\\wsl.localhost\\Ubuntu\\" + str(wsl_setup.home).lstrip("/").replace(
        "/", "\\"
    )
    assert (wsl_setup.home / ".local" / "bin" / "claude-launcher").is_symlink()
    assert lines[-1].startswith('Added the "Claude Code (Ubuntu)" profile')


def test_profile_id_is_the_distros_own_and_stable(wsl_setup):
    other = WslSetup({**wsl_setup.env, "WSL_DISTRO_NAME": "Debian"}, wsl_setup.run)
    assert wsl_setup.profile.guid == WslSetup(wsl_setup.env, wsl_setup.run).profile.guid
    assert wsl_setup.profile.guid != other.profile.guid


def test_setup_uninstall(wsl_setup):
    list(wsl_setup.install(None, None))
    list(wsl_setup.uninstall())
    assert not (
        wsl_setup.windows / "Microsoft" / "Windows Terminal" / "Fragments" / "claude-launcher-wsl-Ubuntu"
    ).exists()
    assert not (wsl_setup.home / ".local" / "bin" / "claude-launcher").exists()


def test_the_hook_is_linuxs(wsl_setup):
    assert wsl_setup.hook_command() == Path(wsl_setup.home) / ".local" / "bin" / "claude-session-hook"


def test_needs_to_be_in_wsl(wsl_setup):
    with pytest.raises(RuntimeError, match="WSL_DISTRO_NAME"):
        list(WslSetup({}, wsl_setup.run).install(None, None))
