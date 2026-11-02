"""The restart rule, with made-up events: the same on every platform.
tests/linux/test_restart_journal.py has the rule with Linux's own events."""

import json
import os
from pathlib import Path

import pytest

from claude_launcher import restart
from claude_launcher.platforms import Event
from claude_launcher.restart import KEEP, LEAD, SHARED, TAIL, Closed, Reason, RestartState, reasons, unknown
from conftest import LAST_BOOT, NOW, THIS_BOOT, FakeEvents, FakeHost, conversation, write_record

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
        write_record(claude_home.paths, session_id, boot, pid, started, ended, name=name)

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


@pytest.mark.parametrize(("this_boot", "last_boot"), [("0" * 32, "f" * 32), ("f" * 32, "0" * 32)])
def test_running_again_stays_out_whichever_record_is_read_last(setup, claude_home, this_boot, last_boot):
    # Records are read in name order, and names start with the boot ID: the
    # last boot's record of a session running again can come after its own.
    setup.host.boot = this_boot
    sid = setup.session()
    setup.record(sid, boot=last_boot, ended=RESTART.start + 1)
    setup.record(sid, boot=this_boot, pid=200, started=NOW - 60)
    setup.host.running.add(200)
    assert len(setup.make(RESTART).sort()) == 0
    assert records(claude_home) == [f"{this_boot}_200"]


def test_the_newer_closing_wins_whichever_record_is_read_last(setup):
    # One session resumed in another boot and closed again: two records.
    # Records are read in name order, and the boot read last holds the older one.
    sid = setup.session()
    older = Event(NOW - 5000, NOW - 4990, "logout")
    setup.record(sid, boot="0" * 32, ended=RESTART.start + 1)
    setup.record(sid, boot="f" * 32, ended=older.start + 1)
    assert setup.make(older, RESTART).sort().sessions == {sid: Closed(RESTART.start, "restart")}


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
        setup.record(setup.session(), started=NOW - KEEP - 2, ended=NOW - KEEP - 1)
        setup.make(readable=False).sort()
        assert records(claude_home) == []

    def test_an_undecided_record_waits_from_its_end(self, setup, claude_home):
        setup.record(setup.session(), started=NOW - KEEP - 1, ended=NOW - 700)
        setup.make(readable=False).sort()
        assert records(claude_home) == [f"{LAST_BOOT}_100"]


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
    writing = claude_home.paths.open_records / f"{LAST_BOOT}_2.tmp"
    writing.write_text("{}")
    os.utime(writing, (NOW - 5, NOW - 5))
    setup.make(RESTART).sort()
    assert records(claude_home) == [f"{LAST_BOOT}_2.tmp"]


def test_a_record_left_half_written_is_removed_after_a_minute(setup, claude_home):
    # A hook killed between writing a record and renaming it.
    claude_home.paths.open_records.mkdir(parents=True)
    left = claude_home.paths.open_records / f"{LAST_BOOT}_2.tmp"
    left.write_text("{}")
    other = claude_home.paths.open_records / "notes.tmp"  # not a record's: left alone
    other.write_text("")
    for path in (left, other):
        os.utime(path, (NOW - 61, NOW - 61))
    setup.make(RESTART).sort()
    assert records(claude_home) == ["notes.tmp"]


def test_a_record_moved_aside_is_read(setup):
    # The hook keeps a record under <name>_<session id> when a new Claude gets its pid.
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, ended=LOGOUT.start + 1, name=f"{THIS_BOOT}_100_{sid}")
    assert setup.make(LOGOUT).sort().sessions == {sid: Closed(LOGOUT.start, "logout")}


def test_a_record_moved_aside_whose_session_runs_again_is_deleted(setup, claude_home):
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, started=LOGOUT.start - 100, name=f"{THIS_BOOT}_100_{sid}")
    setup.record(sid, boot=THIS_BOOT, pid=200, started=NOW - 60)
    setup.host.running.add(200)
    assert len(setup.make(LOGOUT).sort()) == 0
    assert records(claude_home) == [f"{THIS_BOOT}_200"]


def test_a_record_moved_aside_that_crashed_is_deleted(setup, claude_home):
    # No end, and nothing after it started in this boot: it crashed or was killed.
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, started=LOGOUT.end + 10, name=f"{THIS_BOOT}_100_{sid}")
    assert len(setup.make(LOGOUT).sort()) == 0
    assert records(claude_home) == []


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

        def boot_lost(self, record):
            return False

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


