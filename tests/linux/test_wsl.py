"""WSL: Linux with Windows' restarts and logoffs, and WSL stopping on its
own counted as a restart. WSL is Linux, so these are with Linux's tests;
they use fakes for Windows' side."""

import json

import pytest

from claude_launcher import platforms
from claude_launcher.platforms import Event
from claude_launcher.platforms.wsl import is_wsl
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.restart import Closed, Record, RestartState
from conftest import FakeHost, conversation

NOW = 1_800_000_000.0
WSL_STARTED = NOW - 300  # this WSL boot
THIS_BOOT, LAST_BOOT = "1" * 32, "2" * 32


@pytest.mark.parametrize(
    ("release", "wsl"),
    [("6.6.87.2-microsoft-standard-WSL2", True), ("4.4.0-19041-Microsoft", True), ("6.8.0-31-generic", False)],
)
def test_is_wsl(release, wsl):
    assert is_wsl(release, env={"WSL_DISTRO_NAME": "Ubuntu"}) is wsl


@pytest.mark.parametrize(
    "marker", ["run/WSL", "proc/sys/fs/binfmt_misc/WSLInterop", "proc/sys/fs/binfmt_misc/WSLInterop-late"]
)
def test_wsl_without_its_distro_name(tmp_path, marker):
    # sudo resets the environment; WSL's own files are still there.
    (tmp_path / marker).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / marker).touch()
    assert is_wsl("6.6.87.2-microsoft-standard-WSL2", root=str(tmp_path), env={})


def test_a_container_on_wsls_kernel_isnt_wsl(tmp_path):
    # Docker Desktop's containers, and devcontainers, run WSL's kernel.
    assert not is_wsl("6.6.87.2-microsoft-standard-WSL2", root=str(tmp_path), env={})


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
        Event(WSL_STARTED, WSL_STARTED, "wsl-stop"),
    ]


def test_without_interop_wsl_stopping_still_counts():
    assert source(None).events(None, same_boot=False) == [Event(WSL_STARTED, WSL_STARTED, "wsl-stop")]
    assert source(None).events(None, same_boot=True) is None


@pytest.fixture
def sort(claude_home):
    def run(*records, windows=()):
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
        s = source(windows)
        state = RestartState(claude_home.paths, s, s.host, now=lambda: NOW)
        return ids, state.sort()

    return run


def test_wsl_shutdown_brings_back_what_was_running(sort):
    # wsl --shutdown kills Claude, so the record never got an end.
    (killed,), group = sort(None)
    assert killed in group.sessions and group.sessions[killed] == Closed(WSL_STARTED, "wsl-stop")


def test_a_windows_restart_is_said_over_wsl_stopping(sort):
    # It stopped WSL too; it came first.
    (killed,), group = sort(None, windows=[WINDOWS_RESTART])
    assert group.sessions[killed] == Closed(WINDOWS_RESTART.start, "restart")


def test_a_logoff_is_said_over_wsl_stopping(claude_home):
    # A Claude that ended at a logoff, with WSL starting again moments later.
    logoff = Event(NOW - 3000, NOW - 3000, "logout")
    host = FakeHost(boot=THIS_BOOT, started=lambda now: NOW - 2999)
    state = RestartState(claude_home.paths, WslEvents(host, FakeWindows([logoff]), now=lambda: NOW), host)
    record = Record("s", 1, LAST_BOOT, "", NOW - 3600, ended=NOW - 3001)
    assert state._closed_by(record, False, NOW) == Closed(logoff.start, "logout")


def test_a_tab_closed_before_isnt_brought_back(sort):
    (closed,), group = sort(NOW - 3000)
    assert closed not in group.sessions
