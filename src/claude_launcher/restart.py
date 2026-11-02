"""The sessions that were closed by a restart or a logout, to offer back.

claude-session-hook leaves a record in state/open/<boot id>_<pid> for each
interactive Claude. An exit you chose (/exit, /clear, switching with /resume) deletes it;
any other end (closing the tab, or the desktop closing every window) keeps it,
with the time it ended. A new Claude that gets the pid of one whose record
is still there moves that record to <boot id>_<pid>_<its session id>. So
does the same Claude when it starts its next session (/clear, /resume)
before ending this one, so a record moved aside whose own Claude still runs
was an end it chose, and is deleted.

A record is one line of JSON (`Record.load` reads it and ignores keys it
doesn't know): "session_id", "pid", "boot_id", "login_session" (the login
session's ID, or ""; on Linux the audit session, which is logind's for an
SSH login but on a desktop with systemd 256 or later the user manager's),
"started_at" (a Unix time), and once it ended "end_reason" and "ended_at".
Optionally "start_ticks", when the process started in the platform's own
units (Linux: field 22 of /proc/<pid>/stat), which the host matches exactly
to tell its Claude from one that got the pid since; without it, the host
compares "started_at" with a margin.

`sort()` runs each time the picker opens and decides, for each record whose
Claude has gone, whether a restart or a logout closed it. Both are an event
with a start and an end, which the platform's event source provides (on
Linux, the systemd journal: platforms/linux/journal.py).

A Claude that ended between LEAD seconds before an event started and TAIL
seconds after it ended was closed by it; when several did, the reason with
the lowest rank wins: a restart over the logout it causes, and a platform's
own reasons (WSL stopping) after both. One that never recorded an end (the
power was cut, or the hook never got to run) was closed by the first event
after it started, or, with none from an earlier boot, when that boot ended:
the latest end among its records, as below, else this boot's start.

When the system has lost an earlier boot's end (the event source's
boot_lost: a journal kept only in memory, or a full or cleared event log),
the latest end among that boot's records stands in for its restart, under
the same margins. A restart closes every open Claude at once, so those ends
bunch together; a tab closed by hand well before them wasn't closed by it.
A record of this boot that nothing closed, from before what the system
still has (Windows' Fast Startup shuts down without a new boot), waits for
the boot to end.

Those join the group in restored.json, {session id: {"at": time, "why":
reason key}}, for KEEP seconds, or until resumed or dismissed. Any other
record is deleted once its Claude has gone, and only once the group is
saved, so a sort cut short loses nothing; and only if it's unchanged, since
a new Claude can take the pid and its name while the sort runs.

This module is the same on every platform; the record and restored.json
formats are what each platform's hook and event source agree on.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import Paths

if TYPE_CHECKING:
    from .platforms import Event, EventSource, Host

# <boot id>_<pid>, or with _<session id> after it: a record the hook moved
# aside when another session took its name. "<record>.tmp" is one being written.
_RECORD_NAME = re.compile(r"[0-9a-f-]{32,36}_[0-9]+(?:_[0-9a-f-]{36})?")
_TMP_STALE = 60  # a hook writes and renames in milliseconds
KEEP = 7 * 86400  # how long closed sessions stay in the group
# How close to a restart or logout a Claude has to have ended to count.
#
# LEAD: desktops close windows before logind hears about it. GNOME (measured
# on Ubuntu 26.04) closes them about 0.5 s before a desktop session logs out;
# KDE logs the whole session out before it asks logind to reboot; any desktop
# waits on apps that are slow to quit. A reboot run from a terminal is the one
# case where logind goes first.
# TAIL: Claude's SessionEnd hook stamps the end, and Claude may take a moment
# to run it after its terminal goes away.
#
# The margins are generous on purpose. Too wide, and a tab you closed yourself
# just before logging out shows up as a ↻ row you can dismiss. Too narrow, and
# a session the logout closed is missing from the list.
LEAD = 30
TAIL = 10
_UNDECIDED = object()


@dataclass(frozen=True)
class Reason:
    """Something that closes every Claude it catches, and how the picker says it."""

    key: str  # Event.why, and "why" in restored.json
    header: str  # under the title: "↻ 2 sessions were open when {header}"
    row: str  # a ↻ row's "when": "Mon {row}"
    rank: int  # when one event closed a Claude that another did too, the lowest wins


# Every platform's reasons. A platform adds its own in Platform.reasons.
SHARED = (
    Reason("restart", "your computer restarted", "restart", 10),
    Reason("logout", "you logged out", "logout", 20),
)


def unknown(key: str) -> Reason:
    """A reason no platform here gives (one from another version's
    restored.json, say): kept, ranked last, and said plainly."""
    return Reason(key, "Claude was stopped", "stop", 1_000_000)


def reasons(extra: Iterable[Reason] = ()) -> tuple[Reason, ...]:
    """The shared reasons and a platform's own, in the order they win."""
    return tuple(sorted((*SHARED, *extra), key=lambda r: r.rank))