class TestRemove:
    def test_a_file_held_open_is_tried_again(self, tmp_path, monkeypatch):
        # Windows: the hook or an antivirus scan can hold it for a moment.
        path = tmp_path / "rec"
        path.write_text("{}")
        real, tries = Path.unlink, []

        def unlink(self, missing_ok=False):
            tries.append(self)
            if len(tries) < 3:
                raise PermissionError(13, "in use")
            real(self, missing_ok)

        monkeypatch.setattr(Path, "unlink", unlink)
        monkeypatch.setattr(restart.time, "sleep", lambda s: None)
        restart._remove(path)
        assert len(tries) == 3 and not path.exists()

    def test_gives_up_quietly(self, tmp_path, monkeypatch):
        tries = []

        def unlink(self, missing_ok=False):
            tries.append(self)
            raise PermissionError(13, "in use")

        monkeypatch.setattr(Path, "unlink", unlink)
        monkeypatch.setattr(restart.time, "sleep", lambda s: None)
        restart._remove(tmp_path / "rec")
        assert len(tries) == 5

    def test_a_missing_file_is_fine(self, tmp_path):
        restart._remove(tmp_path / "gone")


def test_the_platforms_host_and_events_are_made_on_first_use(setup, claude_home, monkeypatch):
    from test_platforms import platform

    from claude_launcher import platforms

    made = []

    def events():
        made.append("events")
        return FakeEvents([RESTART])

    monkeypatch.setattr(platforms, "_current", platform("mine", host=setup.host, events=events))
    state = RestartState(claude_home.paths, now=lambda: NOW, reasons=SHARED)
    sid = setup.session()
    setup.record(sid, ended=NOW - 695)
    state.load()
    assert made == []
    assert state.sort().sessions == {sid: Closed(RESTART.start, "restart")}
    assert state.host is setup.host
    assert made == ["events"]
    state.sort()
    assert made == ["events"]  # once per run


@pytest.mark.parametrize(
    ("extra", "ticks"),
    [
        ({"start_ticks": 12345}, 12345),
        ({}, None),  # an older hook's, or Windows'
        ({"start_ticks": "12345"}, None),
        ({"start_ticks": True}, None),
        ({"start_ticks": 1.5}, None),
        ({"some_newer_key": [1, 2]}, None),  # keys it doesn't know are left alone
    ],
)
def test_record_loads_start_ticks_if_there(tmp_path, extra, ticks):
    from claude_launcher.restart import Record

    path = tmp_path / "record"
    data = {"session_id": "s", "pid": 7, "boot_id": THIS_BOOT, "login_session": "", "started_at": NOW, **extra}
    path.write_text(json.dumps(data))
    record = Record.load(path)
    assert record is not None and record.start_ticks == ticks and record.started == NOW


@pytest.mark.parametrize(
    "bad",
    [
        {"started_at": "nan"},
        {"started_at": "inf"},
        {"ended_at": "nan"},
        {"ended_at": "-inf"},
        {"started_at": float("nan")},
    ],
)
def test_record_times_that_arent_times_are_bad(tmp_path, bad):
    from claude_launcher.restart import Record

    path = tmp_path / "record"
    data = {"session_id": "s", "pid": 7, "boot_id": THIS_BOOT, "login_session": "", "started_at": NOW, **bad}
    path.write_text(json.dumps(data))
    assert Record.load(path) is None


def test_the_host_gets_the_records_start_ticks(setup, claude_home):
    asked = []

    def running(pid, started=None, start_ticks=None):
        asked.append((pid, started, start_ticks))
        return True

    setup.host.claude_running = running
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, pid=100, started=NOW - 60)
    path = claude_home.paths.open_records / f"{THIS_BOOT}_100"
    path.write_text(json.dumps({**json.loads(path.read_text()), "start_ticks": 4321}))
    setup.make().sort()
    assert asked == [(100, NOW - 60, 4321)]
    assert path.exists()  # still running: kept


def test_a_record_a_new_claude_wrote_during_the_sort_is_kept(setup, claude_home):
    # The sort asks for events (seconds, on Linux) after reading the records.
    # Meanwhile a new Claude gets the gone one's pid: its hook moves the old
    # record aside and writes its own under the name the sort decided on.
    old, new = setup.session(), setup.session()
    setup.record(old, boot=THIS_BOOT, started=NOW - 600)
    path = claude_home.paths.open_records / f"{THIS_BOOT}_100"

    class PidReused:
        def events(self, record, same_boot):
            os.replace(path, f"{path}_{old}")
            write_record(claude_home.paths, new, THIS_BOOT, 100, NOW - 1)
            return [Event(NOW - 300, NOW - 299, "logout")]

    state = RestartState(claude_home.paths, PidReused(), setup.host, now=lambda: NOW)
    assert state.sort().sessions == {old: Closed(NOW - 300, "logout")}
    assert records(claude_home) == sorted([f"{THIS_BOOT}_100", f"{THIS_BOOT}_100_{old}"])
    assert json.loads(path.read_text())["session_id"] == new


