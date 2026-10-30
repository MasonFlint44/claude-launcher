"""The launcher's loop and how it starts Claude, in-process with fakes.
test_e2e.py runs the real thing in a pseudo-terminal."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from claude_launcher import launcher, platforms
from claude_launcher.picker import FzfTooOld, Pick


class FakePicker:
    """Picker's start() and pick(), as the loop uses them."""

    started = 0
    result: Pick | str = Pick("/")
    too_old = False

    def __init__(self, run):
        self.run = run

    def start(self, cols, again=False):
        if FakePicker.too_old:
            raise FzfTooOld("claude-launcher needs fzf 0.67 or newer")
        FakePicker.started += 1
        (self.run / "rows").write_text("")
        return ["fzf"]

    def pick(self, query):
        return FakePicker.result


class FakeSignals:
    """Answers each run with the next of `codes`; records what was run."""

    def __init__(self, *codes):
        self.codes = list(codes)
        self.runs = []

    def run(self, argv, **kwargs):
        self.runs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.codes.pop(0), stdout=b"\n")


@pytest.fixture
def loop(monkeypatch, tmp_path):
    FakePicker.started, FakePicker.result, FakePicker.too_old = 0, Pick(str(tmp_path)), False
    monkeypatch.setattr(launcher, "Picker", FakePicker)
    monkeypatch.setattr(launcher, "_set_title", lambda: None)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)

    def run(*codes):
        signals = FakeSignals(*codes)
        return launcher._loop(signals, tmp_path, []), signals

    return run


@pytest.mark.parametrize("signum", [9, 15])
def test_fzf_killed_by_a_signal_ends_the_launcher(loop, signum):
    code, signals = loop(-signum)
    assert code == 128 + signum
    assert len(signals.runs) == 1  # no claude


def test_fzf_failing_ends_the_launcher(loop):
    code, signals = loop(2)
    assert code == 2 and len(signals.runs) == 1


def test_fzf_quitting_ends_the_launcher_cleanly(loop):
    assert loop(launcher.QUIT)[0] == 0


def test_a_pick_runs_claude_then_the_picker_again(loop, tmp_path):
    code, signals = loop(0, 0, launcher.QUIT)
    assert code == 0
    assert [kwargs.get("cwd") for _, kwargs in signals.runs] == [None, str(tmp_path), None]
    assert FakePicker.started == 2


def test_fzf_too_old(loop, capsys):
    FakePicker.too_old = True
    assert loop()[0] == 1
    assert "needs fzf 0.67 or newer" in capsys.readouterr().err


class RunsForReal:
    def __init__(self):
        self.env = None

    def run(self, argv, **kwargs):
        self.env = kwargs.get("env")
        return subprocess.run(argv, **kwargs)


def test_claude_not_on_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(launcher, "_claude_command", lambda args: ["claude", *args])
    assert launcher._claude(RunsForReal(), str(tmp_path), []) is None
    assert capsys.readouterr().err.strip() == "claude isn't on your PATH"


def test_no_such_directory(tmp_path, capsys):
    gone = str(tmp_path / "gone")
    assert launcher._claude(RunsForReal(), gone, []) is None
    assert capsys.readouterr().err.strip() == f"No such directory: {gone}"


@pytest.mark.skipif(os.name == "nt", reason="PWD is POSIX's")
def test_claude_gets_its_directory_as_pwd(monkeypatch, tmp_path):
    monkeypatch.setenv("PWD", "/somewhere/else")
    signals = FakeSignals(0)
    launcher._claude(signals, str(tmp_path), [])
    assert signals.runs[0][1]["env"]["PWD"] == str(tmp_path)


class TestClaudeCommand:
    def test_posix_runs_claude_by_name(self):
        assert launcher._claude_command(["-c"], which=lambda name: "/x/claude", windows=False) == ["claude", "-c"]

    def test_windows_exe_by_its_path(self):
        found = "C:\\Users\\me\\.local\\bin\\claude.exe"
        assert launcher._claude_command(["-c"], which=lambda name: found, windows=True) == [found, "-c"]

    def test_windows_not_found_is_left_to_fail(self):
        assert launcher._claude_command(["-c"], which=lambda name: None, windows=True) == ["claude", "-c"]

    def test_windows_npm_cmd_through_cmd(self):
        found = "C:\\Users\\me\\AppData\\Roaming\\npm\\claude.CMD"
        line = launcher._claude_command(
            ["--resume", "abc"], which=lambda name: found, windows=True, comspec="C:\\Windows\\cmd.exe"
        )
        assert line == f'"C:\\Windows\\cmd.exe" /d /e:on /v:off /s /c ""{found}" "--resume" "abc""'

    def test_windows_line_breaks_refused(self):
        with pytest.raises(ValueError, match="line break"):
            launcher._claude_command(["a\nb"], which=lambda name: "C:\\npm\\claude.cmd", windows=True)


@pytest.mark.parametrize(
    ("arg", "quoted"),
    [
        ("abc", '"abc"'),
        ("", '""'),
        ("a b&c|d<e>f^g!h", '"a b&c|d<e>f^g!h"'),  # all inside quotes cmd keeps
        ('say "hi"', '"say ""hi"""'),  # cmd never sees an unquoted stretch
        ('a\\"b', '"a\\\\""b"'),  # backslashes before a quote doubled
        ("C:\\x\\", '"C:\\x\\\\"'),  # and before the closing one
        ("%PATH%", '"%%cd:~,%PATH%%cd:~,%"'),  # no variable can start
    ],
)
def test_batch_arg(arg, quoted):
    assert launcher._batch_arg(arg) == quoted


def test_unsupported_os(monkeypatch, capsys):
    monkeypatch.setattr(platforms, "_current", None)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert launcher.main([]) == 1
    assert capsys.readouterr().err.strip() == "claude-launcher doesn't run on darwin yet"


def test_utf8_out_skips_missing_streams(monkeypatch):
    # pythonw, or a process started detached, has no stdout or stderr.
    from claude_launcher import text

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    text.utf8_out()