@dataclass(frozen=True)
class Closed:
    at: float  # when the restart or logout happened
    why: str  # a Reason's key


@dataclass(frozen=True)
class Group:
    sessions: dict[str, Closed]

    def __len__(self) -> int:
        return len(self.sessions)


def normalize_boot_id(boot_id: str) -> str:
    """Boot IDs without dashes, in lower case: Linux's /proc has dashes, journalctl doesn't."""
    return boot_id.replace("-", "").lower()


def closes(event: Event, ended: float) -> bool:
    """Whether a Claude that ended at `ended` was closed by `event`."""
    return event.start - LEAD <= ended <= event.end + TAIL


def _load(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _remove(path: Path) -> None:
    # On Windows a file another process has open (the hook writing it, an
    # antivirus scan) can't be removed for a moment.
    for _ in range(5):
        try:
            path.unlink()
            return
        except PermissionError:
            time.sleep(0.01)
        except OSError:
            return


def _parse_group(data: dict) -> dict[str, Closed]:
    group = {}
    for session_id, value in data.items():
        why = value.get("why") if isinstance(value, dict) else None
        if isinstance(why, str) and why and isinstance(value.get("at"), (int, float)):
            group[session_id] = Closed(float(value["at"]), value["why"])
    return group


@dataclass(frozen=True)
class Record:
    session_id: str
    pid: object
    boot_id: str
    login_session: str
    started: float
    ended: float | None
    # When the process started, in the platform's own units, matched exactly
    # (Linux: clock ticks since boot). Optional: older hooks and Windows'
    # don't write it.
    start_ticks: int | None = None

    @classmethod
    def load(cls, path: Path) -> Record | None:
        data = _load(path) or {}
        try:
            started = float(data["started_at"])
            ended = None if data.get("ended_at") is None else float(data["ended_at"])
            if not math.isfinite(started) or not math.isfinite(ended or 0.0):  # "nan" and "inf" are floats too
                return None
            ticks = data.get("start_ticks")
            return cls(
                session_id=str(data["session_id"]),
                pid=data.get("pid"),
                boot_id=normalize_boot_id(str(data.get("boot_id", ""))),
                login_session=str(data.get("login_session", "")),
                started=started,
                ended=ended,
                start_ticks=ticks if isinstance(ticks, int) and not isinstance(ticks, bool) else None,
            )
        except (KeyError, TypeError, ValueError):
            return None


class RestartState:
    def __init__(
        self,
        paths: Paths,
        events: EventSource | None = None,
        host: Host | None = None,
        now: Callable[[], float] = time.time,
        reasons: Sequence[Reason] | None = None,
    ):
        self.paths = paths
        self._events_source = events
        self._host = host
        self._now = now
        self._reasons = reasons
        self._boot_started: float | None = None

    @property
    def host(self) -> Host:
        if self._host is None:
            from .platforms import current

            self._host = current().host
        return self._host

    @property
    def events(self) -> EventSource:
        """Made on first use: most runs only load the group."""
        if self._events_source is None:
            from .platforms import current

            self._events_source = current().events()
        return self._events_source

    @property
    def ranks(self) -> dict[str, int]:
        """Each reason's rank, the platform's own included. Only sorting asks."""
        if self._reasons is None:
            from .platforms import current

            self._reasons = reasons(current().reasons)
        return {r.key: r.rank for r in self._reasons}

    @contextmanager
    def _locked(self, decided: Iterable[tuple[Path, Record | None]] = ()) -> Iterator[dict[str, Closed]]:
        """The group, loaded under a lock and saved afterwards if it changed;
        then the `decided` records are deleted. Restored tabs can all open
        the picker at once."""
        self.paths.open_records.mkdir(parents=True, exist_ok=True)
        with self.host.lock(self.paths.lock):
            loaded = _parse_group(_load(self.paths.restored) or {})
            group = dict(loaded)
            yield group
            now = self._now()
            kept = {k: c for k, c in group.items() if now - c.at < KEEP and self.paths.log_for(k)}
            if kept != loaded or not self.paths.restored.exists():
                data = {k: {"at": c.at, "why": c.why} for k, c in kept.items()}
                tmp = self.paths.restored.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="")
                os.replace(tmp, self.paths.restored)
            for path, record in decided:
                # Only if it's still the record that was decided on: a new
                # Claude with its pid may have written its own there since.
                if Record.load(path) == record:
                    _remove(path)

    def load(self) -> Group:
        return Group(_parse_group(_load(self.paths.restored) or {}))

    def sort(self) -> Group:
        decided: list[tuple[Path, Record | None]] = []  # deleted once the group is saved
        with self._locked(decided) as group:
            self._sort_records(group, decided)
        return self.load()

    def _sort_records(self, group: dict[str, Closed], decided: list[tuple[Path, Record | None]]) -> None:
        now = self._now()
        self._boot_started = None
        this_boot = normalize_boot_id(self.host.boot_id())
        # The running sessions first: a session open again mustn't rejoin the
        # group from an older record of it read later.
        running: set[str] = set()
        gone: list[tuple[Path, Record, bool]] = []
        for path in sorted(self.paths.open_records.iterdir()):
            if not _RECORD_NAME.fullmatch(path.name):
                self._remove_stale_tmp(path, now)
                continue
            record = Record.load(path)
            if record is None:
                decided.append((path, None))
                continue
            same_boot = record.boot_id == this_boot
            if (
                same_boot
                and record.ended is None
                and self.host.claude_running(record.pid, record.started, record.start_ticks)
            ):
                if path.name.count("_") == 2:
                    # Moved aside, yet its own Claude still runs: that Claude
                    # started another session before ending this one (/clear
                    # or /resume, with the start hook run first), so the end
                    # hook didn't find it. An end it chose.
                    decided.append((path, record))
                    continue
                running.add(record.session_id)
            else:
                gone.append((path, record, same_boot))
        for session_id in running:
            group.pop(session_id, None)  # it's open again
        # The latest end in each earlier boot, from every record of it (one
        # whose session is open again, or whose log is gone, too): the nearest
        # thing to when that boot ended, for a boot the system has lost.
        boot_ends: dict[str, float] = {}
        for _, record, same_boot in gone:
            if not same_boot and record.ended is not None:
                boot_ends[record.boot_id] = max(boot_ends.get(record.boot_id, record.ended), record.ended)
        for path, record, same_boot in gone:
            if record.session_id in running:
                decided.append((path, record))
                continue
            closed = self._closed_by(record, same_boot, now, boot_ends.get(record.boot_id))
            if closed is _UNDECIDED:
                # Past KEEP from its end it couldn't join the group anyway.
                # From its start, a long-running Claude's record would go
                # before its boot is decided, and with it the latest end there.
                if now - (record.started if record.ended is None else record.ended) > KEEP:
                    decided.append((path, record))
                continue  # decide once the system can be asked
            if (
                isinstance(closed, Closed)
                and now - closed.at < KEEP
                and self.paths.log_for(record.session_id)
                and (record.session_id not in group or group[record.session_id].at < closed.at)
            ):
                group[record.session_id] = closed
            decided.append((path, record))

    @staticmethod
    def _remove_stale_tmp(path: Path, now: float) -> None:
        """A record being written, left behind by a hook killed before its
        rename once it's old."""
        if path.suffix != ".tmp" or not _RECORD_NAME.fullmatch(path.stem):
            return
        try:
            if now - path.stat().st_mtime > _TMP_STALE:
                _remove(path)
        except OSError:
            pass

    def _boot_start(self, now: float) -> float:
        """When this boot began, read once per sort: it's worked out from the
        uptime, which moves on between reads, and the sessions one restart
        closed should share one time."""
        if self._boot_started is None:
            self._boot_started = self.host.boot_start(now)
        return self._boot_started

    def _open_at_boot_end(self, record: Record, now: float, boot_end: float | None) -> Closed:
        """An earlier boot's record with no end, that no event closed: it was
        open when that boot ended. The latest end among the boot's records
        stands in for when, as for a lost boot, so one restart is one time;
        without one after it started, this boot's start."""
        if boot_end is not None and boot_end >= record.started:
            return Closed(boot_end, "restart")
        return Closed(self._boot_start(now), "restart")

    def _closed_by(
        self, record: Record, same_boot: bool, now: float, boot_end: float | None = None
    ) -> Closed | None | object:
        """The restart or logout that closed the record's Claude, None if
        neither did, or _UNDECIDED if the system can't say yet. `boot_end`:
        the latest end among the records of the record's boot."""
        events = self.events.events(record, same_boot)
        if events is None:  # the system can't be asked
            if record.ended is None and not same_boot:
                return self._open_at_boot_end(record, now, boot_end)
            return _UNDECIDED
        if record.ended is None:
            after = [e for e in events if e.start >= record.started]
            first = min(after, key=lambda e: e.start, default=None)
            if first:
                return Closed(first.start, first.why)
            if not same_boot:  # it was open when that boot ended, though the system no longer says when
                return self._open_at_boot_end(record, now, boot_end)
            return None  # it crashed, or was killed
        if not same_boot:
            lost = self.events.boot_lost(record)
            if lost is None:
                return _UNDECIDED
            if lost and boot_end is not None:
                # A restart closes every Claude still open within seconds, so
                # their ends bunch at the boot's last moment, and the latest
                # end stands in for the restart. If none was open then, the
                # last tab closed by hand comes back: a ↻ row to dismiss.
                from .platforms import Event  # here: platforms imports this module

                events = [*events, Event(boot_end, boot_end, "restart")]
        matches = [e for e in events if closes(e, record.ended)]
        if not matches:
            # In this boot, the system can still have lost what closed it:
            # Windows' Fast Startup keeps the boot across shutdowns, and the
            # log may have dropped one. Then it waits, for the next full
            # restart and the stand-in above, or until KEEP after its end.
            if same_boot and self.events.boot_lost(record) is not False:
                return _UNDECIDED
            return None
        ranks = self.ranks
        best = min(matches, key=lambda e: (ranks.get(e.why, unknown(e.why).rank), e.start))
        return Closed(best.start, best.why)

    def drop(self, session_ids: Iterable[str]) -> Group:
        with self._locked() as group:
            for session_id in session_ids:
                group.pop(session_id, None)
        return self.load()

    def drop_all(self) -> Group:
        with self._locked() as group:
            group.clear()
        return self.load()
