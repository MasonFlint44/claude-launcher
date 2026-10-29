"""Which platform the launcher picks."""

from __future__ import annotations

import importlib.machinery
import sys
import types
from pathlib import Path

import pytest

from claude_launcher import platforms


def platform(name, **parts):
    return platforms.Platform(
        **{
            "name": name,
            "host": None,
            "events": lambda: None,
            "setup": lambda: None,
            "fzf_os": "x",
            "fzf_shell": "sh -c",
            "core_command": " ".join,
            "fzf_env": {},
            "columns_lost": 0,
            "fzf_listen": "socket",
            "data_dir": lambda env: Path("/data"),
            **parts,
        }
    )


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(platforms, "_current", None)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="loads Linux's platform")
def test_linux(fresh, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert platforms.current().name == "linux"
    assert platforms.current() is platforms.current()


def test_unsupported(fresh, monkeypatch):
    monkeypatch.setattr(sys, "platform", "plan9")
    with pytest.raises(platforms.Unsupported, match="plan9"):
        platforms.current()


def test_a_forks_platform_comes_first(fresh, monkeypatch):
    name = "claude_launcher.platforms.fork"
    fork = types.ModuleType(name)
    fork.__spec__ = importlib.machinery.ModuleSpec(name, None)
    mine = platform("mine", setup=lambda: None)
    fork.platform = lambda: mine
    monkeypatch.setitem(sys.modules, name, fork)
    assert platforms.current() is mine


def test_the_core_runs_a_platforms_own_commands(fresh, monkeypatch, capsys):
    from claude_launcher import cli

    calls = []

    class Setup:
        commands = {"hello": lambda args: calls.append(args) or 7}

    mine = platform("mine", setup=Setup)
    monkeypatch.setattr(platforms, "_current", mine)
    assert cli.main(["hello", "a", "b"]) == 7
    assert calls == [["a", "b"]]
    assert cli.main(["nope"]) == 2
    assert "install run" in capsys.readouterr().err  # the usage
