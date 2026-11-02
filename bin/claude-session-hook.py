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


def windows_boot_id() -> str:
    """Windows counts its boots; as 32 hex digits, it's a boot ID like Linux's."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BOOT_KEY) as key:
        return format(int(winreg.QueryValueEx(key, "BootId")[0]), "032x")


def _retry(action, *args):
    for i in range(_TRIES):
        try:
            return action(*args)
        except PermissionError:
            if i == _TRIES - 1:
                raise
            time.sleep(_PAUSE)


def run(event, payload, env, boot_id=windows_boot_id, now=time.time):
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

    if event == "end" and reason in CHOSEN_ENDS:
        with contextlib.suppress(OSError):
            _retry(os.remove, record)
        return
    if event == "start":
        new = fresh
    else:
        try:
            with open(record, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        # Another session's record (no start was seen for this one): start afresh.
        new = old if isinstance(old, dict) and old.get("session_id") == session_id else fresh
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
