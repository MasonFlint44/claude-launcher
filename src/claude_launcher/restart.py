"""The sessions that were closed by a restart or a logout, to offer back.

claude-session-hook leaves a record in state/open/<boot id>_<pid> for each
interactive Claude. An exit you chose (/exit, /clear, switching with /resume) deletes it;
any other end (closing the tab, or the desktop closing every window) keeps it,
with the time it ended.

`sort()` runs each time the picker opens and decides, for each record whose
Claude has gone, whether a restart or a logout closed it. Both are an event
with a start and an end, which the platform's event source provides (on
Linux, the systemd journal: platforms/linux/journal.py).

A Claude that ended between LEAD seconds before an event started and TAIL
seconds after it ended was closed by it; a restart wins over a logout. One
that never recorded an end (the power was cut, or the hook never got to run)
was closed by the first event after it started.

Those join the group in restored.json, {session id: {"at": time, "why":
"restart" | "logout"}}, for KEEP seconds, or until resumed or dismissed. Any
other record is deleted once its Claude has gone.

This module is the same on every platform; the record and restored.json
formats are what each platform's hook and event source agree on.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import Paths

if TYPE_CHECKING:
    from .platforms import Event, EventSource, Host

_RECORD_NAME = re.compile(r"[0-9a-f-]{32,36}_[0-9]+")
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
REASONS = ("restart", "logout")
_UNDECIDED = object()


@dataclass(frozen=True)
class Closed:
    at: float  # when the restart or logout happened
    why: str  # "restart" or "logout"


@dataclass(frozen=True)
class Group:
    sessions: dict[str, Closed]

    @property
    def latest(self) -> Closed | None:
        return max(self.sessions.values(), key=lambda c: c.at, default=None)

    @property
    def reasons(self) -> set[str]:
        return {c.why for c in self.sessions.values()}

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
        with open(path) as f:
            value = json.load(f)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _parse_group(data: dict) -> dict[str, Closed]:
    group = {}
    for session_id, value in data.items():
        if isinstance(value, dict) and isinstance(value.get("at"), (int, float)) and value.get("why") in REASONS:
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

    @classmethod
    def load(cls, path: Path) -> Record | None:
        data = _load(path) or {}
        try:
            ended = data.get("ended_at")
            return cls(
                session_id=str(data["session_id"]),
                pid=data.get("pid"),
                boot_id=normalize_boot_id(str(data.get("boot_id", ""))),
                login_session=str(data.get("login_session", "")),
                started=float(data["started_at"]),
                ended=None if ended is None else float(ended),
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
    ):
        self.paths = paths
        self._events_source = events
        self._host = host
        self._now = now

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

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Closed]]:
        """The group, loaded under a lock and saved afterwards if it changed.
        Restored tabs can all open the picker at once."""
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
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
                os.replace(tmp, self.paths.restored)

    def load(self) -> Group:
        return Group(_parse_group(_load(self.paths.restored) or {}))

    def sort(self) -> Group:
        with self._locked() as group:
            self._sort_records(group)
        return self.load()

    def _sort_records(self, group: dict[str, Closed]) -> None:
        now = self._now()
        this_boot = normalize_boot_id(self.host.boot_id())
        for path in sorted(self.paths.open_records.iterdir()):
            if not _RECORD_NAME.fullmatch(path.name):
                continue  # a record being written
            record = Record.load(path)
            if record is None:
                _remove(path)
                continue
            same_boot = record.boot_id == this_boot
            if same_boot and record.ended is None and self.host.claude_running(record.pid):
                group.pop(record.session_id, None)  # it's open again
                continue
            closed = self._closed_by(record, same_boot, now)
            if closed is _UNDECIDED:
                if now - record.started > KEEP:
                    _remove(path)
                continue  # decide once the system can be asked
            if (
                isinstance(closed, Closed)
                and now - closed.at < KEEP
                and self.paths.log_for(record.session_id)
                and (record.session_id not in group or group[record.session_id].at < closed.at)
            ):
                group[record.session_id] = closed
            _remove(path)

    def _closed_by(self, record: Record, same_boot: bool, now: float) -> Closed | None | object:
        """The restart or logout that closed the record's Claude, None if
        neither did, or _UNDECIDED if the system can't say yet."""
        events = self.events.events(record, same_boot)
        if events is None:  # the system can't be asked
            if record.ended is None and not same_boot:
                return Closed(self.host.boot_start(now), "restart")  # it was open when that boot ended
            return _UNDECIDED
        if record.ended is None:
            after = [e for e in events if e.start >= record.started]
            first = min(after, key=lambda e: e.start, default=None)
            if first:
                return Closed(first.start, first.why)
            if not same_boot:  # it was open when that boot ended, though the system no longer says when
                return Closed(self.host.boot_start(now), "restart")
            return None  # it crashed, or was killed
        matches = [e for e in events if closes(e, record.ended)]
        if not matches:
            return None
        best = min(matches, key=lambda e: (e.why != "restart", e.start))
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
