"""What every platform's EventSource has to do, on the platform these tests
run on: answer for a record without failing, with well-formed events, and
say whether a boot is lost."""

from __future__ import annotations

import time

from claude_launcher.platforms import Event, current
from claude_launcher.restart import Record, normalize_boot_id, reasons

PLATFORM = current()
KEYS = {r.key for r in reasons(PLATFORM.reasons)}  # the shared reasons and the platform's own


def record(boot_id: str) -> Record:
    now = time.time()
    return Record("0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", 1, normalize_boot_id(boot_id), "", now - 60, now - 30)


def check(events) -> None:
    assert events is None or isinstance(events, list)
    for e in events or ():
        assert isinstance(e, Event)
        assert e.why in KEYS
        assert e.start <= e.end


def test_this_boot():
    check(PLATFORM.events().events(record(PLATFORM.host.boot_id()), same_boot=True))


def test_a_boot_the_system_never_saw():
    check(PLATFORM.events().events(record("f" * 32), same_boot=False))


def test_whether_a_boot_is_lost():
    assert PLATFORM.events().boot_lost(record("f" * 32)) in (True, False, None)


def test_a_record_of_this_boot_that_just_ended_isnt_lost():
    # restart.py asks this when nothing closed such a record; a working
    # system still has the last half minute.
    assert PLATFORM.events().boot_lost(record(PLATFORM.host.boot_id())) in (False, None)
