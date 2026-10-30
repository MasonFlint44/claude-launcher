"""The real launcher, fzf and session hook, in a pseudo-terminal, with a fake claude."""

from __future__ import annotations

import os
import shutil
import signal

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
if [[ -n ${CLAUDE_HOLD:-} ]]; then  # stay until that file exists, like a Claude in use
  trap 'echo "claude got ctrl-c"' INT
  until [[ -e $CLAUDE_HOLD ]]; do sleep 0.05; done
fi
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
    # The launcher's Python runs through `env python3`; log each run.
    core_calls = tmp_path / "core-calls"
    system_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{core_calls}"\nexec {system_python} "$@"\n')
    python.chmod(0o755)
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

    def term(argv: list[str], **more_env: str) -> Term:
        t = Term(argv, {**env, **more_env}, cwd=str(claude_home.home))
        terms.append(t)
        return t

    def start(*args: str, **more_env: str) -> Term:
        return term([str(LAUNCHER), *args], **more_env)

    def claude_calls() -> list[tuple[str, str]]:
        if not calls.exists():
            return []
        return [tuple(line.split("\t", 1)) for line in calls.read_text().splitlines()]

    def core_runs(action: str) -> int:
        """How many times the launcher's Python ran `ui <action>`."""
        if not core_calls.exists():
            return 0
        return sum(f" ui {action}" in line for line in core_calls.read_text().splitlines())

    start.term = term
    start.calls = claude_calls
    start.core_runs = core_runs
    start.runtime = runtime
    yield start
    for t in terms:
        t.close()


def quit_with_esc(t: Term) -> None:
    # Wait for the first press to show before the second: two Escs that reach
    # fzf together count as one press, and a busy machine can delay the first.
    t.send("esc")
    t.wait_for("esc again to quit")
    t.send("esc")
    assert t.wait_exit() == 0


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


PORT_LAUNCHER = """\
import dataclasses, sys
sys.path.insert(0, {src!r})
from claude_launcher.platforms import linux
plain = linux.platform
linux.platform = lambda: dataclasses.replace(plain(), fzf_listen="port")
from claude_launcher.launcher import main
sys.exit(main(sys.argv[1:]))
"""


def test_hint_comes_back_through_a_port(launch, tmp_path):
    # Windows' way: fzf on a localhost port with a key, as Linux's platform.
    script = tmp_path / "port-launcher"
    script.write_text(PORT_LAUNCHER.format(src=str(REPO_DIR / "src")))
    t = launch.term(["python3", str(script)])
    t.wait_for("Select a project directory for Claude")
    t.send("esc")
    t.wait_for("esc again to quit")
    t.wait_for("enter open", timeout=3)
    t.send("esc")
    t.wait_for("esc again to quit")
    t.send("esc")
    assert t.wait_exit() == 0


def test_typed_path(launch, claude_home):
    target = claude_home.home / "some project"
    target.mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/some project")
    # Each key typed in a path runs the launcher twice (the title, then the
    # list), about 45 ms in all: quicker than anyone types.
    t.wait_for("> ~/some project")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(target), "")]


def test_pasted_path_runs_the_launcher_once(launch, tmp_path):
    target = tmp_path / "a long directory name to paste"
    target.mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    before = launch.core_runs("change")
    t.paste(str(target))
    t.wait_for("> " + str(target))
    t.wait_for("Directories in")
    assert launch.core_runs("change") - before == 1
    t.send("enter")
    # Not "fake claude in": the test terminal has no alternate screen, so
    # claude's line starts where fzf left the cursor, after the long query, and wraps.
    t.wait_for("Select a project directory for Claude")
    assert launch.calls() == [(str(target), "")]


def test_path_mode_tab_completes_then_enter_opens(launch, claude_home):
    home = claude_home.home
    (home / "git" / "claude-launcher" / "src").mkdir(parents=True)
    (home / "git" / "command-center").mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/git/cl")
    t.wait_for("tab complete")
    t.wait_gone("~/git/command-center")
    t.send("tab")
    t.wait_for("> ~/git/claude-launcher/")
    t.wait_for("~/git/claude-launcher/src")
    screen = t.wait_for("Directories in ~/git/claude-launcher/")
    assert screen.index("▌ ~/git/claude-launcher/\n") < screen.index("~/git/claude-launcher/src")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(home / "git" / "claude-launcher"), "")]


