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
print(json.dumps(sorted(m for m in sys.modules if m.startswith("claude_launcher"))), file=sys.stderr)
"""
NOT_ON_A_KEY_PRESS = ("claude_launcher.platforms",)


@pytest.mark.parametrize(
    "action", [["change", "abc"], ["change", "~/"], ["tab", "~/"], ["key", "esc", "x"], ["result", "x"]]
)
def test_key_actions_stay_light(tmp_path, claude_home, action):
    done = subprocess.run(
        [sys.executable, "-c", PROBE, str(REPO_DIR / "src"), "--run", str(tmp_path), "ui", *action],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = json.loads(done.stderr.strip().splitlines()[-1])
    assert [m for m in loaded if m.startswith(NOT_ON_A_KEY_PRESS)] == []
