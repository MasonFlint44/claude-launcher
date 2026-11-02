"""platforms/linux/setup.py's links in ~/.local/bin, and taking them out."""

import json
import os
import subprocess

import pytest

from claude_launcher.paths import CORE, REPO_DIR, THEME_FILE
from claude_launcher.platforms.linux import desktop, terminals
from claude_launcher.platforms.linux import setup as linux_setup
from claude_launcher.platforms.linux.setup import (
    COMMANDS,
    LinuxSetup,
    link,
    link_commands,
    unlink_commands,
    unlink_ours,
)
from conftest import desktop_only


def env_for(home):
    return {"HOME": str(home), "PATH": "/usr/bin:/bin"}


def test_links_and_unlinks_the_commands(tmp_path):
    env = env_for(tmp_path)
    lines = list(link_commands(env))
    for name in COMMANDS:
        assert os.readlink(tmp_path / ".local" / "bin" / name) == str(REPO_DIR / "bin" / name)
    assert lines[-1] == f"warning: {tmp_path / '.local' / 'bin'} is not on your PATH"
    assert list(unlink_commands(env)) == [f"Removed {tmp_path / '.local' / 'bin' / name}" for name in COMMANDS]
    assert not list((tmp_path / ".local" / "bin").iterdir())


def test_on_path_no_warning(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / ".local" / "bin")}
    assert not [line for line in link_commands(env) if line.startswith("warning: ")]


def test_unlinks_links_made_through_a_symlink_to_the_repo(tmp_path):
    # Older installs linked to the repo by the path install.sh was run from,
    # which can go through a symlink.
    through = tmp_path / "code"
    through.symlink_to(REPO_DIR)
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name in COMMANDS:
        (bin_dir / name).symlink_to(through / "bin" / name)
    assert len(list(unlink_commands(env_for(tmp_path)))) == len(COMMANDS)
    assert not list(bin_dir.iterdir())


def test_leaves_other_links_and_files_alone(tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "claude-launcher").symlink_to("/usr/bin/env")
    (bin_dir / "claude-session-hook").write_text("mine")
    assert list(unlink_commands(env_for(tmp_path))) == []
    assert (bin_dir / "claude-launcher").is_symlink() and (bin_dir / "claude-session-hook").is_file()


def test_a_dangling_link_into_the_repo_still_goes(tmp_path):
    link = tmp_path / "gone"
    link.symlink_to(REPO_DIR / "bin" / "no-longer-here")
    assert list(unlink_ours(link)) == [f"Removed {link}"]


@desktop_only  # WSL's platform has no terminals to list
def test_terminal_list():
    done = subprocess.run([str(CORE), "terminal", "list"], capture_output=True, text=True, check=True)
    assert done.stdout.split()[:2] == ["ghostty", "kitty"] and "default" in done.stdout.split()


def test_link_leaves_a_file_that_isnt_a_link(tmp_path):
    mine = tmp_path / "claude-dark"
    mine.write_text("mine")
    assert list(link(THEME_FILE, mine)) == [f"warning: {mine} exists and isn't a link; leaving it alone"]
    assert mine.read_text() == "mine" and not mine.is_symlink()


def test_link_replaces_a_link(tmp_path):
    path = tmp_path / "a" / "claude-dark"
    path.parent.mkdir()
    path.symlink_to("/nowhere")
    assert list(link(THEME_FILE, path)) == [f"Linked {path}"]
    assert path.resolve() == THEME_FILE


# --- LinuxSetup, with the desktop parts faked ---------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", f"{home}/.local/bin:/usr/bin:/bin")
    return home


@pytest.fixture
def calls(monkeypatch):
    calls = []

    def install_icon(ctx, icon):
        calls.append(("icon", ctx, icon))
        return "Installed the icon"

    def install_terminals(ctx, names):
        calls.append(("terminals", ctx, names))
        return ["claude-launcher.desktop: opens Ghostty"]

    monkeypatch.setattr(desktop, "install_icon", install_icon)
    monkeypatch.setattr(desktop, "install_terminals", install_terminals)
    monkeypatch.setattr(desktop, "remove_terminals", lambda ctx: calls.append(("remove", ctx)) or ["x.desktop"])
    monkeypatch.setattr(desktop, "remove_icon", lambda ctx: calls.append(("remove icon", ctx)))
    return calls


def test_install(home, tmp_path, calls):
    lines = list(LinuxSetup().install("ghostty,,kitty", "/x/icon.svg"))
    bin_dir = home / ".local" / "bin"
    theme = tmp_path / "config" / "ghostty" / "themes" / "claude-dark"
    assert lines == [
        *(f"Linked {bin_dir / name}" for name in COMMANDS),
        f"Linked {theme}",
        "Installed the icon",
        "claude-launcher.desktop: opens Ghostty",
    ]
    assert theme.resolve() == THEME_FILE
    (_, ctx, icon), (_, _, names) = calls
    assert icon == "/x/icon.svg" and names == ["ghostty", "kitty"]
    assert ctx.launcher == bin_dir / "claude-launcher" and ctx.data_home == tmp_path / "data"


