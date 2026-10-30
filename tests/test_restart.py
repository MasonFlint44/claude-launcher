"""The restart rule, with made-up events: the same on every platform.
tests/linux/test_restart_journal.py has the rule with Linux's own events."""

import json

import pytest

from claude_launcher.platforms import Event
from claude_launcher.restart import KEEP, LEAD, SHARED, TAIL, Closed, Reason, RestartState, reasons, unknown
from conftest import FakeEvents, FakeHost, conversation

NOW = 1_800_000_000.0
THIS_BOOT = "1" * 32
LAST_BOOT = "2" * 32
BOOT_STARTED = NOW - 600
RESTART = Event(NOW - 700, NOW - 690, "restart")
LOGOUT = Event(NOW - 2000, NOW - 1990, "logout")


@pytest.fixture
def setup(claude_home):
    host = FakeHost(boot=THIS_BOOT, started=lambda now: BOOT_STARTED)
    clock = [NOW]

    def make(*events, readable=True):
        return RestartState(
            claude_home.paths,
            FakeEvents(list(events) if readable else None),
            host,
            now=lambda: clock[0],
        )

    def record(session_id, boot=LAST_BOOT, pid=100, started=NOW - 3600, ended=None, name=None):
        claude_home.paths.open_records.mkdir(parents=True, exist_ok=True)
        rec = {"session_id": session_id, "pid": pid, "boot_id": boot, "login_session": "", "started_at": started}
        if ended is not None:
            rec.update(end_reason="other", ended_at=ended)
        (claude_home.paths.open_records / (name or f"{boot}_{pid}")).write_text(json.dumps(rec))

    def session():
        return claude_home.log("/tmp", conversation("x", "/tmp"))

    class Setup:
        pass

    s = Setup()
    s.make, s.record, s.session, s.host, s.clock = make, record, session, host, clock
    return s


def records(claude_home):
    return sorted(p.name for p in claude_home.paths.open_records.iterdir())


@pytest.mark.parametrize(
    ("ended", "counts"),
    [
        (RESTART.start - LEAD, True),
        (RESTART.start - LEAD - 1, False),
        (RESTART.end + TAIL, True),
        (RESTART.end + TAIL + 1, False),
        ((RESTART.start + RESTART.end) / 2, True),
    ],
)
def test_margins(setup, ended, counts):
    sid = setup.session()
    setup.record(sid, ended=ended)
    group = setup.make(RESTART).sort()
    assert group.sessions == ({sid: Closed(RESTART.start, "restart")} if counts else {})


def test_restart_beats_the_logout_it_caused(setup):
    sid = setup.session()
    logout = Event(RESTART.start - 5, RESTART.start - 4, "logout")
    setup.record(sid, ended=RESTART.start - 4.5)
    assert setup.make(logout, RESTART).sort().sessions == {sid: Closed(RESTART.start, "restart")}


def test_the_earliest_of_two_logouts(setup):
    sid = setup.session()
    first, second = Event(NOW - 1000, NOW - 990, "logout"), Event(NOW - 995, NOW - 994, "logout")
    setup.record(sid, ended=NOW - 994.5)
    assert setup.make(second, first).sort().sessions == {sid: Closed(first.start, "logout")}


def test_never_ended_takes_the_first_event_after_it_started(setup):
    sid = setup.session()
    setup.record(sid, started=LOGOUT.start - 100)
    assert setup.make(RESTART, LOGOUT).sort().sessions == {sid: Closed(LOGOUT.start, "logout")}


def test_never_ended_in_an_earlier_boot_without_events_counts_from_boot(setup):
    sid = setup.session()
    setup.record(sid)
    assert setup.make().sort().sessions == {sid: Closed(BOOT_STARTED, "restart")}


def test_never_ended_in_this_boot_without_events_was_a_crash(setup, claude_home):
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT)
    assert len(setup.make().sort()) == 0
    assert records(claude_home) == []


def test_still_running_stays_and_leaves_the_group(setup, claude_home):
    sid = setup.session()
    setup.record(sid)
    setup.make().sort()
    setup.record(sid, boot=THIS_BOOT, pid=200)
    setup.host.running.add(200)
    assert len(setup.make().sort()) == 0
    assert records(claude_home) == [f"{THIS_BOOT}_200"]


class TestSystemCantBeAsked:
    def test_ended_waits(self, setup, claude_home):
        sid = setup.session()
        setup.record(sid, ended=NOW - 695)
        assert len(setup.make(readable=False).sort()) == 0
        assert records(claude_home) == [f"{LAST_BOOT}_100"]
        assert setup.make(RESTART).sort().sessions == {sid: Closed(RESTART.start, "restart")}

    def test_never_ended_in_an_earlier_boot_counts_from_boot(self, setup):
        sid = setup.session()
        setup.record(sid)
        assert setup.make(readable=False).sort().sessions == {sid: Closed(BOOT_STARTED, "restart")}

    def test_old_undecided_records_are_dropped(self, setup, claude_home):
        setup.record(setup.session(), started=NOW - KEEP - 1, ended=NOW - KEEP)
        setup.make(readable=False).sort()
        assert records(claude_home) == []


def test_expires_after_keep(setup):
    sid = setup.session()
    setup.record(sid, ended=NOW - 695)
    setup.make(RESTART).sort()
    setup.clock[0] = RESTART.start + KEEP + 1
    assert len(setup.make().load()) == 1  # load() only reads
    assert len(setup.make().sort()) == 0


