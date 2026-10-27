"""When boots and login sessions ended, from the systemd journal."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# logind's "System is rebooting." / "System is powering down." message. It's
# logged before GNOME closes the windows, so it marks when the restart began.
SHUTDOWN_MESSAGE_ID = "98268866d1d54a499c4e98921d93bc40"
# A desktop session ending, as your own systemd user manager logs it: GNOME
# (since 3.34) and KDE Plasma (since 5.25) run the session as
# graphical-session.target, and stop it as the session ends, just before the
# windows close. This is in your user journal, which needs no special group,
# and the unit-stopped message ID has been stable for many systemd releases.
UNIT_STOPPED_MESSAGE_ID = "9d1aaa27d60140bd96365438aad20286"
GRAPHICAL_SESSION_UNIT = "graphical-session.target"

# A logout, in logind's messages: "New session ..." gives the session's type
# (its TYPE field only from systemd 258; older releases leave it out).
# "Session N logged out." (no message ID; logged from session_stop_scope) comes
# when the session's leader exits, about half a second after GNOME has closed
# the windows. "Removed session N." follows once the session's processes have
# gone, which can take seconds longer; it stands in if the first is missing.
SESSION_NEW_MESSAGE_ID = "8d45620c1a4348dbb17410da57c60c66"
SESSION_REMOVED_MESSAGE_ID = "3354939424b4456d9802ca8333ed424a"
SESSION_LOGGED_OUT_FUNC = "session_stop_scope"
GRAPHICAL_TYPES = {"wayland", "x11", "mir"}

_BOOT_ROW = re.compile(r"^\s*-?\d+\s+([0-9a-f]{32})\s", re.MULTILINE)

Runner = Callable[[Sequence[str]], str | None]


def run_journalctl(args: Sequence[str]) -> str | None:
    """journalctl's output, or None if it can't be run or fails."""
    try:
        done = subprocess.run(["journalctl", "--no-pager", "-q", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


@dataclass(frozen=True)
class Logout:
    at: float  # when the session logged out
    removed: float  # when logind removed it (at, if the journal doesn't say)
    session_id: str
    graphical: bool  # a desktop session, whose end closes every terminal window


def normalize_boot_id(boot_id: str) -> str:
    """Boot IDs as the journal writes them: /proc has dashes, journalctl doesn't."""
    return boot_id.replace("-", "").lower()


