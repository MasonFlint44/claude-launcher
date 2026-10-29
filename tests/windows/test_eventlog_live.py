"""The real System log, on Windows: readable, and this boot's start is in it."""

import time

from claude_launcher.platforms.windows import eventlog as el
from claude_launcher.platforms.windows.host import WindowsHost


def test_this_boot_is_in_the_log():
    started = WindowsHost().boot_start(time.time())
    found = el.EventLog().since(started - 600)
    assert found is not None
    boots = [e.at for e in found if (e.provider, e.id) == (el.KERNEL, el.BOOTED)]
    assert any(abs(at - started) < 120 for at in boots)


def test_my_sid():
    assert (el.user_sid() or "").startswith("S-1-5-")
