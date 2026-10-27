"""A stand-in for Claude, run as /usr/local/bin/claude (a link to python3), so
its command line is what platforms.linux.host.is_claude and the hook look for. It runs the
real claude-session-hook as Claude Code would: `start` when it begins, and
`end` (reason "other") when its terminal closes (SIGHUP) or it's stopped
(SIGTERM). usage: claude fake_claude.py SESSION_ID"""

import json
import os
import signal
import subprocess
import sys
import time

HOOK = "/src/bin/claude-session-hook"
session_id = sys.argv[1]
env = {**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"}


def hook(event: str, **extra) -> None:
    data = json.dumps({"session_id": session_id, "hook_event_name": event, **extra})
    subprocess.run(
        ["/bin/bash", "-c", f"{HOOK} {'start' if event == 'SessionStart' else 'end'}"],
        input=data,
        text=True,
        env=env,
        check=False,
    )


def end(*_):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    hook("SessionEnd", reason="other")
    os._exit(0)


signal.signal(signal.SIGHUP, end)
signal.signal(signal.SIGTERM, end)
hook("SessionStart", source="startup")
while True:
    time.sleep(60)
