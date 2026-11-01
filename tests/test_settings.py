"""Adding claude-session-hook to Claude Code's settings.json, and taking it out."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from claude_launcher import settings as hook_settings


def q(path: str) -> str:
    """The hook's path as the shell form writes it (Windows' backslashes get quoted too)."""
    return shlex.quote(str(Path(path)))


class TestHook:
    def test_add_keeps_other_settings_and_hooks(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"matcher": "x", "hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [other]}}))
        assert hook_settings.add_hook(path, Path("/bin/claude-session-hook"))
        settings = json.loads(path.read_text())
        assert settings["model"] == "opus"
        assert settings["hooks"]["SessionStart"][0] == other
        assert settings["hooks"]["SessionStart"][1]["hooks"][0]["command"] == f"{q('/bin/claude-session-hook')} start"
        assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == f"{q('/bin/claude-session-hook')} end"

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
        assert [h["command"] for g in groups for h in g["hooks"]] == [f"{q('/new/claude-session-hook')} start"]

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
        if os.name != "nt":  # Windows files have no such modes
            assert real.stat().st_mode & 0o777 == 0o600

    def test_remove(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"hooks": {"SessionEnd": [other]}}))
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {"hooks": {"SessionEnd": [other]}}

    SPACED = "/home/John Doe/.local/bin/claude-session-hook"

    def test_a_path_with_a_space_is_quoted(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, Path(self.SPACED))
        hooks = json.loads(path.read_text())["hooks"]
        assert hooks["SessionStart"][0]["hooks"][0]["command"] == f"{q(self.SPACED)} start"
        assert hooks["SessionEnd"][0]["hooks"][0]["command"] == f"{q(self.SPACED)} end"

    @pytest.mark.skipif(os.name == "nt", reason="runs the command with sh")
    def test_a_quoted_hook_runs_through_a_shell(self, tmp_path):
        hook = tmp_path / "John Doe" / "claude-session-hook"
        hook.parent.mkdir()
        hook.write_text('#!/bin/sh\necho "$1" > "$(dirname "$0")/ran"\n')
        hook.chmod(0o755)
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, hook)
        command = json.loads(path.read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        subprocess.run(["sh", "-c", command], check=True)
        assert (hook.parent / "ran").read_text() == "start\n"

    def _unquoted(self, path):
        """settings.json as an install from before the quoting left it."""
        old = {
            event: [{"hooks": [{"type": "command", "command": f"{self.SPACED} {arg}"}]}]
            for event, arg in hook_settings.HOOK_EVENTS
        }
        path.write_text(json.dumps({"hooks": old}))

    def test_reinstall_replaces_an_unquoted_entry(self, tmp_path):
        path = tmp_path / "settings.json"
        self._unquoted(path)
        assert hook_settings.add_hook(path, Path(self.SPACED))
        groups = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert [h["command"] for g in groups for h in g["hooks"]] == [f"{q(self.SPACED)} start"]
        assert not hook_settings.add_hook(path, Path(self.SPACED))

    def test_remove_takes_out_an_unquoted_entry(self, tmp_path):
        path = tmp_path / "settings.json"
        self._unquoted(path)
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {}

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


class TestHookWithoutAShell:
    """Windows: Claude runs python.exe with the hook script as an argument."""

    ARGV = ["C:\\Python\\python.exe", "-I", "C:\\cl\\bin\\claude-session-hook.py"]

    def test_program_and_args(self, tmp_path):
        path = tmp_path / "settings.json"
        assert hook_settings.add_hook(path, self.ARGV)
        hooks = json.loads(path.read_text())["hooks"]
        assert hooks["SessionStart"] == [
            {"hooks": [{"type": "command", "command": self.ARGV[0], "args": [*self.ARGV[1:], "start"]}]}
        ]
        assert hooks["SessionEnd"][0]["hooks"][0]["args"][-1] == "end"

    def test_again_changes_nothing(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, self.ARGV)
        assert not hook_settings.add_hook(path, self.ARGV)

    def test_replaces_the_shell_form(self, tmp_path):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        hook_settings.add_hook(path, self.ARGV)
        groups = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert [h.get("args") for g in groups for h in g["hooks"]] == [[*self.ARGV[1:], "start"]]

    def test_remove(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"model": "opus"}))
        hook_settings.add_hook(path, self.ARGV)
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {"model": "opus"}


class TestUnusualFiles:
    """Shapes Claude wouldn't write: reported plainly, and the file left alone."""

    @pytest.mark.parametrize(
        "settings, message",
        [
            ({"hooks": []}, "hooks isn't an object"),
            ({"hooks": None}, "hooks isn't an object"),
            ({"hooks": {"SessionStart": {}}}, "hooks.SessionStart isn't a list"),
            ({"hooks": {"SessionEnd": None}}, "hooks.SessionEnd isn't a list"),
            ({"hooks": {"SessionStart": [{"hooks": None}]}}, "aren't a list"),
            ({"hooks": {"SessionStart": [{"hooks": "x"}]}}, "aren't a list"),
        ],
    )
    @pytest.mark.parametrize("change", ["add", "remove"])
    def test_refused_with_a_plain_message(self, tmp_path, settings, message, change):
        path = tmp_path / "settings.json"
        text = json.dumps(settings)
        path.write_text(text)
        with pytest.raises(ValueError, match=message):
            if change == "add":
                hook_settings.add_hook(path, Path("/x/claude-session-hook"))
            else:
                hook_settings.remove_hook(path)
        assert path.read_text() == text

    def test_other_events_are_not_checked(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"hooks": {"Stop": {"odd": True}}}))
        assert hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        assert json.loads(path.read_text())["hooks"]["Stop"] == {"odd": True}

    def test_a_bom_is_read_and_not_written(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "opus"}).encode())
        assert hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert json.loads(raw)["model"] == "opus"
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {"model": "opus"}