def test_a_record_the_same_session_wrote_again_during_the_sort_is_kept(setup, claude_home):
    # The session resumed under the same pid while the sort ran: its new record
    # has the same name and session ID, but it's another record.
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, started=NOW - 600, ended=NOW - 299.5)
    path = claude_home.paths.open_records / f"{THIS_BOOT}_100"

    class Resumed:
        def events(self, record, same_boot):
            write_record(claude_home.paths, sid, THIS_BOOT, 100, NOW - 1)
            return [Event(NOW - 300, NOW - 299, "logout")]

    state = RestartState(claude_home.paths, Resumed(), setup.host, now=lambda: NOW)
    assert state.sort().sessions == {sid: Closed(NOW - 300, "logout")}
    assert json.loads(path.read_text())["started_at"] == NOW - 1


# A boot the system has lost (a journal kept only in memory, a full or
# cleared event log): the latest end among its records stands in for its
# restart.
OTHER_BOOT = "3" * 32


def lost(setup, *events):
    state = setup.make(*events)
    state.events.lost = True
    return state


def test_a_lost_boot_offers_back_only_the_ends_at_its_last_moment(setup):
    by_hand, a, b = setup.session(), setup.session(), setup.session()
    setup.record(by_hand, pid=1, ended=NOW - 2000)
    setup.record(a, pid=2, ended=NOW - 705)
    setup.record(b, pid=3, ended=NOW - 700)
    assert lost(setup).sort().sessions == {a: Closed(NOW - 700, "restart"), b: Closed(NOW - 700, "restart")}


@pytest.mark.parametrize(("before", "counts"), [(LEAD, True), (LEAD + 1, False)])
def test_a_lost_boot_has_the_usual_margin(setup, before, counts):
    last, earlier = setup.session(), setup.session()
    setup.record(last, pid=1, ended=NOW - 700)
    setup.record(earlier, pid=2, ended=NOW - 700 - before)
    assert (earlier in lost(setup).sort().sessions) is counts


def test_each_lost_boot_has_its_own_last_moment(setup):
    a, b = setup.session(), setup.session()
    setup.record(a, pid=1, ended=NOW - 5000)
    setup.record(b, boot=OTHER_BOOT, pid=1, ended=NOW - 700)
    assert lost(setup).sort().sessions == {a: Closed(NOW - 5000, "restart"), b: Closed(NOW - 700, "restart")}


def test_a_lost_boots_last_moment_counts_a_session_open_again(setup):
    # It's open again, so it isn't offered back, but its end still says when
    # that boot ended: the tab closed by hand long before isn't a ↻ row.
    reopened, by_hand = setup.session(), setup.session()
    setup.record(reopened, pid=1, ended=NOW - 700)
    setup.record(by_hand, pid=2, ended=NOW - 2000)
    setup.record(reopened, boot=THIS_BOOT, pid=200, started=NOW - 60)
    setup.host.running.add(200)
    assert lost(setup).sort().sessions == {}


def test_a_lost_boots_last_moment_counts_a_session_whose_log_is_gone(setup):
    by_hand = setup.session()
    setup.record("0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", pid=1, ended=NOW - 700)
    setup.record(by_hand, pid=2, ended=NOW - 2000)
    assert lost(setup).sort().sessions == {}


def test_a_lost_boots_session_with_no_end_still_counts_from_this_boot(setup):
    open_, ended = setup.session(), setup.session()
    setup.record(open_, pid=1)
    setup.record(ended, pid=2, ended=NOW - 700)
    assert lost(setup).sort().sessions == {
        open_: Closed(BOOT_STARTED, "restart"),
        ended: Closed(NOW - 700, "restart"),
    }


def test_a_lost_boots_events_still_count(setup):
    # WSL: the Windows log is lost, but WSL stopping is WSL's own.
    sid = setup.session()
    logout = Event(NOW - 2000, NOW - 1999, "logout")
    setup.record(sid, pid=1, ended=NOW - 1999.5)
    setup.record(setup.session(), pid=2, ended=NOW - 700)
    assert lost(setup, logout).sort().sessions[sid] == Closed(logout.start, "logout")


def test_a_boot_not_lost_has_no_stand_in(setup, claude_home):
    sid = setup.session()
    setup.record(sid, ended=NOW - 700)
    assert setup.make().sort().sessions == {}
    assert records(claude_home) == []


