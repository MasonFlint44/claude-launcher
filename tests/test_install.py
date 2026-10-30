"""install.py's steps, the same on every platform, with a fake platform and
setup: what the lines say, and what's left as it was when a step fails."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from claude_launcher import install


class FakeSetup:
    what = "the thing"
    done = "Open it."

    def __init__(self, hook=None, fails=None, check=None, uninstall_fails=None):
        self.hook = hook
        self.fails = fails
        self.check_fails = check
        self.uninstall_fails = uninstall_fails
        self.installed = False

    def check(self):
        if self.check_fails:
            raise self.check_fails

    def hook_command(self):
        return self.hook

    def install(self, terminal_names, icon):
        yield "Linked it"
        if self.fails:
            raise self.fails
        self.installed = True
        yield "Set it up"

    def uninstall(self):
        yield "Removed it"
        if self.uninstall_fails:
            raise self.uninstall_fails

    commands = {}


class FakePlatform:
    fzf_os = "linux"

    def __init__(self, setup, data):
        self._setup, self.data = setup, data

    def setup(self):
        return self._setup

    def data_dir(self, env):
        return self.data


@pytest.fixture
def env(claude_home, tmp_path, monkeypatch):
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, **kw: "claude" if name == "claude" else which(name, **kw))
    monkeypatch.setattr(install.fzf, "find", lambda own: ("fzf", (0, 70, 0)))
    claude_home.settings = claude_home.paths.config / "settings.json"
    claude_home.hook = claude_home.home / ".local" / "bin" / "claude-session-hook"
    claude_home.data = tmp_path / "data"
    return claude_home


def run(env, setup):
    return list(install.install(FakePlatform(setup, env.data)))


def test_install(env):
    setup = FakeSetup(hook=env.hook)
    lines = run(env, setup)
    assert lines[0] == "Using fzf 0.70.0 (fzf)"
    assert lines[1].startswith("Added the session hook to ")
    assert lines[-3:] == ["Linked it", "Set it up", "Done. Open it."]
    hooks = json.loads(env.settings.read_text())["hooks"]
    assert hooks["SessionStart"] == [{"hooks": [{"type": "command", "command": f"{env.hook} start"}]}]
    assert "already in" in run(env, FakeSetup(hook=env.hook))[1]


def test_a_program_and_its_arguments(env):
    # Windows' hook: cmd and the .cmd that runs it, with no shell.
    lines = run(env, FakeSetup(hook=["C:\\cmd.exe", "/d", "/c", "C:\\x\\claude-session-hook.cmd"]))
    assert lines[-1] == "Done. Open it."
    hooks = json.loads(env.settings.read_text())["hooks"]
    assert hooks["SessionEnd"] == [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "C:\\cmd.exe",
                    "args": ["/d", "/c", "C:\\x\\claude-session-hook.cmd", "end"],
                }
            ]
        }
    ]


def test_no_claude_is_a_warning(env, monkeypatch):
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, **kw: None if name == "claude" else which(name, **kw))
    lines = run(env, FakeSetup(hook=env.hook))
    assert lines[0].startswith("warning: claude is not on your PATH")
    assert lines[-1] == "Done. Open it."


def test_fzf_that_cant_be_fetched_stops_before_anything_changes(env, monkeypatch):
    monkeypatch.setattr(install.fzf, "find", lambda own: (None, (0, 44, 0)))

    def fetch(own, system):
        raise OSError("no network")

    monkeypatch.setattr(install.fzf, "fetch", fetch)
    setup = FakeSetup(hook=env.hook)
    lines = run(env, setup)
    assert lines[0].startswith("fzf 0.44.0 is older")
    assert lines[-2:] == ["warning: no network", "error: could not fetch fzf; install fzf 0.67 or newer, then re-run"]
    assert not env.settings.exists() and not setup.installed


def test_a_setup_that_cant_go_ahead_stops_first(env):
    lines = run(env, FakeSetup(hook=env.hook, check=RuntimeError("not here")))
    assert lines == ["error: not here"]
    assert not env.settings.exists()


def test_a_failed_setup_is_an_error(env):
    lines = run(env, FakeSetup(hook=env.hook, fails=OSError("disk full")))
    assert lines[-3:] == ["Linked it", "warning: disk full", "error: could not set up the thing"]


def test_settings_that_arent_json_are_a_warning(env):
    env.settings.parent.mkdir(parents=True, exist_ok=True)
    env.settings.write_text("{not json")
    lines = run(env, FakeSetup(hook=env.hook))
    assert lines[1].startswith(f"warning: could not add the session hook to {env.settings}")
    assert lines[-1] == "Done. Open it."
    assert env.settings.read_text() == "{not json"


def test_uninstall(env):
    run(env, FakeSetup(hook=env.hook))
    env.data.mkdir()
    env.paths.state.mkdir(parents=True)
    lines = list(install.uninstall(FakePlatform(FakeSetup(), env.data)))
    assert lines[0] == "Removed it"
    assert lines[1] == f"Removed the session hook from {env.settings}"
    assert lines[2].startswith(f"Removed {env.data}")
    assert lines[3].startswith("Left the launcher's state in ")
    assert "hooks" not in json.loads(env.settings.read_text())
    assert not env.data.exists()


@pytest.mark.parametrize("failure", [OSError("busy"), AttributeError("a bug")])
def test_uninstall_goes_on_past_any_failure(env, failure):
    run(env, FakeSetup(hook=env.hook))
    env.data.mkdir()
    lines = list(install.uninstall(FakePlatform(FakeSetup(uninstall_fails=failure), env.data)))
    assert lines[1] == f"warning: could not remove everything the installer set up: {failure}"
    assert "hooks" not in json.loads(env.settings.read_text())
    assert not env.data.exists()


def test_uninstall_with_settings_that_arent_json(env):
    env.settings.parent.mkdir(parents=True, exist_ok=True)
    env.settings.write_text("{not json")
    env.data.mkdir()
    lines = list(install.uninstall(FakePlatform(FakeSetup(), env.data)))
    assert lines[1].startswith(f"warning: could not remove the session hook from {env.settings}")
    assert not env.data.exists()


def test_uninstall_when_the_platform_cant_make_its_setup(env):
    class Broken(FakePlatform):
        def setup(self):
            raise RuntimeError("no setup")

    lines = list(install.uninstall(Broken(None, Path(env.data))))
    assert lines[0] == "warning: could not remove everything the installer set up: no setup"
