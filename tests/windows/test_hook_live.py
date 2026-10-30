"""Windows' hook run as Claude runs it: cmd, the hook's .cmd (which finds
Python), then the hook. From a directory with a space and brackets, which
cmd /c is fussy about."""

import json
import os
import subprocess

from claude_launcher.platforms.windows.host import WindowsHost
from claude_launcher.platforms.windows.setup import WindowsSetup
from claude_launcher.restart import Record

SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"


def test_records_this_boot(tmp_path):
    home = tmp_path / "Jo Smith (work)"
    setup = WindowsSetup(home / "AppData" / "Local" / "claude-launcher", env={"USERPROFILE": str(home)})
    list(setup.install(None, None))
    env = {
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "CLAUDE_PID": "4242",
        "SYSTEMROOT": os.environ["SYSTEMROOT"],
        "PATH": os.environ["PATH"],
    }
    payload = json.dumps({"session_id": SID, "hook_event_name": "SessionStart", "source": "startup"})
    argv = [*setup.hook_command(), "start"]
    subprocess.run(argv, input=payload.encode(), env=env, check=True, timeout=10)
    [path] = (tmp_path / "claude" / "state" / "claude-launcher" / "open").iterdir()
    record = Record.load(path)
    assert record.boot_id == WindowsHost().boot_id() and record.pid == 4242 and record.session_id == SID
