"""Windows' install: claude-launcher.cmd and the Windows Terminal profile.
Files only, so these run on any OS."""

import json
from pathlib import Path

import pytest

from claude_launcher.platforms.windows import terminal
from claude_launcher.platforms.windows.setup import DEFAULT_ICON, HOOK, WindowsSetup
from claude_launcher.theme import Theme

PY = "C:\\Program Files\\Python314\\python.exe"


@pytest.fixture
def env(tmp_path):
    return {"USERPROFILE": str(tmp_path / "me"), "LOCALAPPDATA": str(tmp_path / "local")}


@pytest.fixture
def setup(env, tmp_path):
    return WindowsSetup(tmp_path / "data", env=env, python=PY)


def fragment(env):
    return json.loads((terminal.fragments_dir(env) / "claude-launcher.json").read_text())


def test_scheme_is_claude_dark_from_the_theme_file():
    theme = Theme.load()
    scheme = terminal.scheme(theme)
    assert scheme["name"] == "Claude Dark"
    assert scheme["background"] == theme.colors["background"] and scheme["cursorColor"] == theme.accent
    assert scheme["brightBlack"] == theme.palette[8] and scheme["white"] == theme.palette[7]
    assert len(scheme) == 1 + 4 + 16


def test_install_writes_the_profile_and_the_command(setup, env):
    lines = list(setup.install(None, None))
    profile = fragment(env)["profiles"][0]
    assert profile["name"] == "Claude Code" and profile["colorScheme"] == "Claude Dark"
    assert profile["commandline"].startswith(f'"{PY}" "') and profile["commandline"].endswith('claude-launcher"')
    assert profile["startingDirectory"] == "%USERPROFILE%"
    assert Path(profile["icon"]).read_bytes() == DEFAULT_ICON.read_bytes()
    shim = Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    assert f'"{PY}"' in shim.read_text() and shim.read_text().rstrip().endswith("%*")
    assert any("Claude Code" in line for line in lines)


def test_the_hook_runs_python_directly(setup):
    assert setup.hook_command() == [PY, "-I", str(HOOK)]


def test_profile_guid_never_changes(setup, env):
    # Windows Terminal keeps the user's own changes to the profile under it.
    list(setup.install(None, None))
    list(setup.install(None, None))
    assert fragment(env)["profiles"][0]["guid"] == terminal.PROFILE_GUID


def test_own_icon_kept_until_default(setup, env, tmp_path):
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"png")
    list(setup.install(None, str(mine)))
    mine.unlink()  # a copy is kept
    list(setup.install(None, None))
    assert Path(fragment(env)["profiles"][0]["icon"]).read_bytes() == b"png"
    list(setup.install(None, "default"))
    assert Path(fragment(env)["profiles"][0]["icon"]).read_bytes() == DEFAULT_ICON.read_bytes()


def test_icon_must_be_a_png(setup, tmp_path):
    with pytest.raises(ValueError, match="png"):
        list(setup.install(None, str(tmp_path / "x.svg")))


def test_someone_elses_cmd_is_left_alone(setup, env):
    shim = Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"@echo mine\r\n")
    lines = list(setup.install(None, None))
    assert any("isn't one of ours" in line for line in lines)
    list(setup.uninstall())
    assert shim.read_bytes() == b"@echo mine\r\n"


def test_uninstall_takes_it_all_out(setup, env):
    list(setup.install(None, None))
    lines = list(setup.uninstall())
    assert not terminal.fragments_dir(env).exists()
    assert not (Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd").exists()
    assert len(lines) == 2


def test_terminal_option_is_for_linux(setup):
    assert any("--terminal is for Linux" in line for line in setup.install("ghostty", None))
