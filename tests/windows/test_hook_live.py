"""Windows' hook and launcher run as Claude and a terminal run them: cmd,
the .cmd (which finds Python), its entry script, then the repo's script.
From user directories with the characters cmd and code pages trip on."""

import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

from claude_launcher.platforms.windows import data_dir
from claude_launcher.platforms.windows import setup as windows_setup
from claude_launcher.platforms.windows.host import WindowsHost
from claude_launcher.platforms.windows.setup import WindowsSetup
from claude_launcher.restart import Record

SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
NAMES = ["Jo Smith", "Jo (work) [x]", "A&B", "a^b", "100%", "x!y!", "José Ünal", "Đ с ↻", "A&B ^1 100% !x! (é)"]


def libuv_join(argv):
    """A command line as Claude (Bun, libuv) builds one: an argument is quoted
    only if it has a space, tab or quote, with backslashes doubled before a
    quote and at the end."""
    out = []
    for arg in argv:
        if arg and not any(c in arg for c in ' \t"'):
            out.append(arg)
            continue
        quoted, slashes = ['"'], 0
        for ch in arg:
            if ch == "\\":
                slashes += 1
                continue
            quoted.append("\\" * (slashes * 2 + 1) + '"' if ch == '"' else "\\" * slashes + ch)
            slashes = 0
        quoted.append("\\" * (slashes * 2) + '"')
        out.append("".join(quoted))
    return " ".join(out)


def test_libuv_join():
    assert libuv_join(["a", "b c", 'q"', "d\\", "e f\\"]) == 'a "b c" "q\\"" d\\ "e f\\\\"'


def user(tmp_path, name):
    home = tmp_path / name
    env = {
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "SystemRoot": os.environ["SYSTEMROOT"],
        # This test's Python first, so the .cmd finds one.
        "PATH": os.pathsep.join([os.path.dirname(sys.executable), os.environ["PATH"]]),
    }
    return env


@pytest.mark.parametrize("name", NAMES)
def test_the_hook_from_any_users_directory(tmp_path, name, monkeypatch):
    env = user(tmp_path, name)
    # The repo in such a directory too: its path is only in the entry script.
    repo_hook = tmp_path / name / "repo" / "claude-session-hook.py"
    repo_hook.parent.mkdir(parents=True)
    shutil.copyfile(windows_setup.HOOK, repo_hook)
    monkeypatch.setattr(windows_setup, "HOOK", repo_hook)
    setup = WindowsSetup(data_dir(env), env=env)
    list(setup.install(None, None))
    config = tmp_path / name / ".claude"
    env |= {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG_DIR": str(config), "CLAUDE_PID": "4242"}
    payload = json.dumps({"session_id": SID, "hook_event_name": "SessionStart", "cwd": str(tmp_path / name)})
    # settings.json's argv, through the command line Claude would build.
    line = libuv_join([*setup.hook_command(), "start"])
    done = subprocess.run(line, input=payload.encode(), env=env, capture_output=True, timeout=20)
    assert done.returncode == 0, (line, done.stdout, done.stderr)
    [path] = (config / "state" / "claude-launcher" / "open").iterdir()
    record = Record.load(path)
    assert record.boot_id == WindowsHost().boot_id() and record.pid == 4242 and record.session_id == SID


@pytest.mark.parametrize("name", NAMES)
def test_the_launcher_from_any_users_directory(tmp_path, name):
    # Typed in cmd: the shim on PATH, then the data directory's .cmd, then
    # bin/claude-launcher, which says there's no such session and stops.
    env = user(tmp_path, name)
    setup = WindowsSetup(data_dir(env), env=env)
    list(setup.install(None, None))
    env["PATH"] = os.pathsep.join([str(tmp_path / name / ".local" / "bin"), env["PATH"]])
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / name / ".claude")
    missing = str(uuid.uuid4())
    cmd = os.path.join(os.environ["SYSTEMROOT"], "System32", "cmd.exe")
    done = subprocess.run(
        f'"{cmd}" /d /c claude-launcher --resume {missing}',
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert done.returncode == 1 and f"No session with ID {missing}" in done.stderr, (done.stdout, done.stderr)
