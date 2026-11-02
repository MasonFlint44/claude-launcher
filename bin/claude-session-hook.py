"""Claude Code SessionStart/SessionEnd hook for Windows: notes which sessions
are open, so after a restart or logoff claude-launcher can list the ones that
were. The same record as bin/claude-session-hook (bash, for Linux and WSL);
see that script and restart.py.

Claude runs it through a .cmd in the launcher's data directory that finds
Python (platforms/windows/setup.py), which runs a small entry script beside
it, which runs this. It runs on every session start and end, so it imports
only what it needs, and always exits 0.
"""

import contextlib
import json
import os
import re
import sys
import time

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHOSEN_ENDS = ("prompt_input_exit", "clear", "resume", "logout")  # the record goes
# Another process (the picker reading a record, a virus scanner) can have a
# file open for a moment, and Windows then refuses to replace or delete it.
_TRIES, _PAUSE = 5, 0.01
_BOOT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
# A process that began this long after a record's Claude started isn't that
# Claude, but a new one given its pid: the same margin as the launcher's
# (platforms/windows/host.py).
_REUSED_AFTER = 10


def windows_boot_id() -> str:
    """Windows counts its boots; as 32 hex digits, it's a boot ID like Linux's."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BOOT_KEY) as key:
        return format(int(winreg.QueryValueEx(key, "BootId")[0]), "032x")


def process_created(pid):
    """When process `pid` began, as a Unix time, or None if it can't be told.
    Asked only when another session's record is under the pid, so most runs
    never load ctypes."""
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32")  # its own, so no one else's types change
    except (AttributeError, OSError):  # not Windows
        return None
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
    finally:
        kernel32.CloseHandle(handle)
    # FILETIME counts 100 ns from 1601.
    return ((times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) - 116444736000000000) / 1e7


def _retry(action, *args):
    for i in range(_TRIES):
        try:
            return action(*args)
        except PermissionError:
            if i == _TRIES - 1:
                raise
            time.sleep(_PAUSE)


def run(event, payload, env, boot_id=windows_boot_id, now=time.time, created=process_created):
    # Only interactive terminal sessions: not `claude -p` or the SDK (sdk-cli).
    if env.get("CLAUDE_CODE_ENTRYPOINT") != "cli" or event not in ("start", "end"):
        return
    try:
        data = json.loads(payload)
    except ValueError:
        return
    session_id = str(data.get("session_id", ""))
    if not UUID.fullmatch(session_id):
        return
    reason = data.get("reason") if isinstance(data.get("reason"), str) else "other"
    # Claude says its own pid. Without it there's no record: the hook's parent
    # is cmd or py, which has gone by the time the picker looks, so the record
    # would be taken for a closed Claude while this one still runs.
    if not env.get("CLAUDE_PID", "").isdigit():
        return
    pid = int(env["CLAUDE_PID"])
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    folder = os.path.join(
        env.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "state", "claude-launcher", "open"
    )
    boot = boot_id()
    record = os.path.join(folder, f"{boot}_{pid}")
    fresh = {"session_id": session_id, "pid": pid, "boot_id": boot, "login_session": "", "started_at": now()}

    try:
        with open(record, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = None
    old_session = str(old.get("session_id", "")) if isinstance(old, dict) else ""
    # Another session's record that this same Claude wrote (it had begun by
    # that record's start): its session before this one, which it ended by
    # choice (/clear, /resume). Claude waits only a moment for the end hook
    # before starting the next session, and then kills it, so that end may
    # not have got to delete it.
    earlier = False
    if UUID.fullmatch(old_session) and old_session != session_id:
        old_start = old.get("started_at")
        if isinstance(old_start, (int, float)) and not isinstance(old_start, bool):
            began = created(pid)
            earlier = began is not None and began <= old_start + _REUSED_AFTER
    if event == "end" and reason in CHOSEN_ENDS:
        # Only this session's record, or this Claude's earlier one: another's
        # (no start was seen for this one) is left for the launcher to sort.
        if old_session == session_id or earlier:
            with contextlib.suppress(OSError):
                _retry(os.remove, record)
        return
    # Another session's record under this pid: this Claude's earlier one
    # goes. Anything else is a Claude killed with no end (a logoff or Fast
    # Startup, say) whose pid came back before the launcher sorted it, or
    # this Claude's earlier one when its start couldn't be read. Keep it
    # aside, under its session's ID, for the launcher to find. Windows reuses
    # pids quickly.
    if earlier:
        with contextlib.suppress(OSError):
            _retry(os.remove, record)
    elif UUID.fullmatch(old_session) and old_session != session_id:
        with contextlib.suppress(OSError):
            _retry(os.replace, record, f"{record}_{old_session}")
    if event == "start":
        new = fresh
    else:
        new = old if old_session == session_id else fresh
        new = {**new, "end_reason": re.sub(r"[^a-z_]", "", reason) or "other", "ended_at": now()}
    os.makedirs(folder, exist_ok=True)
    with open(record + ".tmp", "w", encoding="utf-8", newline="") as f:  # atomically, as the bash hook does
        f.write(json.dumps(new, separators=(",", ":")) + "\n")
    _retry(os.replace, record + ".tmp", record)


def main():
    with contextlib.suppress(Exception):  # a hook must never get in Claude's way
        # Bytes, as UTF-8: Windows' Python would decode a pipe in the ANSI
        # code page, and -I ignores PYTHONIOENCODING.
        payload = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        run(sys.argv[1] if len(sys.argv) > 1 else "", payload, os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
