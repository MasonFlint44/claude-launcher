"""Where the launcher keeps its own files on Linux."""

from pathlib import Path

from claude_launcher.platforms.linux import data_dir


def test_follows_xdg():
    assert data_dir({"XDG_DATA_HOME": "/data", "HOME": "/h"}) == Path("/data/claude-launcher")


def test_default_is_under_home():
    assert data_dir({"HOME": "/h"}) == Path("/h/.local/share/claude-launcher")
