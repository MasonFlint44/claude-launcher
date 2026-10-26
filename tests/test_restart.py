import json

import pytest

from claude_launcher.journal import (
    GRAPHICAL_SESSION_UNIT,
    SESSION_LOGGED_OUT_FUNC,
    SESSION_NEW_MESSAGE_ID,
    SESSION_REMOVED_MESSAGE_ID,
    SHUTDOWN_MESSAGE_ID,
    UNIT_STOPPED_MESSAGE_ID,
    Journal,
)
from claude_launcher.restart import KEEP, Closed, RestartState, is_claude
from conftest import conversation

NOW = 1_800_000_000.0
THIS_BOOT = "11111111-1111-1111-1111-111111111111"
LAST_BOOT = "22222222-2222-2222-2222-222222222222"
BOOT_STARTED = NOW - 600  # this boot began ten minutes ago
REBOOT = NOW - 700  # logind's "System is rebooting." in the last boot
USER = "me"


def boot_key(boot):
    return boot.replace("-", "")


def stamp(t):
    return str(int(t * 1e6))


class FakeJournal:
    """journalctl, for LAST_BOOT and THIS_BOOT.

    `sessions` lists USER's login sessions that ended, as (boot, session id,
    type, logged out at), optionally followed by how long removal took
    (default 0.5 s) and whether the "logged out" message is there (default yes).
    """

    def __init__(
        self, shutdown_message=REBOOT, last_entry=REBOOT + 5, readable=True, sessions=(), desktop=(), system=True
    ):
        self.shutdown_message, self.last_entry, self.readable = shutdown_message, last_entry, readable
        self.sessions = list(sessions)
        self.desktop = list(desktop)  # (boot, when graphical-session.target stopped)
        self.system = system  # False: only the user journal can be read
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.readable:
            return None
        if args[0] == "--user":
            boot = args[2]
            assert f"USER_UNIT={GRAPHICAL_SESSION_UNIT}" in args and f"MESSAGE_ID={UNIT_STOPPED_MESSAGE_ID}" in args
            lines = [
                {"JOB_TYPE": "stop", "JOB_RESULT": "done", "__REALTIME_TIMESTAMP": stamp(t)}
                for b, t in self.desktop
                if boot_key(b) == boot
            ]
            return "".join(json.dumps(e) + "\n" for e in lines)
        if not self.system and args[0] != "--list-boots":
            return ""  # the system journal's messages aren't readable
        if args[0] == "--list-boots":
            boots = [
                {"boot_id": boot_key(LAST_BOOT), "first_entry": 1, "last_entry": int(self.last_entry * 1e6)},
                {"boot_id": boot_key(THIS_BOOT), "first_entry": int(BOOT_STARTED * 1e6), "last_entry": int(NOW * 1e6)},
            ]
            return json.dumps(boots)
        boot = args[1]
        if f"MESSAGE_ID={SHUTDOWN_MESSAGE_ID}" in args:
            if boot != boot_key(LAST_BOOT) or self.shutdown_message is None:
                return ""
            return json.dumps({"__REALTIME_TIMESTAMP": stamp(self.shutdown_message)}) + "\n"
        if f"MESSAGE_ID={SESSION_REMOVED_MESSAGE_ID}" in args:
            assert f"USER_ID={USER}" in args and f"CODE_FUNC={SESSION_LOGGED_OUT_FUNC}" in args
            lines = []
            for b, sid, type_, logged_out, *rest in self.sessions:
                removal_takes, has_logged_out = (list(rest) + [0.5, True][len(rest) :])[:2]
                if boot_key(b) != boot:
                    continue
                lines.append(
                    {
                        "MESSAGE_ID": SESSION_NEW_MESSAGE_ID,
                        "SESSION_ID": sid,
                        "TYPE": type_,
                        "__REALTIME_TIMESTAMP": stamp(logged_out - 3000),
                    }
                )
                if has_logged_out:
                    lines.append(
                        {
                            "CODE_FUNC": SESSION_LOGGED_OUT_FUNC,
                            "SESSION_ID": sid,
                            "__REALTIME_TIMESTAMP": stamp(logged_out),
                        }
                    )
                lines.append(
                    {
                        "MESSAGE_ID": SESSION_REMOVED_MESSAGE_ID,
                        "SESSION_ID": sid,
                        "__REALTIME_TIMESTAMP": stamp(logged_out + removal_takes),
                    }
                )
            lines.sort(key=lambda e: int(e["__REALTIME_TIMESTAMP"]))
            return "".join(json.dumps(e) + "\n" for e in lines)
        return None