def test_whether_a_boot_is_lost_can_wait(setup, claude_home):
    sid = setup.session()
    setup.record(sid, ended=NOW - 700)
    state = setup.make()
    state.events.lost = None
    assert state.sort().sessions == {}
    assert records(claude_home) == [f"{LAST_BOOT}_100"]
    assert lost(setup).sort().sessions == {sid: Closed(NOW - 700, "restart")}


class NeverAsked(FakeEvents):
    def boot_lost(self, record):
        raise AssertionError("asked whether the boot is lost")


def test_this_boots_record_an_event_closed_never_asks_if_its_lost(setup, claude_home):
    sid = setup.session()
    setup.record(sid, boot=THIS_BOOT, ended=LOGOUT.start + 1)
    state = RestartState(claude_home.paths, NeverAsked([LOGOUT]), setup.host, now=lambda: NOW)
    assert state.sort().sessions == {sid: Closed(LOGOUT.start, "logout")}


class TestThisBootLost:
    """Windows' Fast Startup shuts down without a new boot, so a record of
    this boot can have ended before what the log still has. Nothing closed
    it as far as the system knows, but it waits rather than go."""

    def test_not_lost_goes(self, setup, claude_home):
        setup.record(setup.session(), boot=THIS_BOOT, ended=NOW - 100)
        assert setup.make().sort().sessions == {}
        assert records(claude_home) == []

    @pytest.mark.parametrize("answer", [True, None], ids=["lost", "cant-say"])
    def test_lost_waits(self, setup, claude_home, answer):
        setup.record(setup.session(), boot=THIS_BOOT, ended=NOW - 100)
        state = setup.make()
        state.events.lost = answer
        assert state.sort().sessions == {}
        assert records(claude_home) == [f"{THIS_BOOT}_100"]

    def test_the_next_full_restart_works_it_out(self, setup, claude_home):
        by_hand, open_at_shutdown = setup.session(), setup.session()
        setup.record(by_hand, pid=1, boot=THIS_BOOT, ended=NOW - 5000)
        setup.record(open_at_shutdown, pid=2, boot=THIS_BOOT, ended=NOW - 3000)
        assert lost(setup).sort().sessions == {}
        assert len(records(claude_home)) == 2
        setup.host.boot = "4" * 32  # a full restart
        assert lost(setup).sort().sessions == {open_at_shutdown: Closed(NOW - 3000, "restart")}
        assert records(claude_home) == []

    def test_it_waits_until_keep_after_its_end(self, setup, claude_home):
        setup.record(setup.session(), boot=THIS_BOOT, started=NOW - KEEP - 100, ended=NOW - KEEP - 1)
        assert lost(setup).sort().sessions == {}
        assert records(claude_home) == []


class TestMovedAsideByItsOwnClaude:
    """Claude ran the start hook for its next session (/clear, /resume)
    before the end hook for this one: the record was moved aside, and the
    end hook didn't find it. Its own Claude still runs."""

    def test_it_was_an_end_you_chose(self, setup, claude_home):
        old, new = setup.session(), setup.session()
        setup.record(old, boot=THIS_BOOT, started=NOW - 600, name=f"{THIS_BOOT}_100_{old}")
        setup.record(new, boot=THIS_BOOT, started=NOW - 60)
        setup.host.running.add(100)
        assert len(setup.make(LOGOUT).sort()) == 0
        assert records(claude_home) == [f"{THIS_BOOT}_100"]
        # Not offered back once that Claude has gone, either.
        setup.host.running.clear()
        assert old not in setup.make(Event(NOW - 30, NOW - 20, "logout")).sort().sessions

    def test_a_dead_claudes_is_sorted_as_before(self, setup, claude_home):
        # Moved aside when another Claude got its pid: the host says the
        # pid's process isn't the record's own, so it's sorted as any other.
        old = setup.session()
        setup.record(old, boot=THIS_BOOT, started=LOGOUT.start - 100, name=f"{THIS_BOOT}_100_{old}")
        setup.record(setup.session(), boot=THIS_BOOT, started=NOW - 60)

        class Host(FakeHost):
            def claude_running(self, pid, started=None, start_ticks=None):
                return pid == 100 and started == NOW - 60

        host = Host(boot=THIS_BOOT, started=lambda now: BOOT_STARTED)
        state = RestartState(claude_home.paths, FakeEvents([LOGOUT]), host, now=lambda: NOW)
        assert state.sort().sessions == {old: Closed(LOGOUT.start, "logout")}
        assert records(claude_home) == [f"{THIS_BOOT}_100"]