def test_path_mode_esc_backs_up_to_the_start_screen(launch, claude_home):
    (claude_home.home / "git" / "x").mkdir(parents=True)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/git/x")
    t.wait_for("> ~/git/x")
    t.send("esc")
    # The trailing space: "> ~/git/x" contains "> ~/git/". Two Escs that reach
    # fzf together count as one, so wait until the first has done its part.
    t.wait_for("> ~/git/ ")
    t.wait_for("Directories in ~/git/ ")
    t.send("esc")
    t.wait_for("Directories in ~/ ")
    t.send("esc")
    t.wait_for("esc quit")
    t.wait_for("Select a project directory for Claude")
    t.send("esc")
    t.wait_for("esc again to quit")
    t.send("esc")
    assert t.wait_exit() == 0


def test_ctrl_u_clears_a_path(launch, claude_home):
    (claude_home.home / "git" / "x").mkdir(parents=True)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/git/x")
    t.wait_for("ctrl-u clear")
    t.send("ctrl-u")
    t.wait_for("esc quit")
    t.wait_gone("~/git")


def test_a_path_never_opens_a_project_it_doesnt_name(launch, claude_home):
    proj = claude_home.home / "git" / "command-center"
    proj.mkdir(parents=True)
    claude_home.log(proj, conversation("x", proj))
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/git/cmdx")
    t.settle(0.5)
    t.send("enter")
    t.wait_for("Not a directory: ~/git/cmdx")
    assert launch.calls() == []


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
    t.send("ctrl-s")
    t.wait_for("the session to resume")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(proj), f"--resume {sid}")]


def test_ctrl_s_and_esc_go_back(launch, claude_home):
    claude_home.log("/tmp", conversation("hello", "/tmp"))
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("ctrl-s")
    t.wait_for("Recent Claude sessions")
    t.send("ctrl-s")
    t.wait_for("Select a project directory for Claude")
    t.send("ctrl-s")
    t.wait_for("Recent Claude sessions")
    t.send("esc")
    t.wait_for("Select a project directory for Claude")


def project_with_a_session(claude_home, path, title="an earlier session"):
    path.mkdir(parents=True, exist_ok=True)
    return claude_home.log(path, conversation(title, path))


def test_enter_shows_a_directorys_sessions_and_enter_again_starts_a_new_one(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    project_with_a_session(claude_home, proj)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("proj")
    t.send("enter")
    t.wait_for("Claude sessions in")
    t.wait_for("+ New session")
    t.wait_for("an earlier session")
    t.send("enter")
    t.wait_for("Select a project directory for Claude")
    assert launch.calls() == [(str(proj), "")]


def test_two_enters_at_once_start_a_new_session(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    project_with_a_session(claude_home, proj)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("proj")
    os.write(t.fd, b"\r\r")  # quicker than anyone presses Enter twice
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(proj), "")]