def test_install_without_options(home, calls):
    list(LinuxSetup().install(None, None))
    assert [c[2] for c in calls] == [None, None]


def test_uninstall(home, tmp_path, calls):
    setup = LinuxSetup()
    list(setup.install(None, None))
    lines = list(setup.uninstall())
    bin_dir = home / ".local" / "bin"
    theme = tmp_path / "config" / "ghostty" / "themes" / "claude-dark"
    assert lines == [*(f"Removed {bin_dir / name}" for name in COMMANDS), f"Removed {theme}", "Removed x.desktop"]
    assert [c[0] for c in calls[2:]] == ["remove", "remove icon"]
    assert not theme.is_symlink() and not list(bin_dir.iterdir())


def test_uninstall_goes_on_past_a_failing_part(home, calls, monkeypatch):
    def fails(ctx):
        raise OSError("Permission denied")

    monkeypatch.setattr(desktop, "remove_terminals", fails)
    monkeypatch.setattr(desktop, "remove_icon", fails)
    lines = list(LinuxSetup().uninstall())
    assert lines == [
        "warning: could not remove every desktop entry and profile: Permission denied",
        "warning: could not remove the app icon: Permission denied",
    ]


# --- check(): --terminal and --icon before anything is changed ----------------


def test_check_takes_what_install_would(home, tmp_path, monkeypatch):
    monkeypatch.setattr(terminals.Terminal, "installed", lambda self: True)
    svg = tmp_path / "mine.svg"
    svg.write_text("<svg/>")
    setup = LinuxSetup()
    for names in (None, "", "auto", "ghostty,kitty"):
        setup.check(names, None)
    for icon in (None, "default", str(svg), str(desktop.ICONS_DIR / "claude-launcher-256.png")):
        setup.check(None, icon)
    assert not (home / ".local").exists() and not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    ("names", "error"),
    [("ghostty,nope", "unknown terminal: nope"), ("kitty", "not installed: kitty")],
)
def test_check_turns_down_a_terminal(home, monkeypatch, names, error):
    monkeypatch.setattr(terminals.Terminal, "installed", lambda self: self.name != "kitty")
    with pytest.raises(ValueError, match=error):
        LinuxSetup().check(names, None)


def save_terminals(tmp_path, names):
    state = tmp_path / "data" / "claude-launcher" / "terminals.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps(names))


@pytest.mark.parametrize(
    ("saved", "error"),
    [
        (["ghostty"], "Ghostty, which the launcher was set up with, isn't installed any more"),
        (["ghostty", "kitty"], "Ghostty and kitty, which the launcher was set up with, aren't installed any more"),
    ],
)
def test_check_turns_down_last_times_terminal(home, tmp_path, monkeypatch, saved, error):
    monkeypatch.setattr(terminals.Terminal, "installed", lambda self: self.name not in ("ghostty", "kitty"))
    save_terminals(tmp_path, saved)
    with pytest.raises(ValueError, match=error) as e:
        LinuxSetup().check(None, None)
    assert str(e.value).endswith("; run install.sh --terminal auto (or --terminal NAME)")


def test_check_lets_a_new_terminal_replace_last_times(home, tmp_path, monkeypatch):
    monkeypatch.setattr(terminals.Terminal, "installed", lambda self: self.name != "ghostty")
    save_terminals(tmp_path, ["ghostty"])
    setup = LinuxSetup()
    setup.check("auto", None)
    setup.check("kitty", None)
    monkeypatch.setattr(terminals.Terminal, "installed", lambda self: True)
    setup.check(None, None)


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        ("x.jpg", b"", "svg or .png"),
        ("missing.svg", None, "svg or .png"),
        ("wide.png", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (64).to_bytes(4, "big") + (32).to_bytes(4, "big"), "square"),
        ("short.png", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0", "square"),
    ],
)
def test_check_turns_down_an_icon(home, tmp_path, name, content, error):
    icon = tmp_path / name
    if content is not None:
        icon.write_bytes(content)
    with pytest.raises(ValueError, match=error):
        LinuxSetup().check(None, str(icon))


# --- terminal list, terminal open --------------------------------------------


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ([], "usage: terminal list | terminal open NAME [LAUNCHER]"),
        (["open"], "usage: terminal list | terminal open NAME [LAUNCHER]"),
        (["close", "guake"], "usage: terminal list | terminal open NAME [LAUNCHER]"),
        (["open", "ghostty"], "can't open ghostty this way; only Guake's desktop entry uses terminal open"),
        (["open", "nope"], "can't open nope this way; only Guake's desktop entry uses terminal open"),
    ],
)
def test_terminal_turns_down(args, error, capsys):
    assert linux_setup._terminal(args) == 2
    assert capsys.readouterr().err == error + "\n"


def test_terminal_open_guake(home, monkeypatch):
    opened = []
    monkeypatch.setattr(terminals.Guake, "open", lambda self, ctx: opened.append(ctx))
    assert linux_setup._terminal(["open", "guake", "/x/claude-launcher"]) == 0
    assert opened[0].launcher.as_posix() == "/x/claude-launcher"
