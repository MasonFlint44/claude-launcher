"""The platform the launcher runs on: platforms/linux for now.

`current()` picks it once per run, and imports only that platform's code. A
fork that adds a platform of its own can put a `fork.py` here with a
`platform()` function; it's used ahead of the built-in ones. This repo never
creates that file, so a fork's copy never conflicts with an update.
See docs/dev/platforms.md.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

from .base import Event, EventSource, Host, Platform, Setup

__all__ = ["Event", "EventSource", "Host", "Platform", "Setup", "Unsupported", "current"]

_current: Platform | None = None


class Unsupported(RuntimeError):
    pass


def current() -> Platform:
    global _current
    if _current is None:
        _current = _detect()
    return _current


def _detect() -> Platform:
    # find_spec, not try/except ImportError: an error inside fork.py should show.
    if importlib.util.find_spec(f"{__name__}.fork") is not None:
        return importlib.import_module(f"{__name__}.fork").platform()
    if sys.platform.startswith("linux"):
        from .linux import platform

        return platform()
    raise Unsupported(f"claude-launcher doesn't run on {sys.platform} yet")
