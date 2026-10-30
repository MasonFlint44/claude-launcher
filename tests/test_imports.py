"""Each key press runs the core once, so the key actions import only what
the picker needs: no platform, install or terminal code."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from claude_launcher.paths import REPO_DIR

PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from claude_launcher.cli import main
main(sys.argv[2:])
print(json.dumps(sorted(sys.modules)), file=sys.stderr)
"""
NOT_ON_A_KEY_PRESS = ("claude_launcher.platforms", "claude_launcher.settings")
# Only fetching fzf needs these, and they cost about 12 ms.
FETCHING = ("urllib", "hashlib", "tarfile", "zipfile")


@pytest.mark.parametrize(
    "action",
    [
        ["change", "abc"],
        ["change", "~/"],
        ["tab", "~/"],
        ["key", "esc", "x"],
        ["key", "ctrl-c", ""],
        ["result", "x"],
        ["enter", "x", ""],
        ["sessions"],
        ["dismiss", "x"],
        ["menu"],
    ],
)
def test_key_actions_stay_light(tmp_path, claude_home, action):
    # As the picker leaves it once it has started: it saves how key bindings
    # run the core, so key presses don't have to ask the platform.
    (tmp_path / "state.json").write_text(json.dumps({"shell": "bash -c", "core": "core --run x"}))
    done = subprocess.run(
        [sys.executable, "-c", PROBE, str(REPO_DIR / "src"), "--run", str(tmp_path), "ui", *action],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = json.loads(done.stderr.strip().splitlines()[-1])
    assert [m for m in loaded if m.startswith(NOT_ON_A_KEY_PRESS)] == []
    assert [m for m in loaded if m.split(".")[0] in FETCHING] == []
