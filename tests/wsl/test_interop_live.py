"""WSL's interop, live: Windows' programs through the real code paths. Runs
only in WSL (the VM's, by hand: GitHub's runners can't run WSL 2), and only
reads: nothing is installed or written on either side."""

import os
import re
import time
from pathlib import Path

import pytest

from claude_launcher.platforms.linux.host import LinuxHost
from claude_launcher.platforms.windows.eventlog import EventLog, WindowsEvents
from claude_launcher.platforms.wsl import is_wsl
from claude_launcher.platforms.wsl.events import WslEvents
from claude_launcher.platforms.wsl.interop import Interop
from claude_launcher.platforms.wsl.setup import WslSetup
from claude_launcher.restart import Record

pytestmark = pytest.mark.skipif(not is_wsl(), reason="WSL only")


def test_system32_is_found_with_and_without_windows_path():
    on_path = Interop().system32()
    from_mounts = Interop(env={}).system32()  # appendWindowsPath=false
    assert on_path is not None and (on_path / "cmd.exe").is_file()
    assert from_mounts is not None and (from_mounts / "cmd.exe").is_file()


def test_local_app_data_comes_through_cmd():
    local = WslSetup()._local_app_data()
    assert re.fullmatch(r"[A-Za-z]:\\.+\\AppData\\Local", local), local
    assert Path(WslSetup()._wslpath("-u", local)).is_dir()


def test_wslpath_and_the_distros_name():
    setup = WslSetup()
    root = setup._wslpath("-w", "/")
    assert root.startswith("\\\\wsl"), root
    # Without WSL_DISTRO_NAME (sudo clears it), the name comes from wslpath.
    from_wslpath = WslSetup(env={k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}).distro
    assert from_wslpath
    if os.environ.get("WSL_DISTRO_NAME"):
        assert from_wslpath == os.environ["WSL_DISTRO_NAME"] == setup.distro


def test_my_windows_sid():
    assert (Interop().user_sid() or "").startswith("S-1-5-")


def test_the_event_log_through_interop():
    interop = Interop()
    log = EventLog(run=interop.wevtutil)
    assert log.since(time.time() - 8 * 86400) is not None
    host = LinuxHost()
    record = Record("s", 1, host.boot_id(), "", time.time() - 3600, None)
    assert WindowsEvents(log, sid=interop.user_sid).events(record, same_boot=True) is not None


def test_an_earlier_boot_adds_wsl_stopping_at_this_distros_start():
    host = LinuxHost()
    now = time.time()
    record = Record("s", 1, "0" * 32, "", now - 3600, None)
    found = WslEvents(host, now=lambda: now).events(record, same_boot=False)
    stops = [e for e in found if e.why == "wsl-stop"]
    assert len(stops) == 1 and stops[0].start == pytest.approx(host.boot_start(now), abs=1)  # uptime moves on
