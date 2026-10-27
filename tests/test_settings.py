"""Adding claude-session-hook to Claude Code's settings.json, and taking it out."""

import json
import os
from pathlib import Path

import pytest

from claude_launcher import settings as hook_settings


class TestHook:
    def test_add_keeps_other_settings_and_hooks(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"matcher": "x", "hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [other]}}))
        assert hook_settings.add_hook(path, Path("/bin/claude-session-hook"))
        settings = json.loads(path.read_text())
        assert settings["model"] == "opus"
        assert settings["hooks"]["SessionStart"][0] == other
        assert settings["hooks"]["SessionStart"][1]["hooks"][0]["command"] == "/bin/claude-session-hook start"
        assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "/bin/claude-session-hook end"

    def test_add_twice_changes_nothing(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        before = path.read_text()
        assert not hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        assert path.read_text() == before

    def test_moved_hook_replaces_the_old_one(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, Path("/old/claude-session-hook"))
        hook_settings.add_hook(path, Path("/new/claude-session-hook"))
        groups = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert [h["command"] for g in groups for h in g["hooks"]] == ["/new/claude-session-hook start"]

    def test_writes_through_a_symlink(self, tmp_path):
        real = tmp_path / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        os.chmod(real, 0o600)
        link = tmp_path / "settings.json"
        link.symlink_to(real)
        hook_settings.add_hook(link, Path("/x/claude-session-hook"))
        assert link.is_symlink()
        assert "SessionStart" in json.loads(real.read_text())["hooks"]
        assert real.stat().st_mode & 0o777 == 0o600

    def test_remove(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"hooks": {"SessionEnd": [other]}}))
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {"hooks": {"SessionEnd": [other]}}

    def test_remove_last_hook_removes_the_key(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {}

    def test_refuses_a_non_object(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[]")
        with pytest.raises(ValueError):
            hook_settings.add_hook(path, Path("/x/claude-session-hook"))
