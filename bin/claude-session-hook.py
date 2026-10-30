"""Claude Code SessionStart/SessionEnd hook for Windows: notes which sessions
are open, so after a restart or logoff claude-launcher can list the ones that
were. The same record as bin/claude-session-hook (bash, for Linux and WSL);
see that script and restart.py.

Claude runs it through a .cmd that finds Python (platforms/windows/setup.py):
cmd /d /c ...\claude-session-hook.cmd start|end, which runs
python -I claude-session-hook.py start|end. It runs on every session
start and end, so it imports only what it needs, and always exits 0.
"""

import contextlib
import json
import os
import re
import sys
import time

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHOSEN_ENDS = ("prompt_input_exit", "clear", "resume", "logout")  # the record goes
_BOOT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"


def windows_boot_id() -> str:
    """Windows counts its boots; as 32 hex digits, it's a boot ID like Linux's."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BOOT_KEY) as key:
        return format(int(winreg.QueryValueEx(key, "BootId")[0]), "032x")


def run(event, payload, env, boot_id=windows_boot_id, now=time.time, parent=os.getppid):
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
    # Claude says its own pid. The parent is cmd (or py), not Claude: a last resort.
    pid = int(env["CLAUDE_PID"]) if env.get("CLAUDE_PID", "").isdigit() else parent()
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    folder = os.path.join(
        env.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "state", "claude-launcher", "open"
    )
    boot = boot_id()
    record = os.path.join(folder, f"{boot}_{pid}")
    fresh = {"session_id": session_id, "pid": pid, "boot_id": boot, "login_session": "", "started_at": now()}

    if event == "end" and reason in CHOSEN_ENDS:
        with contextlib.suppress(OSError):
            os.remove(record)
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
    os.replace(record + ".tmp", record)


def main():
    with contextlib.suppress(Exception):  # a hook must never get in Claude's way
        run(sys.argv[1] if len(sys.argv) > 1 else "", sys.stdin.read(), os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
