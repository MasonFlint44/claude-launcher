"""install.ps1, read as text on any OS; CI runs it, and PSScriptAnalyzer
over it, on Windows."""

import re

from claude_launcher.paths import REPO_DIR

SCRIPT = REPO_DIR / "install.ps1"


def test_ascii_only():
    # Windows PowerShell 5.1 reads a script without a BOM in the code page.
    SCRIPT.read_bytes().decode("ascii")


def test_help_is_the_whole_comment_block():
    lines = SCRIPT.read_text(encoding="ascii").splitlines()
    block = []
    for line in lines:
        if not line.startswith("#"):
            break
        block.append(re.sub(r"^# ?", "", line))
    assert block[0].startswith("Set up claude-launcher on Windows")
    assert any("-Uninstall" in line for line in block)
    assert lines[len(block)].startswith("param(")


def test_no_state_changing_verbs_without_shouldprocess():
    # PSScriptAnalyzer's PSUseShouldProcessForStateChangingFunctions, as CI runs it.
    names = re.findall(r"^function ([\w-]+)", SCRIPT.read_text(encoding="ascii"), re.MULTILINE)
    flagged = ("New", "Set", "Remove", "Start", "Stop", "Restart", "Reset", "Update")
    assert names and not [n for n in names if n.split("-")[0] in flagged]
