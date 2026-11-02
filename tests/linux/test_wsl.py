"""WSL: Linux with Windows' restarts and logoffs, and WSL stopping on its
own counted as a restart. WSL is Linux, so these are with Linux's tests;
they use fakes for Windows' side."""

import pytest

from claude_launcher import platforms
from claude_launcher.platforms import Event
from claude_launcher.platforms.wsl import is_wsl
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.restart import Closed, Record, RestartState
from conftest import LAST_BOOT, NOW, THIS_BOOT, FakeHost, conversation, write_record

WSL_STARTED = NOW - 300  # this WSL boot


WSL_KERNEL = "6.6.87.2-microsoft-standard-WSL2"
MARKERS = ["run/WSL", "proc/sys/fs/binfmt_misc/WSLInterop", "proc/sys/fs/binfmt_misc/WSLInterop-late"]


def mark(root, marker):
    (root / marker).parent.mkdir(parents=True, exist_ok=True)
    (root / marker).touch()


@pytest.mark.parametrize(
    ("release", "wsl"),
    [(WSL_KERNEL, True), ("4.4.0-19041-Microsoft", True), ("6.8.0-31-generic", False)],
)
def test_is_wsl(tmp_path, release, wsl):
    assert is_wsl(release, root=str(tmp_path), env={"WSL_DISTRO_NAME": "Ubuntu"}) is wsl


@pytest.mark.parametrize("marker", MARKERS)
def test_wsl_without_its_distro_name(tmp_path, marker):
    # sudo resets the environment; WSL's own files are still there.
    mark(tmp_path, marker)
    assert is_wsl(WSL_KERNEL, root=str(tmp_path), env={})


@pytest.mark.parametrize("marker", MARKERS)
def test_wsl_on_a_kernel_of_its_own(tmp_path, marker):
    # A custom kernel from .wslconfig needn't say "microsoft".
    mark(tmp_path, marker)
    assert is_wsl("6.12.1-custom", root=str(tmp_path), env={})


def test_a_container_on_wsls_kernel_isnt_wsl(tmp_path):
    # Docker Desktop's containers, and devcontainers, run WSL's kernel.
    assert not is_wsl(WSL_KERNEL, root=str(tmp_path), env={})


def test_wsl_distro_name_alone_needs_wsls_kernel(tmp_path):
    # It can be passed into a container, or over SSH to another machine.
    assert not is_wsl("6.8.0-31-generic", root=str(tmp_path), env={"WSL_DISTRO_NAME": "Ubuntu"})


def test_wsl_is_picked_inside_wsl(monkeypatch):
    monkeypatch.setattr(platforms, "_current", None)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("claude_launcher.platforms.wsl.is_wsl", lambda release=None: True)
    assert platforms.current().name == "wsl"


class FakeWindowsEvents:
    def __init__(self, found, lost=False):
        self.found, self.lost, self.asked = found, lost, []

    def events(self, record, same_boot):
        self.asked.append(same_boot)
        return None if self.found is None else list(self.found)

    def boot_lost(self, record):
        return self.lost


def source(found):
    host = FakeHost(boot=THIS_BOOT, started=lambda now: WSL_STARTED)
    return WslEvents(host, FakeWindowsEvents(found), now=lambda: NOW)


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


def test_whether_windows_log_is_lost_is_windows_to_say():
    host = FakeHost(boot=THIS_BOOT, started=lambda now: WSL_STARTED)
    assert WslEvents(host, FakeWindowsEvents([], lost=True), now=lambda: NOW).boot_lost(None) is True
    assert WslEvents(host, FakeWindowsEvents([], lost=False), now=lambda: NOW).boot_lost(None) is False
    # Without interop, as for its events: not a reason to wait.
    assert WslEvents(host, FakeWindowsEvents(None, lost=None), now=lambda: NOW).boot_lost(None) is False


def test_without_interop_wsl_stopping_still_counts():
    assert source(None).events(None, same_boot=False) == [Event(WSL_STARTED, WSL_STARTED, "wsl-stop")]
    assert source(None).events(None, same_boot=True) is None


@pytest.fixture
def sort(claude_home):
    def run(*records, windows=()):
        ids = []
        for i, ended in enumerate(records):
            sid = claude_home.log("/tmp", conversation(f"s{i}", "/tmp"))
            write_record(claude_home.paths, sid, LAST_BOOT, 100 + i, NOW - 3600, ended)
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
    state = RestartState(claude_home.paths, WslEvents(host, FakeWindowsEvents([logoff]), now=lambda: NOW), host)
    record = Record("s", 1, LAST_BOOT, "", NOW - 3600, ended=NOW - 3001)
    assert state._closed_by(record, False, NOW) == Closed(logoff.start, "logout")


def test_a_tab_closed_before_isnt_brought_back(sort):
    (closed,), group = sort(NOW - 3000)
    assert closed not in group.sessions


def test_one_wsl_stop_for_every_record_though_the_uptime_moves():
    # boot_start is worked out from the uptime, which moves on between
    # reads: the sessions one stop closed should share one time.
    clock = iter([NOW, NOW + 0.7, NOW + 1.4])
    host = FakeHost(boot=THIS_BOOT, started=lambda now: now - 300)
    s = WslEvents(host, FakeWindowsEvents([]), now=lambda: next(clock))
    first = s.events(None, same_boot=False)
    second = s.events(None, same_boot=False)
    assert first == second == [Event(NOW - 300, NOW - 300, "wsl-stop")]
