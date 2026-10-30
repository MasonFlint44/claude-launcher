"""Restarts and logoffs from Windows' System log, read from wevtutil's XML.
Pure parsing, so these run on any OS; tests/windows checks the real log."""

import re
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


# The XPath wevtutil takes, as far as the event log's queries use it.
_TOKEN = re.compile(
    r"\s*(?:(?P<open>\()|(?P<close>\))|(?P<op>and|or)\b"
    r"|Provider\[@Name='(?P<provider>[^']*)'\]"
    r"|EventID=(?P<id>\d+)"
    r"|TimeCreated\[@SystemTime(?P<cmp>>=|<=|<|>)'(?P<time>[^']*)'\])"
)
_COMPARE = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}


def xpath_filter(query):
    """A predicate on LogEvents for `*[System[...]]`, or an error for
    anything else, so a query the real wevtutil would answer differently
    fails here too."""
    m = re.fullmatch(r"\*\[System\[(.*)\]\]", query)
    assert m, f"not a System query: {query}"
    text, tokens, pos = m.group(1), [], 0
    while pos < len(text):
        t = _TOKEN.match(text, pos)
        assert t and t.end() > pos, f"can't read {text[pos:]!r}"
        tokens.append(t)
        pos = t.end()

    def expr(i):
        test, i = term(i)
        while i < len(tokens) and tokens[i].group("op") == "or":
            right, i = term(i + 1)
            test = (lambda a, b: lambda e: a(e) or b(e))(test, right)
        return test, i

    def term(i):
        test, i = atom(i)
        while i < len(tokens) and tokens[i].group("op") == "and":
            right, i = atom(i + 1)
            test = (lambda a, b: lambda e: a(e) and b(e))(test, right)
        return test, i

    def atom(i):
        t = tokens[i]
        if t.group("open"):
            test, i = expr(i + 1)
            assert tokens[i].group("close"), "unbalanced"
            return test, i + 1
        if t.group("provider") is not None:
            return (lambda name: lambda e: e.provider == name)(t.group("provider")), i + 1
        if t.group("id"):
            return (lambda n: lambda e: e.id == n)(int(t.group("id"))), i + 1
        if t.group("cmp"):
            cmp, at = _COMPARE[t.group("cmp")], el.parse_time(t.group("time"))
            return (lambda e: cmp(e.at, at)), i + 1
        raise AssertionError(f"unexpected {t.group()!r}")

    test, i = expr(0)
    assert i == len(tokens), "trailing text"
    return test


class FakeLog(el.EventLog):
    """wevtutil over one list of events, in the log's order: it filters by the
    query, and honours /rd:true (newest first) and /c:N."""

    def __init__(self, events, now=T + 86400):
        self.events = events
        self.runs = []
        super().__init__(self._answer, now=lambda: now)

    def _answer(self, args):
        self.runs.append(args)
        assert args[:2] == ["qe", "System"] and "/f:xml" in args
        [query] = [a[3:] for a in args if a.startswith("/q:")]
        test = xpath_filter(query)
        found = [x for x in self.events if test(el.parse_events(x)[0])]
        if "/rd:true" in args:
            found.reverse()
        for a in args:
            if a.startswith("/c:"):
                found = found[: int(a[3:])]
        return "".join(found)


def events(log_events, same_boot=False, sid=ME):
    return el.WindowsEvents(FakeLog(log_events), sid=lambda: sid).events(Rec(), same_boot)


def other(at):
    return xml("Service Control Manager", 7036, at)


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
    found = events([other(T - 100), other(T + 300), other(T + 400), BOOTED(T + 500), other(T + 600)])
    assert found == [Event(T + 400, T + 400, "restart")]


def test_a_crash_during_a_restart_starts_at_its_1074():
    found = events([RESTART(T + 100), other(T + 130), BOOTED(T + 500)])
    assert found == [Event(T + 100, T + 130, "restart")]


def test_a_restart_called_off_long_before_a_crash():
    # shutdown /r, then shutdown /a, then a crash an hour later.
    found = events([RESTART(T + 100), other(T + 3700), BOOTED(T + 4000)])
    assert found == [Event(T + 3700, T + 3700, "restart")]