class Journal:
    def __init__(self, run: Runner = run_journalctl):
        self._run = run
        self._last_entries: dict[str, float] | None | bool = False  # False: not asked yet
        self._restarts: dict[str, float | None] = {}
        self._logouts: dict[tuple[str, str], list[Logout]] = {}
        self._desktop_ends: dict[str, list[float]] = {}

    def _boots(self) -> dict[str, float | None] | None:
        """{boot id: time of its last journal entry}, or None if the journal
        can't be read. A time is None until last_entry() looks it up."""
        if self._last_entries is False:
            self._last_entries = None
            out = self._run(["--list-boots", "-o", "json"])
            if out is not None:
                try:
                    boots = json.loads(out)
                    self._last_entries = {
                        normalize_boot_id(b["boot_id"]): b["last_entry"] / 1e6
                        for b in boots
                        if isinstance(b, dict) and "boot_id" in b and "last_entry" in b
                    }
                except (ValueError, TypeError, KeyError):
                    # Before systemd 251, --list-boots ignores -o and prints
                    # "IDX BOOT_ID FIRST—LAST" in local time. Take the IDs; the
                    # times come from each boot's last entry when needed.
                    ids = _BOOT_ROW.findall(out)
                    self._last_entries = dict.fromkeys(ids) if ids else None
        return self._last_entries  # type: ignore[return-value]

    @property
    def available(self) -> bool:
        return self._boots() is not None

    def last_entry(self, boot_id: str) -> float | None:
        """When boot `boot_id` last logged anything, or None if the journal doesn't have it."""
        boots = self._boots()
        boot_id = normalize_boot_id(boot_id)
        if not boots or boot_id not in boots:
            return None
        if boots[boot_id] is None:
            out = self._run(["-b", boot_id, "-n", "1", "-o", "json", "--output-fields=__REALTIME_TIMESTAMP"])
            try:
                boots[boot_id] = int(json.loads(out or "")["__REALTIME_TIMESTAMP"]) / 1e6
            except (ValueError, KeyError, TypeError):
                return None
        return boots[boot_id]

    def restart_time(self, boot_id: str) -> float | None:
        """When the restart that ended boot `boot_id` began: logind's shutdown
        message, else the boot's last journal entry (after a crash or power
        cut, or when only your own journal can be read). None if the journal
        doesn't have that boot."""
        boot_id = normalize_boot_id(boot_id)
        if boot_id in self._restarts:
            return self._restarts[boot_id]
        last_entry = self.last_entry(boot_id)
        result = None
        if last_entry is not None:
            result = self._shutdown_message_time(boot_id) or last_entry
        self._restarts[boot_id] = result
        return result

    def desktop_ends(self, boot_id: str) -> list[float]:
        """When your desktop sessions ended during boot `boot_id`, oldest first."""
        boot_id = normalize_boot_id(boot_id)
        if boot_id in self._desktop_ends:
            return self._desktop_ends[boot_id]
        out = self._run(
            [
                "--user",
                "-b",
                boot_id,
                "-o",
                "json",
                "--output-fields=JOB_TYPE,JOB_RESULT",
                f"USER_UNIT={GRAPHICAL_SESSION_UNIT}",
                f"MESSAGE_ID={UNIT_STOPPED_MESSAGE_ID}",
            ]
        )
        ends = []
        for line in (out or "").splitlines():
            try:
                entry = json.loads(line)
                if entry.get("JOB_TYPE", "stop") == "stop":
                    ends.append(int(entry["__REALTIME_TIMESTAMP"]) / 1e6)
            except (ValueError, KeyError, TypeError):
                continue
        self._desktop_ends[boot_id] = sorted(ends)
        return self._desktop_ends[boot_id]

    def logouts(self, boot_id: str, user: str) -> list[Logout] | None:
        """`user`'s login sessions that ended during boot `boot_id`, oldest
        first, or None if the journal can't be read."""
        boot_id = normalize_boot_id(boot_id)
        key = (boot_id, user)
        if key in self._logouts:
            return self._logouts[key]
        if not self.available:
            return None
        out = self._run(
            [
                "-b",
                boot_id,
                "-o",
                "json",
                "--output-fields=MESSAGE_ID,SESSION_ID,TYPE,CODE_FUNC",
                f"MESSAGE_ID={SESSION_NEW_MESSAGE_ID}",
                f"MESSAGE_ID={SESSION_REMOVED_MESSAGE_ID}",
                f"USER_ID={user}",
                "+",
                f"CODE_FUNC={SESSION_LOGGED_OUT_FUNC}",
                f"USER_ID={user}",
            ]
        )
        types: dict[str, str] = {}
        logged_out: dict[str, float] = {}
        removed: dict[str, float] = {}
        for line in (out or "").splitlines():
            try:
                entry = json.loads(line)
                at = int(entry["__REALTIME_TIMESTAMP"]) / 1e6
                session = str(entry["SESSION_ID"])
            except (ValueError, KeyError, TypeError):
                continue
            if entry.get("MESSAGE_ID") == SESSION_NEW_MESSAGE_ID:
                types[session] = str(entry.get("TYPE", ""))
            elif entry.get("MESSAGE_ID") == SESSION_REMOVED_MESSAGE_ID:
                removed.setdefault(session, at)
            elif entry.get("CODE_FUNC") == SESSION_LOGGED_OUT_FUNC:
                logged_out.setdefault(session, at)
        ends = []
        for session in logged_out.keys() | removed.keys():
            at = logged_out.get(session, removed.get(session))
            ends.append(Logout(at, removed.get(session, at), session, types.get(session, "") in GRAPHICAL_TYPES))
        self._logouts[key] = sorted(ends, key=lambda e: e.at)
        return self._logouts[key]

    def _shutdown_message_time(self, boot_id: str) -> float | None:
        out = self._run(
            ["-b", boot_id, f"MESSAGE_ID={SHUTDOWN_MESSAGE_ID}", "-o", "json", "--output-fields=__REALTIME_TIMESTAMP"]
        )
        if not out:
            return None
        times = []
        for line in out.splitlines():
            try:
                times.append(int(json.loads(line)["__REALTIME_TIMESTAMP"]) / 1e6)
            except (ValueError, KeyError, TypeError):
                continue
        return min(times) if times else None
