"""Windows' host, the parts that aren't Win32 calls, so these run on any OS;
tests/windows and tests/contract check the calls themselves."""

import re

import pytest

from claude_launcher.paths import REPO_DIR
from claude_launcher.platforms.windows import host


def _boot_key(path):
    [key] = re.findall(r'^_BOOT_KEY = (r"[^"]*")$', path.read_text(encoding="utf-8"), re.MULTILINE)
    return key


def test_the_hook_reads_the_same_boot_counter():
    # The hook can't import the package, so it has its own copy.
    hook = REPO_DIR / "bin" / "claude-session-hook.py"
    assert _boot_key(hook) == _boot_key(REPO_DIR / "src" / "claude_launcher" / "platforms" / "windows" / "host.py")


@pytest.mark.parametrize(
    "created, started, same",
    [
        (100.0, 101.0, True),  # Claude starts, then its hook runs
        (100.0, 100.0 - host._REUSED_AFTER + 1, True),  # clocks a little apart
        (200.0, 100.0, False),  # a new process with the old pid
        (None, 100.0, True),  # can't tell: the command line decides
        (100.0, None, True),
    ],
)
def test_is_same_process(created, started, same):
    assert host.is_same_process(created, started) is same


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(host, "command_line", lambda pid: ["C:\\x\\claude.exe"] if pid == 42 else ["cmd.exe"])
    monkeypatch.setattr(host, "process_started", lambda pid: 1000.0)
    return host.WindowsHost()


def test_a_reused_pid_isnt_claude(claude):
    assert claude.claude_running(42, started=1005.0)
    assert not claude.claude_running(42, started=500.0)  # this claude.exe began after that record's
    assert claude.claude_running(42)  # without a start, the command line alone
    assert not claude.claude_running(7, started=1005.0)
    assert not claude.claude_running(True) and not claude.claude_running("42")
