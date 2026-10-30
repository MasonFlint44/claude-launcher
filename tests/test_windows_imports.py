"""WSL imports Windows' event log and terminal profile code; neither brings
in Windows' own host (ctypes, wintypes), which WSL never uses."""

import json
import subprocess
import sys

import pytest

from claude_launcher.paths import REPO_DIR


@pytest.mark.parametrize("module", ["eventlog", "terminal"])
def test_shared_parts_dont_load_the_host(module):
    probe = (
        f"import json, sys; sys.path.insert(0, sys.argv[1]); import claude_launcher.platforms.windows.{module}; "
        "print(json.dumps(sorted(sys.modules)))"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe, str(REPO_DIR / "src")], capture_output=True, text=True, check=True
    )
    loaded = json.loads(done.stdout)
    assert "claude_launcher.platforms.windows.host" not in loaded
    assert "ctypes" not in loaded