@pytest.fixture
def setup(claude_home):
    running = set()

    def make(journal=None):
        return RestartState(
            claude_home.paths,
            Journal(journal or FakeJournal()),
            now=lambda: NOW,
            boot_id=lambda: THIS_BOOT,
            boot_start=lambda now: BOOT_STARTED,
            running=lambda pid: pid in running,
            user=lambda: USER,
        )

    def record(pid, session_id, boot=LAST_BOOT, started=NOW - 3600, ended=None, login_session="1", name=None):
        claude_home.paths.open_records.mkdir(parents=True, exist_ok=True)
        rec = {
            "session_id": session_id,
            "pid": pid,
            "boot_id": boot,
            "login_session": login_session,
            "started_at": started,
        }
        if ended is not None:
            rec.update(end_reason="other", ended_at=ended)
        (claude_home.paths.open_records / (name or f"{boot}_{pid}")).write_text(json.dumps(rec))

    def session():
        return claude_home.log("/tmp", conversation("x", "/tmp"))

    return make, record, session, running


def records(claude_home):
    """The pids of the records left."""
    return sorted(p.name.rsplit("_", 1)[-1] for p in claude_home.paths.open_records.iterdir())


def restored_file(claude_home):
    return json.loads(claude_home.paths.restored.read_text())


class TestRestart:
    def test_closed_by_the_reboot(self, setup, claude_home):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT + 0.8)  # Ghostty closed under it after logind's message
        assert make().sort().sessions == {sid: Closed(REBOOT, "restart")}
        assert records(claude_home) == []
        assert restored_file(claude_home) == {sid: {"at": REBOOT, "why": "restart"}}

    def test_closed_before_the_reboot(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), ended=REBOOT - 60)
        assert len(make().sort()) == 0
        assert records(claude_home) == []

    def test_closed_just_before_the_message_still_counts(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT - 1)
        assert sid in make().sort().sessions

    def test_never_ended(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid)
        assert make().sort().sessions == {sid: Closed(REBOOT, "restart")}

    def test_crash_uses_the_last_journal_entry(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT + 4.5)
        assert make(FakeJournal(shutdown_message=None)).sort().sessions == {sid: Closed(REBOOT + 5, "restart")}

    def test_reboot_beats_the_logout_it_caused(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT + 0.8)
        journal = FakeJournal(sessions=[(LAST_BOOT, "7", "wayland", REBOOT + 1.1)])
        assert make(journal).sort().sessions == {sid: Closed(REBOOT, "restart")}

    def test_reboot_from_a_terminal(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT + 3)  # logind first, then everything gets SIGTERM
        assert make().sort().sessions == {sid: Closed(REBOOT, "restart")}

    def test_desktop_closed_windows_before_logind_heard(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT - 25)
        assert make().sort().sessions == {sid: Closed(REBOOT, "restart")}

    def test_closed_too_long_before(self, setup):
        make, record, session, _ = setup
        record(100, session(), ended=REBOOT - 35)
        assert len(make().sort()) == 0

    def test_kde_style_logout_then_reboot_counts_as_the_restart(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT - 21)  # windows closed, then the session logged out, then the reboot
        journal = FakeJournal(sessions=[(LAST_BOOT, "2", "wayland", REBOOT - 20)])
        assert make(journal).sort().sessions == {sid: Closed(REBOOT, "restart")}

    def test_never_ended_in_a_boot_the_journal_lost(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot="33333333-3333-3333-3333-333333333333")
        assert make().sort().sessions == {sid: Closed(BOOT_STARTED, "restart")}

    def test_boot_not_in_the_journal(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), boot="33333333-3333-3333-3333-333333333333", ended=NOW - 900)
        assert len(make().sort()) == 0
        assert records(claude_home) == []


