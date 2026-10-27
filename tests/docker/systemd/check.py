"""Restart and logout detection under real systemd, run as root inside the
container (systemd is PID 1). `check.py before` sets up the sessions, logs
them out and checks the ↻ group; run.sh then reboots the container (a new
boot ID each start, see boot.sh) and `check.py after` checks the restart.

Each result is one line: RESULT <name> <PASS|FAIL|SKIP|INFO> <detail>.
"""

from __future__ import annotations

import grp
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, "/src/src")

from claude_launcher import restart as restart_mod  # noqa: E402

UID = 1000
HOME = Path("/home/tester")
CLAUDE = HOME / ".claude"
OPEN = CLAUDE / "state" / "claude-launcher" / "open"
STATE = CLAUDE / "state" / "claude-launcher"
SAVED = Path("/var/lib/check")  # what `before` leaves for `after`
JOURNAL_GROUP = "systemd-journal"
failed = False


def result(name: str, status: str, detail: str = "") -> None:
    global failed
    failed |= status == "FAIL"
    print(f"RESULT {name} {status} {detail}".rstrip(), flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    result(name, "PASS" if ok else "FAIL", detail)


def wait(what: str, cond, timeout: float = 20) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    print(f"timed out waiting for {what}", flush=True)
    return False


def sh(*argv: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, **kw)


def as_tester(argv: list[str], group: bool) -> subprocess.CompletedProcess:
    """argv as tester, in the journal group or in no group at all."""
    groups = [f"--groups={grp.getgrnam(JOURNAL_GROUP).gr_gid}"] if group else ["--clear-groups"]
    env = [
        "env",
        "-i",
        f"HOME={HOME}",
        "USER=tester",
        "LOGNAME=tester",
        "LANG=C.UTF-8",
        f"XDG_RUNTIME_DIR=/run/user/{UID}",
        "PATH=/usr/local/bin:/usr/bin:/bin",
    ]
    return sh("setpriv", f"--reuid={UID}", f"--regid={UID}", *groups, *env, *argv)


def systemd_version() -> int:
    return int(sh("systemctl", "--version").stdout.split()[1].split(".")[0].split("~")[0])


# --- the launcher's side ------------------------------------------------------

SORT = """
import json, sys
sys.path.insert(0, "/src/src")
from claude_launcher.paths import Paths
from claude_launcher.restart import RestartState
group = RestartState(Paths.from_env()).sort()
print(json.dumps({k: [c.at, c.why] for k, c in group.sessions.items()}))
"""


def sort(group: bool) -> dict[str, list] | None:
    """The ↻ group after restart.sort, as tester, without changing the state
    (each call starts from the same records)."""
    backup = Path("/tmp/state-backup")
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(STATE, backup, symlinks=True)
    try:
        done = as_tester(["python3", "-c", SORT], group)
        if done.returncode != 0:
            print(done.stderr, flush=True)
            return None
        return json.loads(done.stdout)
    finally:
        shutil.rmtree(STATE)
        shutil.copytree(backup, STATE, symlinks=True)
        sh("chown", "-R", f"{UID}:{UID}", str(STATE))


JOURNAL_PROBE = """
import json, sys
sys.path.insert(0, "/src/src")
from claude_launcher.platforms.linux.journal import Journal
from claude_launcher.restart import normalize_boot_id
j = Journal()
boot = normalize_boot_id(sys.argv[1])
logouts = j.logouts(boot, "tester")
print(json.dumps({
    "available": j.available,
    "boots": len(j._boots() or {}),
    "desktop_ends": len(j.desktop_ends(boot)),
    "logouts": None if logouts is None else [[e.session_id, e.graphical] for e in logouts],
    "restart": j.restart_time(boot),
    "shutdown_message": j._shutdown_message_time(boot),
}))
"""


def probe(boot: str, group: bool) -> dict:
    done = as_tester(["python3", "-c", JOURNAL_PROBE, boot], group)
    if done.returncode != 0:
        print(done.stderr, flush=True)
        return {}
    return json.loads(done.stdout)


# --- sessions -----------------------------------------------------------------


def records() -> dict[str, dict]:
    """The hook's records, by session ID."""
    out = {}
    for path in OPEN.glob("*_*") if OPEN.exists() else []:
        if path.name.endswith(".tmp"):
            continue
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        out[data["session_id"]] = {**data, "_file": path.name}
    return out


def new_session_id() -> str:
    sid = str(uuid.uuid4())
    log = CLAUDE / "projects" / "-home-tester" / f"{sid}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{}\n")
    sh("chown", "-R", f"{UID}:{UID}", str(CLAUDE))
    return sid