def test_enter_on_a_directory_without_sessions_opens_it(launch, claude_home):
    (claude_home.home / "fresh").mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/fresh")
    t.send("enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(claude_home.home / "fresh"), "")]


def test_resume_from_a_directorys_sessions(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    sid = project_with_a_session(claude_home, proj)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("proj")
    t.send("enter")
    t.wait_for("an earlier session")
    t.send("down", "enter")
    t.wait_for("Select a project directory for Claude")
    assert launch.calls() == [(str(proj), f"--resume {sid}")]


def test_a_subdirectorys_session_resumes_there(launch, claude_home):
    sub = claude_home.home / "proj" / "sub"
    sid = project_with_a_session(claude_home, sub, "in the subdirectory")
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("~/proj")
    t.send("enter")
    t.wait_for("Claude sessions in ~/proj")
    t.wait_for("proj/sub")
    t.send("down", "enter")
    t.wait_for("fake claude in")
    assert launch.calls() == [(str(sub), f"--resume {sid}")]


def test_esc_goes_back_to_the_same_row_and_search(launch, claude_home, tmp_path):
    for name in ("proj-a", "proj-b"):  # proj-b is newer, so it's first
        project_with_a_session(claude_home, tmp_path / name)
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("proj")
    t.send("down", "enter")
    t.wait_for("Claude sessions in")
    assert "proj-a" in t.text().splitlines()[0]
    t.send("esc")
    t.wait_for("Select a project directory for Claude")
    t.wait_for("proj-b")
    t.send("enter")  # on the same row again: proj-a, not the first row
    t.wait_for("Claude sessions in")
    assert "proj-a" in t.text().splitlines()[0]


def test_tab_completes_from_the_plain_list(launch, claude_home):
    project_with_a_session(claude_home, claude_home.home / "git" / "proj")
    (claude_home.home / "git" / "proj" / "src").mkdir()
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.type("proj")
    t.send("tab")
    t.wait_for("> ~/git/proj/")
    t.wait_for("~/git/proj/src")


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


def test_resume_on_the_command_line_uses_the_colors(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = claude_home.log(proj, conversation("x", proj))
    t = launch("--resume", sid)
    assert t.wait_exit() == 0
    raw = bytes(t.raw)
    assert 0 <= raw.find(b"\x1b]11;#1f1e1d") < raw.find(b"fake claude in") < raw.rfind(b"\x1b]111\x1b\\")


def test_resume_into_a_directory_it_cant_enter(launch, claude_home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = claude_home.log(proj, conversation("x", proj))
    proj.chmod(0o000)
    try:
        t = launch("--resume", sid)
        assert t.wait_exit() == 1
    finally:
        proj.chmod(0o755)
    assert launch.calls() == []
    assert "Select a project directory" not in t.text()


def test_resume_unknown_session_on_the_command_line(launch):
    t = launch("-r", "0b2f0000-0000-0000-0000-000000000000")
    t.wait_for("No session with ID")
    assert t.wait_exit() == 1


def test_colors_switched_and_restored(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    quit_with_esc(t)
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
    quit_with_esc(t)
    assert list(launch.runtime.iterdir()) == []


def test_tab_titled_while_picking_and_put_back(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    t.send("enter")
    t.wait_for("fake claude in")
    t.wait_for("Select a project directory for Claude")
    t.send("esc")
    t.wait_for("esc again to quit")
    t.send("esc")
    assert t.wait_exit() == 0
    raw = bytes(t.raw)
    named = b"\x1b]0;Claude Code\x1b\\"
    pushed, popped = raw.find(b"\x1b[22;0t"), raw.rfind(b"\x1b[23;0t")
    first = raw.find(named)
    again = raw.find(named, raw.find(b"fake claude in"))
    assert 0 <= pushed < first < again < popped


def test_ctrl_c_belongs_to_claude(launch, tmp_path):
    hold = tmp_path / "hold"
    t = launch(CLAUDE_HOLD=str(hold))
    t.wait_for("Select a project directory for Claude")
    t.send("enter")
    t.wait_for("fake claude in")
    t.send("ctrl-c")
    t.wait_for("claude got ctrl-c")
    hold.touch()
    t.wait_for("Select a project directory for Claude")  # the launcher is still there
    quit_with_esc(t)


def test_hang_up_leaves_the_terminal_alone(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    os.killpg(t.pid, signal.SIGHUP)  # as the kernel does when the terminal goes: fzf too
    assert t.wait_exit() == 129
    assert b"\x1b]111" not in bytes(t.raw)  # no colors put back
    assert list(launch.runtime.iterdir()) == []


def test_hang_up_while_claude_runs_waits_for_claude(launch, tmp_path):
    hold = tmp_path / "hold"
    t = launch(CLAUDE_HOLD=str(hold))
    t.wait_for("Select a project directory for Claude")
    t.send("enter")
    t.wait_for("fake claude in")
    os.kill(t.pid, signal.SIGHUP)  # the launcher alone: its Claude is still running
    t.settle(0.5)
    assert t.status is None
    hold.touch()
    assert t.wait_exit() == 129


def test_term_puts_the_colors_back(launch):
    t = launch()
    t.wait_for("Select a project directory for Claude")
    os.killpg(t.pid, signal.SIGTERM)
    assert t.wait_exit() == 143
    raw = bytes(t.raw)
    assert 0 <= raw.find(b"\x1b]11;#1f1e1d") < raw.rfind(b"\x1b]111\x1b\\")
    assert list(launch.runtime.iterdir()) == []


def test_fzf_killed_ends_the_launcher(launch, claude_home, tmp_path):
    # An fzf killed by a signal isn't a pick: nothing may start in ~.
    killer = tmp_path / "killer"
    killer.mkdir()
    fzf = killer / "fzf"
    fzf.write_text(f'#!/bin/sh\n[ "$1" = --version ] && exec {shutil.which("fzf")} "$@"\nkill -KILL $$\n')
    fzf.chmod(0o755)
    t = launch(PATH=f"{killer}:{tmp_path / 'bin'}:/usr/local/bin:/usr/bin:/bin")
    assert t.wait_exit() == 128 + signal.SIGKILL
    assert launch.calls() == []