class TestLogout:
    LOGOUT = NOW - 120  # the desktop session logged out two minutes ago

    def journal(self, type_="wayland", session_id="2", removal_takes=0.5, has_logged_out=True):
        return FakeJournal(sessions=[(THIS_BOOT, session_id, type_, self.LOGOUT, removal_takes, has_logged_out)])

    def test_closed_by_logging_out(self, setup, claude_home):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT - 0.45)  # windows close just before it logs out
        assert make(self.journal()).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}
        assert records(claude_home) == []

    def test_hook_ran_late(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT + 0.5 + 8)  # 8 s after the removal
        assert make(self.journal()).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_closed_25_seconds_before_logging_out(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT - 25)
        assert make(self.journal()).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_removal_took_a_while(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT + 5)
        assert make(self.journal(removal_takes=6.75)).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_a_lingering_session_doesnt_stretch_the_window(self, setup):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, ended=self.LOGOUT + 45)  # a tab closed after logging back in
        assert len(make(self.journal(removal_takes=3600)).sort()) == 0

    def test_removal_stands_in_for_a_missing_logged_out_message(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT - 0.45)
        journal = self.journal(removal_takes=0.0, has_logged_out=False)
        assert make(journal).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_closed_well_before_logging_out(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, ended=self.LOGOUT - 35)
        assert len(make(self.journal()).sort()) == 0
        assert records(claude_home) == []

    def test_closed_after_logging_out(self, setup):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, ended=self.LOGOUT + 0.5 + 11)
        assert len(make(self.journal()).sort()) == 0

    def test_a_console_session_ending_doesnt_count(self, setup):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, ended=self.LOGOUT - 0.5)
        assert len(make(self.journal(type_="tty", session_id="8")).sort()) == 0

    def test_its_own_ssh_session_ending_counts(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT - 0.2, login_session="9")
        journal = self.journal(type_="tty", session_id="9")
        assert make(journal).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_never_ended_takes_the_first_logout_after_it_started(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, started=self.LOGOUT - 500)
        journal = FakeJournal(
            sessions=[
                (THIS_BOOT, "2", "wayland", self.LOGOUT - 900),  # before it started
                (THIS_BOOT, "5", "wayland", self.LOGOUT),
            ]
        )
        assert make(journal).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_never_ended_without_a_logout_was_a_crash(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, started=NOW - 60)
        assert len(make().sort()) == 0
        assert records(claude_home) == []

    def test_logout_in_an_earlier_boot(self, setup):
        make, record, session, _ = setup
        sid = session()
        logout = REBOOT - 3000
        record(100, sid, started=logout - 600, ended=logout - 0.5)
        journal = FakeJournal(sessions=[(LAST_BOOT, "2", "wayland", logout)])
        assert make(journal).sort().sessions == {sid: Closed(logout, "logout")}


class TestThisBoot:
    def test_running_stays(self, setup, claude_home):
        make, record, session, running = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, started=NOW - 60)
        running.add(100)
        assert len(make().sort()) == 0
        assert records(claude_home) == ["100"]

    def test_closed_tab_is_deleted(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, started=NOW - 60, ended=NOW - 30)
        make().sort()
        assert records(claude_home) == []

    def test_same_pid_in_two_boots(self, setup, claude_home):
        # pids start over each boot: a Claude can get the pid of one a restart closed.
        make, record, session, running = setup
        closed, new = session(), session()
        record(100, closed, ended=REBOOT + 0.8)
        record(100, new, boot=THIS_BOOT, started=NOW - 60)
        running.add(100)
        assert list(make().sort().sessions) == [closed]
        assert records(claude_home) == ["100"]

    def test_reopened_session_leaves_the_group(self, setup):
        make, record, session, running = setup
        sid = session()
        record(100, sid)
        state = make()
        assert sid in state.sort().sessions
        record(200, sid, boot=THIS_BOOT, started=NOW - 10)
        running.add(200)
        assert sid not in state.sort().sessions


class TestJournalUnreadable:
    def test_ended_waits(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), ended=REBOOT + 1)
        assert len(make(FakeJournal(readable=False)).sort()) == 0
        assert records(claude_home) == ["100"]  # kept, to decide once the journal can be read

    def test_this_boot_waits(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, started=NOW - 60, ended=NOW - 30)
        make(FakeJournal(readable=False)).sort()
        assert records(claude_home) == ["100"]

    def test_never_ended_counts_from_boot(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid)
        assert make(FakeJournal(readable=False)).sort().sessions == {sid: Closed(BOOT_STARTED, "restart")}

    def test_old_undecided_records_are_dropped(self, setup, claude_home):
        make, record, session, _ = setup
        record(100, session(), started=NOW - KEEP - 10, ended=NOW - KEEP)
        make(FakeJournal(readable=False)).sort()
        assert records(claude_home) == []


