"""The real launcher, fzf and session hook, in a pseudo-terminal, with a fake claude."""

from __future__ import annotations

import os
import shutil

import pytest

from claude_launcher.paths import REPO_DIR
from conftest import conversation
from term import Term

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not shutil.which("fzf"), reason="needs fzf"),
]

LAUNCHER = REPO_DIR / "bin" / "claude-launcher"
HOOK = REPO_DIR / "bin" / "claude-session-hook"

FAKE_CLAUDE = """#!/usr/bin/env bash
printf '%s\\t%s\\n' "$PWD" "$*" >> "$CLAUDE_CALLS"
echo "fake claude in $PWD"
sleep 0.3
"""


@pytest.fixture
def launch(claude_home, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "fzf").symlink_to(shutil.which("fzf"))  # the fzf on the test run's PATH, to test other versions
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    calls = tmp_path / "calls"
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = {
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(claude_home.home),
        "CLAUDE_CONFIG_DIR": str(claude_home.paths.config),
        "CLAUDE_CALLS": str(calls),
        "XDG_RUNTIME_DIR": str(runtime),
        "TERM": "xterm-256color",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    terms = []

    def start(*args: str) -> Term:
        t = Term([str(LAUNCHER), *args], env, cwd=str(claude_home.home))
        terms.append(t)
        return t

    def claude_calls() -> list[tuple[str, str]]:
        if not calls.exists():
            return []
        return [tuple(line.split("\t", 1)) for line in calls.read_text().splitlines()]

    start.calls = claude_calls
    start.runtime = runtime
    yield start
    for t in terms:
        t.close()


def test_enter_opens_home_then_comes_back(launch, claude_home):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("enter")
    t.wait_for("fake claude in")
    t.wait_for("Select a project directory for Claude")
    assert launch.calls() == [(str(claude_home.home), "")]
    t.send("esc")
    t.wait_for("esc again to quit")
    t.send("esc")
    assert t.wait_exit() == 0


def test_hint_comes_back_after_a_second(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("esc")
    t.wait_for("esc again to quit")
    t.wait_for("enter open", timeout=3)  # put back through fzf's --listen socket


def test_typed_path(launch, tmp_path):
    target = tmp_path / "some project"
    target.mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type(str(target))
    t.wait_for("typed path")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(target), "")]


def test_not_a_directory(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("/no/such/dir")
    t.send("enter")
    t.wait_for("Not a directory: /no/such/dir")
    t.wait_for("Select a project directory for Claude")
    assert launch.calls() == []


def test_resume_from_the_sessions_view(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = claude_home.log(proj, conversation("the session to resume", proj))
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("tab")
    t.wait_for("the session to resume")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(proj), f"--resume {sid}")]


def test_tab_and_esc_go_back(launch, claude_home):
    claude_home.log("/tmp", conversation("hello", "/tmp"))
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("tab")
    t.wait_for("Recent Claude sessions")
    t.send("esc")
    t.wait_for("Select a project directory for Claude")


def test_options_pass_through(launch, claude_home):
    t = launch("--model", "opus")
    t.wait_for("Select a project directory for Claude")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(claude_home.home), "--model opus")]


def test_resume_on_the_command_line(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = claude_home.log(proj, conversation("x", proj))
    t = launch("--resume", sid)
    t.wait_for("fake claude in")
    assert t.wait_exit() == 0
    assert launch.calls() == [(str(proj), f"--resume {sid}")]


def test_resume_unknown_session_on_the_command_line(launch):
    t = launch("-r", "0b2f0000-0000-0000-0000-000000000000")
    t.wait_for("No session with ID")
    assert t.wait_exit() == 1


def test_colors_switched_and_restored(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("esc", "esc")
    t.wait_exit()
    raw = bytes(t.raw)
    asked = raw.find(b"\x1b]11;?")
    applied = raw.find(b"\x1b]11;#1f1e1d")
    reset = raw.rfind(b"\x1b]111\x1b\\")
    assert 0 <= asked < applied < reset
    assert b"\x1b]4;1;#f2545b" in raw[asked:reset]


def test_cleans_up_its_run_directory(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    assert list(launch.runtime.iterdir())
    t.send("esc", "esc")
    t.wait_exit()
    assert list(launch.runtime.iterdir()) == []