def test_a_restart_scheduled_long_before_it_happened():
    # shutdown /r /t 3600: tabs closed by hand in that hour weren't closed by it.
    found = events([RESTART(T + 100), SHUTTING_DOWN(T + 3700), BOOTED(T + 3800)])
    assert found == [Event(T + 3700, T + 3700, "restart")]  # restart.LEAD covers the closing


def test_a_restart_window_is_capped():
    # A 1074 within _BEGUN_MAX of the end, but further back than _SPAN_MAX.
    found = events([RESTART(T + 100), SHUTTING_DOWN(T + 100 + el._BEGUN_MAX - 1), BOOTED(T + 2000)])
    [restart] = found
    assert restart.end - restart.start == el._SPAN_MAX


def test_a_shutdown_without_a_1074_is_at_its_13():
    found = events([SHUTTING_DOWN(T + 100), other(T + 101), BOOTED(T + 200)])
    assert found == [Event(T + 100, T + 100, "restart")]


def test_same_boot_has_no_restart_yet():
    assert events([LOGOFF(T + 100)], same_boot=True) == [Event(T + 100, T + 100, "logout")]


def test_only_my_logoffs():
    assert events([LOGOFF(T + 100, OTHER)], same_boot=True) == []
    assert events([LOGOFF(T + 100)], same_boot=True, sid=None) == []  # can't tell whose


def test_a_log_that_cant_be_read_says_so():
    log = el.EventLog(lambda args: None)
    assert el.WindowsEvents(log, sid=lambda: ME).events(Rec(), False) is None


def test_one_query_for_many_records():
    # After a restart with many tabs: one wevtutil, one whoami, one
    # last-event query per boot, whatever order the records come in.
    log = FakeLog([RESTART(T + 100), SHUTTING_DOWN(T + 110), BOOTED(T + 130), BOOTED(T + 900)], now=T + 1000)
    asked = []
    source = el.WindowsEvents(log, sid=lambda: asked.append(1) or ME)
    for started in (T + 50, T + 20, T + 60, T + 200, T + 10):
        source.events(Rec(started=started), False)
    since = [a for a in log.runs if "/rd:true" not in a]
    assert len(since) == 1 and len(asked) == 1
    assert len([a for a in log.runs if "/rd:true" in a]) == 1  # the later boot's end, once


def test_a_record_older_than_the_first_query_asks_again():
    log = FakeLog([RESTART(T - 9 * 86400), SHUTTING_DOWN(T - 9 * 86400 + 5), BOOTED(T)], now=T + 1000)
    source = el.WindowsEvents(log, sid=lambda: ME)
    assert source.events(Rec(started=T - 100), True) == []
    old = source.events(Rec(started=T - 9 * 86400 - 100), False)
    assert old == [Event(T - 9 * 86400, T - 9 * 86400 + 5, "restart")]
    assert len(log.runs) == 2


def test_later_records_see_only_their_own_events():
    log = FakeLog([LOGOFF(T + 100), LOGOFF(T + 500)])
    source = el.WindowsEvents(log, sid=lambda: ME)
    source.events(Rec(started=T), True)
    later = source.events(Rec(started=T + 400 + 60), True)
    assert later == [Event(T + 500, T + 500, "logout")]


def test_query_asks_for_these_events_since_before_the_record():
    log = FakeLog([], now=T)
    el.WindowsEvents(log, sid=lambda: ME).events(Rec(), True)
    [args] = log.runs
    query = next(a for a in args if a.startswith("/q:"))
    pairs = {
        (provider, int(i))
        for provider, ids in re.findall(r"Provider\[@Name='([^']*)'\] and \(([^)]*)\)", query)
        for i in re.findall(r"EventID=(\d+)", ids)
    }
    assert pairs == {
        (el.KERNEL, 12),
        (el.KERNEL, 13),
        ("User32", 1074),
        ("Microsoft-Windows-Winlogon", 7002),
        ("Microsoft-Windows-Kernel-Power", 42),
    }
    assert f"@SystemTime>='{el.format_time(T - el._LOOKBACK)}'" in query


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
    log = [RESTART(T + 100, "power off"), HIBERNATING(T + 101), other(T + 400), BOOTED(T + 900)]
    found = events(log)
    assert found == [Event(T + 100, T + 101, "restart"), Event(T + 400, T + 400, "restart")]
