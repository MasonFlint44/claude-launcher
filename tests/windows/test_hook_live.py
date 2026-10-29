"""Windows' hook run as Claude runs it: python.exe -I, no shell."""

import json
import subprocess
import sys

from claude_launcher.paths import REPO_DIR
from claude_launcher.platforms.windows.host import WindowsHost
from claude_launcher.restart import Record

SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"


def test_records_this_boot(tmp_path):
    env = {
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CONFIG_DIR": str(tmp_path),
        "CLAUDE_PID": "4242",
        "SYSTEMROOT": "C:\\Windows",
    }
    hook = REPO_DIR / "bin" / "claude-session-hook.py"
    payload = json.dumps({"session_id": SID, "hook_event_name": "SessionStart", "source": "startup"})
    subprocess.run([sys.executable, "-I", str(hook), "start"], input=payload.encode(), env=env, check=True, timeout=10)
    [path] = (tmp_path / "state" / "claude-launcher" / "open").iterdir()
    record = Record.load(path)
    assert record.boot_id == WindowsHost().boot_id() and record.pid == 4242 and record.session_id == SID
