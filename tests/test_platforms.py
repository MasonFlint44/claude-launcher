"""Which platform the launcher picks."""

from __future__ import annotations

import importlib.machinery
import sys
import types

import pytest

from claude_launcher import platforms


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(platforms, "_current", None)


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
    mine = platforms.Platform("mine", host=None, events=lambda: None)
    fork.platform = lambda: mine
    monkeypatch.setitem(sys.modules, name, fork)
    assert platforms.current() is mine
