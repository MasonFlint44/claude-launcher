"""install.sh and install.sh --uninstall, in a fake home."""

import json
import os
import shutil
import subprocess

import pytest

from claude_launcher.install import APP_ID, ICON_SIZES
from claude_launcher.paths import REPO_DIR

pytestmark = pytest.mark.skipif(not shutil.which("fzf"), reason="needs fzf")


def run(home, *args):
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:{home}/stubs:/usr/bin:/bin", "LANG": "C.UTF-8"}
    return subprocess.run([str(REPO_DIR / "install.sh"), *args], env=env, capture_output=True, text=True)


def test_install_then_uninstall(tmp_path):
    home = tmp_path / "home"
    (home / "dotfiles").mkdir(parents=True)
    (home / ".claude").mkdir()
    real_settings = home / "dotfiles" / "settings.json"
    real_settings.write_text(json.dumps({"model": "opus"}))
    (home / ".claude" / "settings.json").symlink_to(real_settings)
    # A Ghostty to find, so the check below doesn't depend on what this
    # machine has installed: the first installed terminal gets picked.
    (home / "stubs").mkdir()
    (home / "stubs" / "ghostty").write_text("#!/bin/sh\n")
    (home / "stubs" / "ghostty").chmod(0o755)

    done = run(home)
    assert done.returncode == 0, done.stdout + done.stderr
    bin_dir = home / ".local" / "bin"
    assert os.readlink(bin_dir / "claude-launcher") == str(REPO_DIR / "bin" / "claude-launcher")
    assert os.readlink(bin_dir / "claude-session-hook") == str(REPO_DIR / "bin" / "claude-session-hook")
    assert (home / ".config" / "ghostty" / "themes" / "claude-dark").resolve() == REPO_DIR / "themes" / "claude-dark"
    desktop = home / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
    assert "--command=bash -lc" in desktop.read_text()
    icons = home / ".local" / "share" / "icons" / "hicolor"
    assert (icons / "scalable" / "apps" / f"{APP_ID}.svg").resolve() == REPO_DIR / "share/icons/claude-launcher.svg"
    for size in ICON_SIZES:
        assert (icons / f"{size}x{size}" / "apps" / f"{APP_ID}.png").exists()
    assert (home / ".claude" / "settings.json").is_symlink()
    settings = json.loads(real_settings.read_text())
    assert settings["model"] == "opus" and set(settings["hooks"]) == {"SessionStart", "SessionEnd"}

    again = run(home)
    assert again.returncode == 0 and "already in" in again.stdout

    done = run(home, "--uninstall")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (bin_dir / "claude-launcher").exists() and not desktop.exists()
    assert not list(icons.rglob(f"{APP_ID}.*"))
    assert json.loads(real_settings.read_text()) == {"model": "opus"}


def test_leaves_a_real_file_alone(tmp_path):
    home = tmp_path / "home"
    themes = home / ".config" / "ghostty" / "themes"
    themes.mkdir(parents=True)
    (themes / "claude-dark").write_text("background = #000000\n")
    done = run(home)
    assert done.returncode == 0
    assert "isn't a link; leaving it alone" in done.stderr
    assert (themes / "claude-dark").read_text() == "background = #000000\n"


def test_a_failed_step_says_so(tmp_path):
    home = tmp_path / "home"
    done = run(home, "--terminal", "no-such-terminal")
    assert done.returncode != 0
    assert "could not set up the desktop launcher" in done.stderr


def test_uninstall_goes_on_past_a_failed_step(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude" / "settings.json"
    settings.write_text("{}")
    assert run(home).returncode == 0
    applications = home / ".local" / "share" / "applications"
    applications.chmod(0o555)  # its desktop entry can't be removed
    try:
        done = run(home, "--uninstall")
    finally:
        applications.chmod(0o755)
    assert "could not remove every desktop entry" in done.stderr
    assert "hooks" not in json.loads(settings.read_text())
    assert not (home / ".local" / "bin" / "claude-session-hook").exists()
