"""What every platform's Host has to do, checked on the platform these tests
run on. A new platform passes these before anything else."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import pytest

from claude_launcher.platforms import current
from conftest import posix_only

HOST = current().host


def test_boot_id_is_stable():
    assert HOST.boot_id()
    assert HOST.boot_id() == HOST.boot_id()


def test_boot_start_is_in_the_past():
    now = time.time()
    assert 0 < HOST.boot_start(now) < now


def test_these_tests_arent_claude():
    assert not HOST.claude_running(os.getpid())
    assert not HOST.claude_running("not a pid")
    assert not HOST.claude_running(None)
    assert not HOST.claude_running(os.getpid(), started=time.time())


def test_user():
    assert HOST.user()


LOCKER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from claude_launcher.platforms import current
with current().host.lock(Path(sys.argv[2])):
    print("locked", flush=True)
    time.sleep(0.5)
    Path(sys.argv[3]).write_text("done")
"""


def test_lock_keeps_another_process_out(tmp_path):
    src = os.path.join(os.path.dirname(__file__), "..", "..", "src")
    done = tmp_path / "done"
    child = subprocess.Popen(
        [sys.executable, "-c", LOCKER, src, str(tmp_path / "lock"), str(done)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with HOST.lock(tmp_path / "lock"):
            assert done.exists()  # only got the lock once the other process let go
    finally:
        child.wait(timeout=10)


@posix_only  # a pseudo-terminal stands in for the terminal; Windows' is checked live
@pytest.mark.parametrize("answers", [True, False])
def test_terminal_reply(answers):
    from fake_terminal import FakeTerminal

    t = FakeTerminal(answers=answers, da1=answers)
    try:
        reply = HOST.terminal_reply(t.slave, b"\x1b]11;?\x1b\\\x1b[c", re.compile(rb"\x1b\[\?[\d;]*c"), 0.3)
        if answers:
            assert reply and b"]11;" in reply
        else:
            assert reply is None
    finally:
        t.close()
