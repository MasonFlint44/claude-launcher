"""bin/claude-launcher and libexec/claude-launcher-core on a Python too old
for the package: a line saying so, not a traceback."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys

import pytest

from claude_launcher.paths import REPO_DIR

SCRIPTS = [REPO_DIR / "bin" / "claude-launcher", REPO_DIR / "libexec" / "claude-launcher-core"]
# The script run as an older Python would: its version is all that differs.
AS_IF = """
import runpy, sys
sys.version_info = (3, 9, 18, "final", 0)
runpy.run_path(sys.argv[1], run_name="__main__")
"""


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_too_old_a_python_says_so(script):
    done = subprocess.run([sys.executable, "-c", AS_IF, str(script)], capture_output=True, text=True)
    assert done.returncode == 1
    assert done.stderr == f"claude-launcher needs Python 3.10 or newer (this is 3.9: {sys.executable})\n"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_check_is_old_syntax(script):
    # Everything up to the check has to parse on the oldest Python 3 ast can check against.
    source = script.read_text(encoding="utf-8")
    head = source[: source.index("sys.path.insert")]
    ast.parse(head, feature_version=(3, 4))


OLD = [p for p in ("python3.9", "python3.8", "python3.7", "python3.6", "python2") if shutil.which(p)]


@pytest.mark.skipif(not OLD, reason="no Python older than 3.10 here")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_on_a_real_old_python(script):
    done = subprocess.run([OLD[0], str(script)], capture_output=True, text=True)
    assert done.returncode == 1
    assert done.stderr.startswith("claude-launcher needs Python 3.10 or newer (this is ")
