"""Restarts and logoffs from Windows' System log, read from wevtutil's XML.
Pure parsing, so these run on any OS; tests/windows checks the real log."""

from dataclasses import dataclass

import pytest

from claude_launcher.platforms.base import Event
from claude_launcher.platforms.windows import eventlog as el

ME = "S-1-5-21-1-2-3-1000"
OTHER = "S-1-5-21-1-2-3-1001"
T = 1_800_000_000.0  # when the Claude started


def xml(provider, event_id, at, **data):
    fields = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in data.items())
    return (
        "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
        f"<Provider Name='{provider}'/><EventID Qualifiers='32768'>{event_id}</EventID>"
        f"<TimeCreated SystemTime='{el.format_time(at)[:-1]}0000Z'/></System>"
        f"<EventData>{fields}</EventData></Event>"
    )


BOOTED = lambda at: xml(el.KERNEL, 12, at)  # noqa: E731
SHUTTING_DOWN = lambda at: xml(el.KERNEL, 13, at)  # noqa: E731
RESTART = lambda at, kind="restart": xml("User32", 1074, at, param5=kind)  # noqa: E731
HIBERNATING = lambda at: xml("Microsoft-Windows-Kernel-Power", 42, at, TargetState=6, EffectiveState=5)  # noqa: E731
LOGOFF = lambda at, sid=ME: xml("Microsoft-Windows-Winlogon", 7002, at, TSId=1, UserSid=sid)  # noqa: E731


@dataclass
class Rec:
    started: float = T
    ended: float | None = None


class FakeLog(el.EventLog):
    """wevtutil answering from a list of events, newest first when asked."""

    def __init__(self, events, last_before=None):
        self.events, self.last = events, last_before
        super().__init__(self._answer)

    def _answer(self, args):
        if any(a.startswith("/q:*[System[TimeCreated[@SystemTime<") for a in args):
            return self.last or ""
        return "".join(self.events)


def events(log_events, same_boot=False, last_before=None, sid=ME):
    return el.WindowsEvents(FakeLog(log_events, last_before), sid=lambda: sid).events(Rec(), same_boot)


def test_parse_time_to_the_100_nanoseconds():
    assert el.parse_time("2026-10-29T05:31:23.5887514Z") == pytest.approx(1793251883.5887514)
    assert el.parse_time("2026-10-29T05:31:23Z") == 1793251883.0
    with pytest.raises(ValueError):
        el.parse_time("yesterday")


def test_format_time_round_trips():
    assert el.parse_time(el.format_time(T + 0.25)) == T + 0.25


def test_parse_events_reads_provider_id_time_and_data():
    [e] = el.parse_events(LOGOFF(T))
    assert (e.provider, e.id, e.at, e.data["UserSid"]) == ("Microsoft-Windows-Winlogon", 7002, T, ME)


def test_restart_from_its_start_to_shutting_down():
    found = events([RESTART(T + 100), LOGOFF(T + 103), SHUTTING_DOWN(T + 110), BOOTED(T + 130)])
    assert Event(T + 100, T + 110, "restart") in found
    assert Event(T + 103, T + 103, "logout") in found  # logged at a restart too; the restart wins


def test_power_off_counts_as_a_restart():
    found = events([RESTART(T + 100, "power off"), SHUTTING_DOWN(T + 104), BOOTED(T + 9000)])
    assert found == [Event(T + 100, T + 104, "restart")]


def test_only_the_records_own_boot():
    # A later boot's restart closed nothing this record had open.
    found = events([RESTART(T + 100), SHUTTING_DOWN(T + 110), BOOTED(T + 130), RESTART(T + 900), BOOTED(T + 950)])
    assert [e.start for e in found] == [T + 100]


def test_the_last_restart_of_the_boot():
    # A restart that was begun, then called off, then begun again.
    found = events([RESTART(T + 50), RESTART(T + 100), SHUTTING_DOWN(T + 110), BOOTED(T + 130)])
    assert found == [Event(T + 100, T + 110, "restart")]


def test_a_crash_ends_at_the_boots_last_event():
    found = events([BOOTED(T + 500)], last_before=xml("Service Control Manager", 7036, T + 400))
    assert found == [Event(T + 400, T + 400, "restart")]


def test_same_boot_has_no_restart_yet():
    assert events([LOGOFF(T + 100)], same_boot=True) == [Event(T + 100, T + 100, "logout")]


def test_only_my_logoffs():
    assert events([LOGOFF(T + 100, OTHER)], same_boot=True) == []
    assert events([LOGOFF(T + 100)], same_boot=True, sid=None) == []  # can't tell whose


def test_a_log_that_cant_be_read_says_so():
    log = el.EventLog(lambda args: None)
    assert el.WindowsEvents(log, sid=lambda: ME).events(Rec(), False) is None


def test_each_query_runs_once():
    runs = []
    log = el.EventLog(lambda args: runs.append(args) or "")
    source = el.WindowsEvents(log, sid=lambda: ME)
    source.events(Rec(), True)
    source.events(Rec(), True)
    assert len(runs) == 1


def test_query_asks_for_these_events_since_before_the_record():
    runs = []
    el.WindowsEvents(el.EventLog(lambda args: runs.append(args) or ""), sid=lambda: ME).events(Rec(), True)
    [args] = runs
    assert args[:2] == ["qe", "System"] and "/f:xml" in args
    query = next(a for a in args if a.startswith("/q:"))
    for part in ("EventID=12", "EventID=13", "EventID=1074", "EventID=7002", el.format_time(T - 60)):
        assert part in query


def test_by_time_not_the_logs_order():
    # A boot whose clock was hours ahead logged "later" events before these.
    skewed = [SHUTTING_DOWN(T + 4000), BOOTED(T + 18000)]
    found = events([*skewed, RESTART(T + 100), SHUTTING_DOWN(T + 104), BOOTED(T + 108)])
    assert found == [Event(T + 100, T + 104, "restart")]


def test_fast_startup_is_a_restart_in_the_same_boot():
    # Shut down with Fast Startup: power off, the logoff, then hibernating, and no new boot.
    found = events([RESTART(T + 100, "power off"), LOGOFF(T + 100.5), HIBERNATING(T + 101)], same_boot=True)
    assert Event(T + 100, T + 101, "restart") in found
    assert Event(T + 100.5, T + 100.5, "logout") in found  # the restart wins in restart.py


def test_hibernating_alone_isnt_a_restart():
    assert events([HIBERNATING(T + 100)], same_boot=True) == []


def test_a_1074_long_before_hibernating_isnt_fast_startup():
    assert (
        events([RESTART(T + 100, "power off"), HIBERNATING(T + 100 + el._FAST_STARTUP_GAP + 1)], same_boot=True) == []
    )


def test_a_crash_after_fast_startup_starts_at_the_last_event():
    # A Fast Startup shutdown, then this boot crashed: the crash isn't dated from the earlier 1074.
    log = [RESTART(T + 100, "power off"), HIBERNATING(T + 101), BOOTED(T + 900)]
    found = events(log, last_before=xml("Some-Provider", 1, T + 400))
    assert found == [Event(T + 100, T + 101, "restart"), Event(T + 400, T + 400, "restart")]
