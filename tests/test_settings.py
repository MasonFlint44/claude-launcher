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


class TestOtherGroups:
    """Groups in the same event that aren't only ours."""

    OURS = {"type": "command", "command": "/old/claude-session-hook start"}
    OTHER = {"type": "command", "command": "other.sh"}

    def write(self, path, groups):
        path.write_text(json.dumps({"hooks": {"SessionStart": groups}}))

    def groups(self, path):
        return json.loads(path.read_text())["hooks"]["SessionStart"]

    def test_odd_and_empty_groups_are_kept(self, tmp_path):
        path = tmp_path / "settings.json"
        odd, empty, no_hooks = "not a group", {"matcher": "x", "hooks": []}, {"matcher": "y"}
        self.write(path, [odd, empty, no_hooks, {"hooks": [self.OURS]}])
        hook_settings.add_hook(path, Path("/new/claude-session-hook"))
        new = {"hooks": [{"type": "command", "command": f"{q('/new/claude-session-hook')} start"}]}
        assert self.groups(path) == [odd, empty, no_hooks, new]
        hook_settings.remove_hook(path)
        assert self.groups(path) == [odd, empty, no_hooks]

    def test_ours_beside_other_hooks(self, tmp_path):
        # The group stays with the other hook; ours moves to a group of its own.
        path = tmp_path / "settings.json"
        self.write(path, [{"matcher": "x", "hooks": [self.OTHER, self.OURS]}])
        hook_settings.add_hook(path, Path("/new/claude-session-hook"))
        new = {"hooks": [{"type": "command", "command": f"{q('/new/claude-session-hook')} start"}]}
        assert self.groups(path) == [{"matcher": "x", "hooks": [self.OTHER]}, new]
        hook_settings.remove_hook(path)
        assert self.groups(path) == [{"matcher": "x", "hooks": [self.OTHER]}]

    def test_the_same_hook_beside_others_is_left_where_it_is(self, tmp_path):
        path = tmp_path / "settings.json"
        ours = {"type": "command", "command": f"{q('/x/claude-session-hook')} start"}
        self.write(path, [{"hooks": [self.OTHER, ours]}])
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        assert self.groups(path) == [{"hooks": [self.OTHER, ours]}]


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


def _windows_forms(tmp_path):
    """What Windows' install writes: cmd /c the .cmd, by %LOCALAPPDATA% or by path."""
    from claude_launcher.platforms.windows import data_dir
    from claude_launcher.platforms.windows.setup import WindowsSetup

    env = {"USERPROFILE": str(tmp_path / "Jo Smith"), "LOCALAPPDATA": str(tmp_path / "Jo Smith" / "AppData" / "Local")}
    return [
        WindowsSetup(data_dir(env), env={**env, "SystemRoot": "C:\\Windows"}).hook_command(),
        WindowsSetup(tmp_path / "data", env=env).hook_command(),
    ]


class TestWhichHooksAreOurs:
    """Ours by the name of what runs, not by a name appearing somewhere."""

    @pytest.mark.parametrize(
        "hook",
        [
            Path("/home/me/.local/bin/claude-session-hook"),  # Linux and WSL
            Path("/home/John Doe/.local/bin/claude-session-hook"),
            ["C:\\Python\\python.exe", "-I", "C:\\cl\\bin\\claude-session-hook.py"],  # Windows before the .cmd
        ],
        ids=["linux", "spaced", "python"],
    )
    def test_every_form_written_is_ours(self, tmp_path, hook):
        path = tmp_path / "settings.json"
        hook_settings.add_hook(path, hook)
        assert hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {}

    def test_windows_forms_are_ours(self, tmp_path):
        for argv in _windows_forms(tmp_path):
            path = tmp_path / "settings.json"
            hook_settings.add_hook(path, argv)
            assert hook_settings.remove_hook(path)
            assert json.loads(path.read_text()) == {}

    WRAPPERS = [
        {"type": "command", "command": "my-claude-session-hook-wrapper start"},
        {"type": "command", "command": "/x/claude-session-hook-wrapper start"},
        {"type": "command", "command": "/x/claude-session-hook/run.sh start"},
        {"type": "command", "command": "bash -c '~/.local/bin/claude-session-hook start; logger hi'"},
        {"type": "command", "command": "C:\\w\\my-claude-session-hook.cmd", "args": ["start"]},
    ]

    def test_someone_elses_wrapper_is_left_alone(self, tmp_path):
        path = tmp_path / "settings.json"
        theirs = {"hooks": self.WRAPPERS}
        path.write_text(json.dumps({"hooks": {"SessionStart": [theirs]}}))
        hook_settings.add_hook(path, Path("/x/claude-session-hook"))
        groups = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert groups[0] == theirs and len(groups) == 2
        hook_settings.remove_hook(path)
        assert json.loads(path.read_text()) == {"hooks": {"SessionStart": [theirs]}}
