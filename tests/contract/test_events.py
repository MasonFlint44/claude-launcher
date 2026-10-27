"""What every platform's EventSource has to do, on the platform these tests
run on: answer for a record without failing, with well-formed events."""

from __future__ import annotations

import time

from claude_launcher.platforms import Event, current
from claude_launcher.restart import REASONS, Record, normalize_boot_id

PLATFORM = current()


def record(boot_id: str) -> Record:
    now = time.time()
    return Record("0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", 1, normalize_boot_id(boot_id), "", now - 60, now - 30)


def check(events) -> None:
    assert events is None or isinstance(events, list)
    for e in events or ():
        assert isinstance(e, Event)
        assert e.why in REASONS
        assert e.start <= e.end


def test_this_boot():
    check(PLATFORM.events().events(record(PLATFORM.host.boot_id()), same_boot=True))


def test_a_boot_the_system_never_saw():
    check(PLATFORM.events().events(record("f" * 32), same_boot=False))