def start_session(name: str, kind: str) -> str:
    """A login session of `kind` (see session.py) with a Claude in it; returns
    its session ID once the hook has recorded the Claude's start."""
    sid = new_session_id()
    Path(f"/tmp/logout-{name}").unlink(missing_ok=True)
    session_type = {"desktop": "wayland", "bare-gui": "wayland", "tty": "tty"}[kind]
    done = sh(
        "systemd-run",
        "-q",
        f"--unit=login-{name}",
        "-p",
        "PAMName=claude-test",
        "-p",
        f"User={UID}",
        "-E",
        f"XDG_SESSION_TYPE={session_type}",
        "-E",
        "XDG_SESSION_CLASS=user",
        "-E",
        f"HOME={HOME}",
        "/usr/bin/python3",
        "/check/session.py",
        name,
        kind,
        sid,
    )
    if done.returncode != 0:
        print(done.stderr, flush=True)
    if not wait(f"{name}'s Claude to start", lambda: sid in records()):
        # Why: the session's and the user manager's state, for the CI log.
        for argv in (
            ("systemctl", "status", "--no-pager", f"login-{name}", f"user@{UID}.service"),
            ("journalctl", "-b", "--no-pager", "-n", "40", "-u", f"login-{name}", "-u", f"user@{UID}.service"),
        ):
            print(sh(*argv).stdout, flush=True)
    return sid


def log_out(name: str, sid: str) -> None:
    Path(f"/tmp/logout-{name}").touch()
    wait(f"{name}'s Claude to end", lambda: "ended_at" in records().get(sid, {}))
    wait(f"{name}'s session to go", lambda: sh("systemctl", "is-active", f"login-{name}").stdout.strip() != "active")


def logind_sessions() -> list[str]:
    return sh("loginctl", "list-sessions", "--no-legend").stdout.split("\n")


