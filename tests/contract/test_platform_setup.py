"""What every platform's Setup and Platform fields have to be, on the platform
these tests run on. Nothing here installs anything."""

from __future__ import annotations

import os
import platform as machine
from pathlib import Path

from claude_launcher import fzf
from claude_launcher.platforms import current
from claude_launcher.restart import SHARED

PLATFORM = current()


def test_setup_says_what_it_sets_up():
    setup = PLATFORM.setup()
    assert setup.what and setup.done
    hook = setup.hook_command()
    if isinstance(hook, Path):
        assert hook.name.startswith("claude-session-hook")
    else:
        assert hook and all(isinstance(part, str) for part in hook)
        assert any("claude-session-hook" in part for part in hook)
    assert all(callable(command) for command in setup.commands.values())
    assert callable(setup.check) and callable(setup.install) and callable(setup.uninstall)


def test_there_is_an_fzf_to_fetch_here():
    assert fzf.build_name(PLATFORM.fzf_os, machine.machine()) is not None


def test_the_core_command_line_names_the_core():
    line = PLATFORM.core_command(["ui", "key", "esc", "a b"])
    assert isinstance(line, str) and "claude-launcher-core" in line


def test_its_own_files_go_in_an_absolute_directory():
    assert PLATFORM.data_dir(os.environ).is_absolute()


def test_fields_have_the_right_shapes():
    assert PLATFORM.fzf_listen in ("socket", "port")
    assert PLATFORM.columns_lost >= 0
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in PLATFORM.fzf_env.items())
    shared = {r.key for r in SHARED}
    assert all(r.key not in shared and r.header and r.row for r in PLATFORM.reasons)
