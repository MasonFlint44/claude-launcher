"""Restarts and logoffs from Windows' System event log, which any user can
read (the Security log needs an administrator). Shared by native Windows and
WSL, which runs the same wevtutil.exe through interop.

- Restart (or power off): from the boot's last "initiated the restart"
  (User32 1074) to "the operating system is shutting down" (Kernel-General
  13). After a crash or power cut there's neither: the restart starts at the
  boot's last logged event.
- Shutting down with Fast Startup: "power off" (1074) and then, instead of
  shutting down, hibernating (Kernel-Power 42, "entering sleep"). The boot
  goes on after it (no Kernel-General 13 or 12, and the same BootId), but
  Claude was closed as at a shutdown, so it counts as one, from the 1074 to
  the 42. Hibernating on its own logs no 1074, and Claude keeps running.
- Logoff: Winlogon's logoff notification (7002) for this user. Windows logs
  one at a restart too; the restart wins, as on Linux.

A boot runs from one Kernel-General 12 ("the operating system started") to
the next.
"""

from __future__ import annotations

import calendar
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..base import Event

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
KERNEL = "Microsoft-Windows-Kernel-General"
BOOTED, SHUTTING_DOWN = 12, 13
RESTART_BEGUN = ("User32", 1074)
LOGOFF = ("Microsoft-Windows-Winlogon", 7002)
HIBERNATING = ("Microsoft-Windows-Kernel-Power", 42)
# Fast Startup hibernates about a second after its 1074 (measured in a VM).
_FAST_STARTUP_GAP = 120
# How far before a Claude started to look: a restart can't have closed it
# before then, but its boot's last events can come just before a record.
_BEFORE = 60
_TIME = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z?")


@dataclass(frozen=True)
class LogEvent:
    provider: str
    id: int
    at: float
    data: dict[str, str] = field(default_factory=dict)


def parse_time(text: str) -> float:
    """Event log times are UTC, to 100 ns: 2026-10-29T05:31:23.5887514Z."""
    m = _TIME.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not an event time: {text!r}")
    y, mo, d, h, mi, s, frac = m.groups()
    whole = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, 0))
    return whole + (float("0." + frac) if frac else 0.0)


def format_time(t: float) -> str:
    whole = int(t)
    tm = time.gmtime(whole)
    return time.strftime("%Y-%m-%dT%H:%M:%S", tm) + f".{int((t - whole) * 1000):03d}Z"


def parse_events(xml: str) -> list[LogEvent]:
    """wevtutil's /f:xml output: <Event> elements one after another."""
    root = ET.fromstring(f"<Events>{xml}</Events>")
    out = []
    for e in root.iter(f"{_NS}Event"):
        system = e.find(f"{_NS}System")
        if system is None:
            continue
        provider = system.find(f"{_NS}Provider")
        event_id = system.find(f"{_NS}EventID")
        created = system.find(f"{_NS}TimeCreated")
        if provider is None or event_id is None or created is None:
            continue
        data = {d.get("Name", ""): (d.text or "") for d in e.iter(f"{_NS}Data")}
        out.append(
            LogEvent(provider.get("Name", ""), int(event_id.text or 0), parse_time(created.get("SystemTime", "")), data)
        )
    return out


