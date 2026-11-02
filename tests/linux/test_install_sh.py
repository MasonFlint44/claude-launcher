"""install.sh and install.sh --uninstall, in a fake home."""

import json
import os
import shutil
import subprocess

import pytest

from claude_launcher import fzf
from claude_launcher.paths import REPO_DIR
from claude_launcher.platforms.linux.desktop import APP_ID, ICON_SIZES
from claude_launcher.platforms.linux.terminals import TERMINALS
from conftest import desktop_only

_FZF = shutil.which("fzf")
_FZF_VERSION = fzf.version(_FZF) if _FZF else None

# An older fzf would be fetched, and no test reaches the network. In WSL the
# installer would write to the real Windows Terminal's settings.
pytestmark = [
    pytest.mark.skipif(not _FZF_VERSION or _FZF_VERSION < fzf.MIN_VERSION, reason="needs fzf 0.67+"),
    desktop_only,
]


@pytest.fixture(scope="module")
def fzf_dir(tmp_path_factory):
    """The fzf these tests run with (CI's floors job puts 0.67 first), alone
    in a directory of its own so nothing else on PATH leaks in."""
    path = tmp_path_factory.mktemp("fzf")
    (path / "fzf").symlink_to(_FZF)
    return path


@pytest.fixture
def run(fzf_dir):
    return lambda home, *args: _run(home, fzf_dir, *args)


def _run(home, fzf_dir, *args):
    # Fetching fzf goes to a closed port and fails: no test reaches the network.
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{home}/stubs:{fzf_dir}:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "https_proxy": "http://127.0.0.1:9",
    }
    done = subprocess.run([str(REPO_DIR / "install.sh"), *args], env=env, capture_output=True, text=True)
    assert "fetching" not in done.stdout, done.stdout
    return done


def nothing_written(home):
    """The install stopped before any step: no hook, links, fzf or icon."""
    assert not (home / ".claude" / "settings.json").exists()
    assert not (home / ".local").exists()
    assert not (home / ".config").exists()


def test_install_then_uninstall(tmp_path, run):
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


def test_leaves_a_real_file_alone(tmp_path, run):
    home = tmp_path / "home"
    themes = home / ".config" / "ghostty" / "themes"
    themes.mkdir(parents=True)
    (themes / "claude-dark").write_text("background = #000000\n")
    done = run(home)
    assert done.returncode == 0
    assert "isn't a link; leaving it alone" in done.stderr
    assert (themes / "claude-dark").read_text() == "background = #000000\n"


def test_an_unknown_terminal_stops_before_anything(tmp_path, run):
    home = tmp_path / "home"
    done = run(home, "--terminal", "ghostty,no-such-terminal")
    assert done.returncode != 0
    assert done.stderr.endswith(f"unknown terminal: no-such-terminal (one of: {', '.join(TERMINALS)})\n")
    nothing_written(home)


def test_last_times_terminal_gone_stops_before_anything(tmp_path, run):
    # One that isn't on the PATH install.sh runs with.
    gone = next(
        (t for t in TERMINALS.values() if t.program and not shutil.which(t.program, path="/usr/bin:/bin")), None
    )
    if gone is None:
        pytest.skip("every terminal is installed here")
    home = tmp_path / "home"
    state = home / ".local" / "share" / "claude-launcher" / "terminals.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps([gone.name]))
    done = run(home)
    assert done.returncode != 0
    assert done.stderr.endswith(
        f"{gone.label}, which the launcher was set up with, isn't installed any more;"
        " run install.sh --terminal auto (or --terminal NAME)\n"
    )
    assert [p for p in home.rglob("*") if p.is_file()] == [state]
    assert not (home / ".claude").exists() and not (home / ".config").exists()


WIDE_PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (64).to_bytes(4, "big") + (32).to_bytes(4, "big")


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [("x.jpg", WIDE_PNG, "svg or .png"), ("wide.png", WIDE_PNG, "square"), ("short.png", WIDE_PNG[:18], "square")],
)
def test_a_bad_icon_stops_before_anything(tmp_path, name, content, error, run):
    home = tmp_path / "home"
    icon = tmp_path / name
    icon.write_bytes(content)
    done = run(home, "--icon", str(icon))
    assert done.returncode != 0
    assert error in done.stderr and "Traceback" not in done.stderr
    nothing_written(home)


def test_a_failed_step_says_so(tmp_path, run):
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "applications").write_text("not a directory")  # no desktop entry fits
    done = run(home)
    assert done.returncode != 0
    assert "could not set up the desktop launcher" in done.stderr


def test_uninstall_goes_on_past_a_failed_step(tmp_path, run):
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


def test_help_lists_the_platforms_terminals(tmp_path, run):
    done = run(tmp_path, "--help")
    assert done.returncode == 0
    text = " ".join(done.stdout.split())
    assert "One of: " + " ".join(TERMINALS) + "." in text
    assert max(len(line) for line in done.stdout.splitlines()) <= 80


def test_uninstall_says_so_when_python_fails(tmp_path):
    home = tmp_path / "home"
    stubs = home / "stubs"
    stubs.mkdir(parents=True)
    # A python3 that passes the version check, then fails as a crash would.
    real = shutil.which("python3")
    (stubs / "python3").write_text(
        f'#!/bin/sh\n[ "$1" = -c ] && exec {real} "$@"\necho "Traceback (most recent call last):" >&2\nexit 1\n'
    )
    (stubs / "python3").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "LANG": "C.UTF-8"}
    done = subprocess.run([str(REPO_DIR / "install.sh"), "--uninstall"], env=env, capture_output=True, text=True)
    assert done.returncode == 0
    assert "the uninstall stopped partway" in done.stderr


@pytest.mark.parametrize("name", ["nope.svg", "no/such/dir.svg"])
def test_an_icon_that_isnt_there(tmp_path, name, run):
    done = run(tmp_path, "--icon", str(tmp_path / name))
    assert done.returncode == 1
    assert done.stderr.strip().endswith(f"not a file: {tmp_path / name}")
    assert "realpath" not in done.stderr
    assert not (tmp_path / ".local").exists()  # stopped before any step


def test_help_lists_its_own_options(tmp_path, run):
    done = run(tmp_path, "--help")
    assert "install.sh -h|--help" in done.stdout


def test_icon_equals_expands_a_tilde(tmp_path, run):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mine.svg").write_text("<svg/>")
    done = run(home, "--icon=~/mine.svg")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (home / ".local" / "share" / "claude-launcher" / "icon.svg").read_text() == "<svg/>"
    done = run(home, "--icon=~")
    assert done.returncode == 1
    assert done.stderr.strip().endswith(f"not a file: {home}")