def test_sessions_whose_log_is_gone_are_dropped(setup, claude_home):
    setup.record("0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", ended=NOW - 695)
    assert len(setup.make(RESTART).sort()) == 0


def test_bad_records_are_removed_and_ones_being_written_are_left(setup, claude_home):
    claude_home.paths.open_records.mkdir(parents=True)
    (claude_home.paths.open_records / f"{LAST_BOOT}_1").write_text("{not json")
    (claude_home.paths.open_records / f"{LAST_BOOT}_2.tmp").write_text("{}")
    setup.make(RESTART).sort()
    assert records(claude_home) == [f"{LAST_BOOT}_2.tmp"]


def test_drop_and_drop_all(setup):
    a, b = setup.session(), setup.session()
    setup.record(a, pid=1, ended=NOW - 695)
    setup.record(b, pid=2, ended=NOW - 695)
    state = setup.make(RESTART)
    assert len(state.sort()) == 2
    assert set(state.drop([a]).sessions) == {b}
    assert len(state.drop_all()) == 0


def test_only_loading_never_asks_for_events(setup, claude_home):
    class Refuses:
        def events(self, record, same_boot):
            raise AssertionError("asked for events")

    setup.record(setup.session(), ended=NOW - 695)
    state = RestartState(claude_home.paths, Refuses(), setup.host, now=lambda: NOW)
    state.load()
    state.drop([])


class Interrupted(Exception):
    pass


def test_a_sort_cut_short_loses_nothing(setup, claude_home):
    # A hang-up or TERM can end the picker mid-sort: the records it had
    # decided stay until the group they joined is saved.
    a, b = setup.session(), setup.session()
    setup.record(a, pid=1, ended=NOW - 695)
    setup.record(b, pid=2, ended=NOW - 695)

    class FailsOnTheSecond:
        asked = 0

        def events(self, record, same_boot):
            self.asked += 1
            if self.asked == 2:
                raise Interrupted
            return [RESTART]

    state = RestartState(claude_home.paths, FailsOnTheSecond(), setup.host, now=lambda: NOW)
    with pytest.raises(Interrupted):
        state.sort()
    assert records(claude_home) == [f"{LAST_BOOT}_1", f"{LAST_BOOT}_2"]
    assert set(setup.make(RESTART).sort().sessions) == {a, b}
    assert records(claude_home) == []


def test_one_restart_one_time_though_the_uptime_moves(setup, claude_home):
    # Linux works out the boot's start from the uptime, read to 10 ms.
    jitter = iter([0.0, 0.01, 0.02, 0.03])
    setup.host.started = lambda now: BOOT_STARTED + next(jitter)
    ids = [setup.session() for _ in range(3)]
    for pid, sid in enumerate(ids):
        setup.record(sid, pid=pid)
    assert {c.at for c in setup.make().sort().sessions.values()} == {BOOT_STARTED}


class TestReasons:
    STOP = Reason("stop", "it stopped", "stop", 15)  # a platform's own, between restart and logout

    def make(self, claude_home, setup, *events, reasons=None):
        return RestartState(claude_home.paths, FakeEvents(list(events)), setup.host, now=lambda: NOW, reasons=reasons)

    def test_a_platforms_own_reason_ranks_by_its_rank(self, setup, claude_home):
        sid = setup.session()
        stop, logout = Event(NOW - 700, NOW - 690, "stop"), Event(NOW - 701, NOW - 690, "logout")
        setup.record(sid, ended=NOW - 695)
        state = self.make(claude_home, setup, logout, stop, RESTART, reasons=reasons([self.STOP]))
        assert state.sort().sessions == {sid: Closed(RESTART.start, "restart")}
        other = setup.session()
        setup.record(other, pid=2, ended=NOW - 695)
        state = self.make(claude_home, setup, logout, stop, reasons=reasons([self.STOP]))
        assert state.sort().sessions[other] == Closed(stop.start, "stop")

    def test_an_unknown_reason_is_kept_and_ranks_last(self, setup, claude_home):
        sid = setup.session()
        stop, logout = Event(NOW - 700, NOW - 690, "stop"), Event(NOW - 701, NOW - 690, "logout")
        setup.record(sid, ended=NOW - 695)
        assert self.make(claude_home, setup, stop, logout, reasons=SHARED).sort().sessions == {
            sid: Closed(logout.start, "logout")
        }
        setup.record(sid, pid=2, ended=NOW - 695, started=NOW - 600)
        claude_home.paths.restored.write_text(json.dumps({sid: {"at": NOW - 60, "why": "stop"}}))
        assert self.make(claude_home, setup, reasons=SHARED).load().sessions == {sid: Closed(NOW - 60, "stop")}

    def test_reasons_come_in_rank_order(self):
        assert [r.key for r in reasons([self.STOP])] == ["restart", "stop", "logout"]
        assert unknown("x").rank > max(r.rank for r in reasons([self.STOP]))

    def test_the_platforms_reasons_by_default(self, setup, claude_home, monkeypatch):
        from test_platforms import platform

        from claude_launcher import platforms

        monkeypatch.setattr(platforms, "_current", platform("mine", reasons=(self.STOP,)))
        state = RestartState(claude_home.paths, FakeEvents([]), setup.host, now=lambda: NOW)
        assert state.ranks == {"restart": 10, "stop": 15, "logout": 20}
