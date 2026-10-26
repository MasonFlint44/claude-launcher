"""The sessions that were closed by a restart or a logout, to offer back.

claude-session-hook leaves a record in state/open/<pid> for each interactive
Claude. An exit you chose (/exit, /clear, switching with /resume) deletes it;
any other end (closing the tab, or the desktop closing every window) keeps it,
with the time it ended.

`sort()` runs each time the picker opens and decides, for each record whose
Claude has gone, whether a restart or a logout closed it. Both are an event
with a start and an end, from the systemd journal:

- Restart: from logind's shutdown message (or, after a crash or power cut, the
  boot's last journal entry) to the end of that boot.
- Logout: your desktop session ending (your systemd user manager stopping
  graphical-session.target, just before the windows close), or logind's
  "logged out" for a desktop session of yours or for the login session Claude
  itself ran in (an SSH session, say), to logind removing that session.
- Without logind's shutdown message (the system journal can't be read), the
  last desktop session to end in an earlier boot stands in for its restart.

A Claude that ended between LEAD seconds before an event started and TAIL
seconds after it ended was closed by it; a restart wins over a logout. One
that never recorded an end (the power was cut, or the hook never got to run)
was closed by the first event after it started.

Those join the group in restored.json, {session id: {"at": time, "why":
"restart" | "logout"}}, for KEEP seconds, or until resumed or dismissed. Any
other record is deleted once its Claude has gone.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pwd
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .journal import Journal, normalize_boot_id
from .paths import Paths

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
# SPAN_MAX: a session's processes can linger long after it logs out, so its
# removal can come much later; past this, the logout is treated as over.
#
# The margins are generous on purpose. Too wide, and a tab you closed yourself
# just before logging out shows up as a ↻ row you can dismiss. Too narrow, and
# a session the logout closed is missing from the list.
LEAD = 30
TAIL = 10
SPAN_MAX = 30
REASONS = ("restart", "logout")
_UNDECIDED = object()


@dataclass(frozen=True)
class Closed:
    at: float  # when the restart or logout happened
    why: str  # "restart" or "logout"


@dataclass(frozen=True)
class _Event:
    start: float
    end: float
    why: str

    def closed(self, ended: float) -> bool:
        return self.start - LEAD <= ended <= self.end + TAIL


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


def current_boot_id() -> str:
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


def boot_start(now: float) -> float:
    with open("/proc/uptime") as f:
        return now - float(f.read().split()[0])


def is_claude(argv: list[str]) -> bool:
    """Whether a command line is Claude Code's: the native `claude` binary, or
    the npm package run by node (whose process name is just "MainThread")."""
    if not argv:
        return False
    if os.path.basename(argv[0]) == "claude":
        return True
    return os.path.basename(argv[0]).startswith("node") and any(
        os.path.basename(a) == "claude" or "/@anthropic-ai/claude-code/" in a for a in argv[1:3]
    )


def claude_running(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().decode(errors="replace").split("\0")
    except OSError:
        return False
    return is_claude([a for a in argv if a])


def current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


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
class _Record:
    session_id: str
    pid: object
    boot_id: str
    login_session: str
    started: float
    ended: float | None

    @classmethod
    def load(cls, path: Path) -> _Record | None:
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
        journal: Journal | None = None,
        now: Callable[[], float] = time.time,
        boot_id: Callable[[], str] = current_boot_id,
        boot_start: Callable[[float], float] = boot_start,
        running: Callable[[object], bool] = claude_running,
        user: Callable[[], str] = current_user,
    ):
        self.paths = paths
        self.journal = journal or Journal()
        self._now = now
        self._boot_id = boot_id
        self._boot_start = boot_start
        self._running = running
        self._user = user

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Closed]]:
        """The group, loaded under a lock and saved afterwards if it changed.
        Restored tabs can all open the picker at once."""
        self.paths.open_records.mkdir(parents=True, exist_ok=True)
        with open(self.paths.lock, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
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
        this_boot = normalize_boot_id(self._boot_id())
        for path in sorted(self.paths.open_records.iterdir()):
            if not path.name.isdigit():
                continue
            record = _Record.load(path)
            if record is None:
                _remove(path)
                continue
            same_boot = record.boot_id == this_boot
            if same_boot and record.ended is None and self._running(record.pid):
                group.pop(record.session_id, None)  # it's open again
                continue
            closed = self._closed_by(record, same_boot, now)
            if closed is _UNDECIDED:
                if now - record.started > KEEP:
                    _remove(path)
                continue  # decide once the journal can be read
            if (
                isinstance(closed, Closed)
                and now - closed.at < KEEP
                and self.paths.log_for(record.session_id)
                and (record.session_id not in group or group[record.session_id].at < closed.at)
            ):
                group[record.session_id] = closed
            _remove(path)

    def _closed_by(self, record: _Record, same_boot: bool, now: float) -> Closed | None | object:
        """The restart or logout that closed the record's Claude, None if
        neither did, or _UNDECIDED if the journal can't say yet."""
        events = self._events(record, same_boot)
        if events is None:  # the journal can't be read
            if record.ended is None and not same_boot:
                return Closed(self._boot_start(now), "restart")  # it was open when that boot ended
            return _UNDECIDED
        if record.ended is None:
            after = [e for e in events if e.start >= record.started]
            first = min(after, key=lambda e: e.start, default=None)
            if first:
                return Closed(first.start, first.why)
            if not same_boot:  # it was open when that boot ended, though the journal no longer says when
                return Closed(self._boot_start(now), "restart")
            return None  # it crashed, or was killed
        matches = [e for e in events if e.closed(record.ended)]
        if not matches:
            return None
        best = min(matches, key=lambda e: (e.why != "restart", e.start))
        return Closed(best.start, best.why)

    def _events(self, record: _Record, same_boot: bool) -> list[_Event] | None:
        """The restarts and logouts that could have closed the record's Claude,
        in its boot, or None if the journal can't be read."""
        logouts = self.journal.logouts(record.boot_id, self._user())
        if logouts is None:
            return None
        events = [
            _Event(e.at, min(e.removed, e.at + SPAN_MAX), "logout")
            for e in logouts
            if e.graphical or (record.login_session and e.session_id == record.login_session)
        ]
        desktop_ends = self.journal.desktop_ends(record.boot_id)
        events += [_Event(t, t, "logout") for t in desktop_ends]
        if not same_boot:
            restart = self.journal.restart_time(record.boot_id)
            if restart is not None:
                events.append(_Event(restart, self.journal.last_entry(record.boot_id) or restart, "restart"))
            elif desktop_ends:
                last = desktop_ends[-1]
                events.append(_Event(last, self.journal.last_entry(record.boot_id) or last, "restart"))
        return events

    def drop(self, session_ids: Iterable[str]) -> Group:
        with self._locked() as group:
            for session_id in session_ids:
                group.pop(session_id, None)
        return self.load()

    def drop_all(self) -> Group:
        with self._locked() as group:
            group.clear()
        return self.load()
