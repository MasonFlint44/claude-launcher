"""The login shell the desktop launcher and WSL's profile start the launcher in."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from claude_launcher.platforms.linux import shell


def entry(path):
    return SimpleNamespace(pw_shell=path)


@pytest.fixture
def runnable(tmp_path):
    """A runnable file at tmp_path/<name>, for any shell name."""

    def make(name):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return str(path)

    return make


@pytest.mark.parametrize("name", shell.SHELLS)
def test_your_shell(monkeypatch, runnable, name):
    path = runnable(name)
    monkeypatch.setattr(shell.pwd, "getpwnam", lambda user: entry(path))
    assert shell.login_shell("me") == path


def test_this_users_shell_by_default(monkeypatch, runnable):
    path = runnable("zsh")
    monkeypatch.setattr(shell.pwd, "getpwuid", lambda uid: entry(path) if uid == os.getuid() else None)
    assert shell.login_shell() == path


@pytest.mark.parametrize("name", ["tcsh", "dash", "sh", "nologin", "false"])
def test_other_shells_get_bash(monkeypatch, runnable, name):
    path = runnable(name)
    monkeypatch.setattr(shell.pwd, "getpwnam", lambda user: entry(path))
    assert shell.login_shell("me") == "bash"


@pytest.mark.parametrize("path", ["", "zsh", "/no/such/zsh", "/odd dir/zsh", "/x;y/bash"])
def test_odd_or_missing_shells_get_bash(monkeypatch, tmp_path, path):
    monkeypatch.setattr(shell.pwd, "getpwnam", lambda user: entry(path))
    assert shell.login_shell("me") == "bash"


def test_not_runnable_gets_bash(monkeypatch, tmp_path):
    path = tmp_path / "zsh"
    path.write_text("")
    path.chmod(0o644)
    monkeypatch.setattr(shell.pwd, "getpwnam", lambda user: entry(str(path)))
    assert shell.login_shell("me") == "bash"


def test_no_such_user_gets_bash(monkeypatch):
    def missing(user):
        raise KeyError(user)

    monkeypatch.setattr(shell.pwd, "getpwnam", missing)
    assert shell.login_shell("nobody-here") == "bash"


UBUNTU_BASHRC = """\
# Ubuntu's ~/.bashrc starts like this: nothing below runs unless interactive.
case $- in
    *i*) ;;
      *) return;;
esac
export PATH="$HOME/nvm-bin:$PATH"
"""


def test_the_launcher_gets_what_bashrc_adds(tmp_path):
    """Why it's interactive: nvm and the like add themselves at the end of
    ~/.bashrc, past the check that stops a shell that isn't."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('[ -n "$BASH_VERSION" ] && [ -f ~/.bashrc ] && . ~/.bashrc\n')
    (home / ".bashrc").write_text(UBUNTU_BASHRC)
    launcher = tmp_path / "launcher"
    launcher.write_text('#!/bin/sh\nprintf %s "$PATH" > "$HOME/path"\n')
    launcher.chmod(0o755)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TERM": "dumb"}
    done = subprocess.run(
        shell.argv("bash", str(launcher)), env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    assert (home / "path").read_text().split(":")[0] == f"{home}/nvm-bin"


def test_a_failing_launcher_leaves_a_shell(tmp_path):
    """`|| exec bash`: the tab stays open to show why."""
    script = shell.script("bash", "false")
    env = {"PATH": "/usr/bin:/bin"}
    done = subprocess.run(["bash", "-c", script], input=b"echo still-here\n", capture_output=True, timeout=30, env=env)
    assert b"still-here" in done.stdout