def fake_record(sid: str, ended: float) -> None:
    """A Claude whose record says it ended at `ended`, and isn't running."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    pid = 999999
    data = {
        "session_id": sid,
        "pid": pid,
        "boot_id": boot,
        "login_session": "",
        "started_at": ended - 600,
        "end_reason": "other",
        "ended_at": ended,
    }
    path = OPEN / f"{boot}_{pid - len(list(OPEN.iterdir()))}"
    path.write_text(json.dumps(data) + "\n")
    os.chown(path, UID, UID)


# --- before -------------------------------------------------------------------


def before() -> None:
    version = systemd_version()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    result("systemd", "INFO", str(version))
    result("journal-dir-from-package", "INFO", Path("/etc/journal-dir-from-package").read_text().strip())

    # Every journalctl call journal.py makes, as the launcher would make it.
    boots_json = sh("journalctl", "--list-boots", "-o", "json", "-q", "--no-pager")
    try:
        parsed = json.loads(boots_json.stdout)
        ok = all("boot_id" in b and "last_entry" in b for b in parsed)
    except ValueError:
        ok = False
    # JSON from systemd 251; before that, text (journal.py reads either).
    result("list-boots-format", "INFO", "json" if ok else "text")

    # tester's systemd user manager, and a desktop for it.
    unit_dir = HOME / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy("/check/fake-desktop.target", unit_dir)
    sh("chown", "-R", f"{UID}:{UID}", str(HOME))

    desk = start_session("desk", "desktop")
    gui = start_session("gui", "bare-gui")
    tty = start_session("tty", "tty")
    restart = start_session("reboot", "desktop")  # left open for the restart
    for name, sid in (("desk", desk), ("gui", gui), ("tty", tty), ("reboot", restart)):
        check(f"hook-start-{name}", sid in records(), records().get(sid, {}).get("_file", "no record"))
    rec = records().get(tty, {})
    check(
        "hook-login-session",
        bool(rec.get("login_session")),
        f"tty Claude's login session: {rec.get('login_session')!r}",
    )
    # logind names a session after its audit session, which the hook reads,
    # except in a container on systemd 257 and later, where it says c1, c2...
    info = sh("loginctl", "show-session", rec.get("login_session") or "none", "-p", "Type", "--value")
    audit_ids = info.returncode == 0
    result("logind-uses-audit-ids", "INFO", "yes" if audit_ids else "no")
    desk_rec = records().get(desk, {})
    # Apps run by the user manager, as a desktop's are: their audit session
    # is the user manager's own, not a logind session that logs out.
    result("desktop-app-login-session", "INFO", repr(desk_rec.get("login_session")))

    # A logout counts for every Claude that ended near it (LEAD before to
    # TAIL after), so each one comes well after the one before.
    gap = restart_mod.LEAD + restart_mod.TAIL + 5
    log_out("gui", gui)
    time.sleep(gap)
    log_out("tty", tty)
    time.sleep(gap)
    log_out("desk", desk)

    first, ended = records()[gui]["ended_at"], records()[desk]["ended_at"]
    early, late = new_session_id(), new_session_id()
    fake_record(early, first - 100)
    fake_record(late, ended + 100)
    sh("chown", "-R", f"{UID}:{UID}", str(CLAUDE))

    # The user journal is written once journald flushes; wait for the stop.
    def stopped() -> bool:
        return probe(boot, group=False).get("desktop_ends", 0) > 0

    check("user-journal-desktop-end", wait("graphical-session.target's stop in tester's journal", stopped, 30))
    has_type = version >= 258
    for group in (False, True):
        tag = "group" if group else "nogroup"
        p = probe(boot, group)
        result(f"journal-{tag}", "INFO", json.dumps(p))
        check(f"journal-available-{tag}", bool(p.get("available")))
        g = sort(group)
        if g is None:
            check(f"sort-{tag}", False, "restart.sort failed")
            continue
        why = {sid: (g.get(sid) or [None, None])[1] for sid in (desk, gui, tty, early, late)}
        check(f"desktop-logout-{tag}", why[desk] == "logout", f"got {why[desk]}")
        check(f"ended-before-logout-{tag}", why[early] is None, f"got {why[early]}")
        check(f"ended-after-logout-{tag}", why[late] is None, f"got {why[late]}")
        # logind's messages are in the system journal: only with the group.
        if audit_ids or not group:
            check(f"ssh-logout-{tag}", why[tty] == ("logout" if group else None), f"got {why[tty]}")
        else:
            result(f"ssh-logout-{tag}", "SKIP", "logind's session IDs aren't the audit IDs here")
        # A desktop without graphical-session.target: only logind's TYPE (258+) shows it.
        expect = "logout" if group and has_type else None
        check(f"typed-logout-{tag}", why[gui] == expect, f"got {why[gui]}, expected {expect}")

    # Reboot only once the desktop's logout is too long ago to count, so only
    # the restart can account for the Claude left open.
    time.sleep(max(0.0, ended + restart_mod.LEAD + restart_mod.TAIL + 5 - time.time()))
    SAVED.mkdir(parents=True, exist_ok=True)
    (SAVED / "before.json").write_text(json.dumps({"boot": boot, "restart": restart}))


# --- after --------------------------------------------------------------------


def after() -> None:
    saved = json.loads((SAVED / "before.json").read_text())
    old_boot, sid = saved["boot"], saved["restart"]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    check("new-boot-id", boot != old_boot, f"{old_boot[:8]} -> {boot[:8]}")
    rec = records().get(sid, {})
    check("hook-end-at-shutdown", "ended_at" in rec, "the Claude open at the restart recorded its end")
    for group in (False, True):
        tag = "group" if group else "nogroup"
        p = probe(old_boot, group)
        result(f"journal-old-boot-{tag}", "INFO", json.dumps(p))
        if group:
            check("shutdown-message", p.get("shutdown_message") is not None, 'logind\'s "System is rebooting."')
        g = sort(group)
        if g is None:
            check(f"sort-after-{tag}", False, "restart.sort failed")
            continue
        got = g.get(sid)
        check(f"restart-{tag}", bool(got) and got[1] == "restart", f"got {got}")
        if group and got and p.get("shutdown_message"):
            check("restart-time-is-shutdown-message", abs(got[0] - p["shutdown_message"]) < 0.01)


if __name__ == "__main__":
    {"before": before, "after": after}[sys.argv[1]]()
    sys.exit(1 if failed else 0)