def _wevtutil(args: Sequence[str]) -> str | None:
    try:
        done = subprocess.run(["wevtutil.exe", *args], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace")


def _one(provider: str, *ids: int) -> str:
    which = " or ".join(f"EventID={i}" for i in ids)
    return f"(Provider[@Name='{provider}'] and ({which}))"


class EventLog:
    """The System log, through wevtutil. Each query runs once per picker run."""

    def __init__(self, run: Callable[[Sequence[str]], str | None] = _wevtutil):
        self._run = run
        self._cache: dict[tuple, list[LogEvent] | None] = {}

    def query(self, xpath: str, newest_first: bool = False, count: int | None = None) -> list[LogEvent] | None:
        key = (xpath, newest_first, count)
        if key not in self._cache:
            args = ["qe", "System", f"/q:{xpath}", "/f:xml"]
            if newest_first:
                args.append("/rd:true")
            if count is not None:
                args.append(f"/c:{count}")
            out = self._run(args)
            try:
                self._cache[key] = None if out is None else parse_events(out)
            except (ET.ParseError, ValueError):
                self._cache[key] = None
        return self._cache[key]

    def since(self, t: float) -> list[LogEvent] | None:
        """The boots, restarts and logoffs from `t` on, oldest first."""
        kinds = " or ".join(
            [_one(KERNEL, BOOTED, SHUTTING_DOWN), _one(*RESTART_BEGUN), _one(*LOGOFF), _one(*HIBERNATING)]
        )
        return self.query(f"*[System[({kinds}) and TimeCreated[@SystemTime>='{format_time(t)}']]]")

    def last_before(self, t: float) -> LogEvent | None:
        """The last event of any kind before `t`: where a boot that crashed ended."""
        found = self.query(f"*[System[TimeCreated[@SystemTime<'{format_time(t)}']]]", newest_first=True, count=1)
        return found[0] if found else None


def _fast_startups(events: list[LogEvent]) -> list[tuple[float, float]]:
    """Fast Startup shutdowns in a boot's events (oldest first): each 1074
    followed by hibernating, with no shutting down between, as (1074, 42)."""
    out = []
    for i, e in enumerate(events):
        if (e.provider, e.id) != RESTART_BEGUN:
            continue
        for later in events[i + 1 :]:
            if later.at - e.at > _FAST_STARTUP_GAP or (later.provider, later.id) == (KERNEL, SHUTTING_DOWN):
                break
            if (later.provider, later.id) == HIBERNATING:
                out.append((e.at, later.at))
                break
    return out


def user_sid() -> str | None:
    """The Windows user's SID, which logoffs are logged under. whoami.exe, so
    WSL can ask too."""
    try:
        done = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rb'"(S-1-[0-9-]+)"', done.stdout)
    return m.group(1).decode() if m else None


class WindowsEvents:
    """Native Windows' EventSource: a record's boot is Windows' own."""

    def __init__(self, log: EventLog, sid: Callable[[], str | None] = user_sid):
        self.log = log
        self._sid = sid

    def events(self, record, same_boot: bool) -> list[Event] | None:
        found = self.log.since(record.started - _BEFORE)
        if found is None:
            return None
        # The record's boot ends where the next one starts. By time, not by
        # the log's order: a clock set wrong, then right, leaves events from
        # "later" earlier in the log.
        found = sorted(found, key=lambda e: e.at)
        boots = [e.at for e in found if (e.provider, e.id) == (KERNEL, BOOTED) and e.at > record.started]
        next_boot = boots[0] if boots else None
        in_boot = [e for e in found if next_boot is None or e.at < next_boot]
        sid = self._sid()
        events = [
            Event(e.at, e.at, "logout")
            for e in in_boot
            if (e.provider, e.id) == LOGOFF and sid is not None and e.data.get("UserSid") == sid
        ]
        fast = _fast_startups(in_boot)
        events += [Event(begun, slept, "restart") for begun, slept in fast]
        if not same_boot and next_boot is not None:
            events.append(self._restart(in_boot, next_boot, {begun for begun, _ in fast}))
        return events

    def _restart(self, in_boot: list[LogEvent], next_boot: float, fast: set[float]) -> Event:
        begun = [e.at for e in in_boot if (e.provider, e.id) == RESTART_BEGUN and e.at not in fast]
        ended = [e.at for e in in_boot if (e.provider, e.id) == (KERNEL, SHUTTING_DOWN)]
        if begun:
            start = begun[-1]
        else:  # a crash or power cut: the boot's last logged event
            last = self.log.last_before(next_boot)
            start = last.at if last else (ended[-1] if ended else next_boot)
        end = max([start, *(t for t in ended if t >= start)])
        return Event(start, end, "restart")