class TestGroup:
    def test_expires(self, setup, claude_home):
        make, _, session, _ = setup
        sid = session()
        claude_home.paths.state.mkdir(parents=True)
        claude_home.paths.restored.write_text(json.dumps({sid: {"at": NOW - KEEP - 1, "why": "restart"}}))
        assert len(make().sort()) == 0

    def test_drops_sessions_whose_log_is_gone(self, setup, claude_home):
        make, _, _, _ = setup
        claude_home.paths.state.mkdir(parents=True)
        gone = "0b2f0000-0000-0000-0000-000000000000"
        claude_home.paths.restored.write_text(json.dumps({gone: {"at": NOW - 60, "why": "logout"}}))
        assert len(make().sort()) == 0

    def test_ignores_bad_entries(self, setup, claude_home):
        make, _, session, _ = setup
        sid = session()
        claude_home.paths.state.mkdir(parents=True)
        claude_home.paths.restored.write_text(json.dumps({sid: NOW - 60}))
        assert len(make().sort()) == 0

    def test_latest_and_reasons(self, setup):
        make, record, session, _ = setup
        a, b = session(), session()
        record(1, a, ended=REBOOT + 1)
        logout = REBOOT - 3000
        record(2, b, started=logout - 600, ended=logout - 1)
        group = make(FakeJournal(sessions=[(LAST_BOOT, "2", "wayland", logout)])).sort()
        assert group.latest == Closed(REBOOT, "restart")
        assert group.reasons == {"restart", "logout"}

    def test_drop_and_drop_all(self, setup):
        make, record, session, _ = setup
        a, b, c = session(), session(), session()
        for pid, sid in ((1, a), (2, b), (3, c)):
            record(pid, sid)
        state = make()
        assert len(state.sort()) == 3
        assert set(state.drop([a]).sessions) == {b, c}
        assert len(state.drop_all()) == 0

    def test_bad_records_are_deleted(self, setup, claude_home):
        make, _, _, _ = setup
        claude_home.paths.open_records.mkdir(parents=True)
        (claude_home.paths.open_records / f"{THIS_BOOT}_5").write_text("{not json")
        (claude_home.paths.open_records / f"{THIS_BOOT}_6").write_text(json.dumps({"session_id": "x"}))
        make().sort()
        assert records(claude_home) == []


def test_journal_asked_once_per_boot(setup):
    make, record, session, _ = setup
    for pid in (1, 2, 3):
        record(pid, session())
    journal = FakeJournal()
    make(journal).sort()
    asked = [
        c[0] if c[0] in ("--list-boots", "--user") else ("shutdown" if any("98268866" in a for a in c) else "logouts")
        for c in journal.calls
    ]
    assert sorted(asked) == ["--list-boots", "--user", "logouts", "shutdown"]


class TestDesktopSession:
    """graphical-session.target, from the user journal."""

    LOGOUT = NOW - 120

    def test_logout_on_systemd_before_258(self, setup):
        # logind's messages there have no TYPE, so its desktop sessions look like any other.
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT + 0.19)
        journal = FakeJournal(sessions=[(THIS_BOOT, "2", "", self.LOGOUT + 0.6)], desktop=[(THIS_BOOT, self.LOGOUT)])
        assert make(journal).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_both_signals_give_one_answer(self, setup):
        make, record, session, _ = setup
        sid = session()
        record(100, sid, boot=THIS_BOOT, ended=self.LOGOUT + 0.19)
        journal = FakeJournal(
            sessions=[(THIS_BOOT, "2", "wayland", self.LOGOUT + 0.6)], desktop=[(THIS_BOOT, self.LOGOUT)]
        )
        assert make(journal).sort().sessions == {sid: Closed(self.LOGOUT, "logout")}

    def test_closed_long_after_the_desktop_ended(self, setup):
        make, record, session, _ = setup
        record(100, session(), boot=THIS_BOOT, ended=self.LOGOUT + 60)
        assert len(make(FakeJournal(desktop=[(THIS_BOOT, self.LOGOUT)])).sort()) == 0

    def test_restart_without_the_system_journal(self, setup):
        # Arch, say, before you're in a group that can read the system journal.
        make, record, session, _ = setup
        sid = session()
        record(100, sid, ended=REBOOT + 0.2)
        journal = FakeJournal(system=False, desktop=[(LAST_BOOT, REBOOT - 3000), (LAST_BOOT, REBOOT)])
        group = make(journal).sort()
        assert list(group.sessions) == [sid] and group.sessions[sid].why == "restart"

    def test_earlier_desktop_ends_in_that_boot_are_logouts(self, setup):
        make, record, session, _ = setup
        sid = session()
        logout = REBOOT - 3000
        record(100, sid, started=logout - 600, ended=logout + 0.2)
        journal = FakeJournal(system=False, desktop=[(LAST_BOOT, logout), (LAST_BOOT, REBOOT)])
        assert make(journal).sort().sessions == {sid: Closed(logout, "logout")}


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["claude"], True),
        (["/home/me/.local/bin/claude", "--resume", "x"], True),
        (["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], True),
        (["/usr/bin/node", "/home/me/.npm-global/bin/claude"], True),
        (["node", "/srv/app/index.js"], False),
        (["bash", "claude"], False),
        ([], False),
    ],
)
def test_is_claude(argv, expected):
    assert is_claude(argv) is expected
