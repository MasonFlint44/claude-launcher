"""The launcher's loop and how it starts Claude, in-process with fakes.
test_e2e.py runs the real thing in a pseudo-terminal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from claude_launcher import launcher, platforms
from claude_launcher.picker import FzfTooOld, Pick
from conftest import conversation


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

    def run(self, argv, started=None, **kwargs):
        self.runs.append((argv, kwargs))
        if started:
            started()
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
        self.pending, self.in_child = None, False

    def run(self, argv, started=None, **kwargs):
        self.env = kwargs.get("env")
        return launcher._Signals.run(self, argv, started, **kwargs)


def test_claude_not_on_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(launcher, "_claude_command", lambda args: ["claude", *args])
    assert launcher._claude(RunsForReal(), str(tmp_path), []) is None
    assert capsys.readouterr().err.strip() == "claude isn't on your PATH"


def test_no_such_directory(tmp_path, capsys):
    gone = str(tmp_path / "gone")
    assert launcher._claude(RunsForReal(), gone, []) is None
    assert capsys.readouterr().err.strip() == f"No such directory: {gone}"


# A claude that waits, up to 5 s, for a file: exits 3 if it came, 4 if not.
WAITS_FOR = """
import os, sys, time
end = time.time() + 5
while not os.path.exists(sys.argv[1]) and time.time() < end:
    time.sleep(0.02)
sys.exit(3 if os.path.exists(sys.argv[1]) else 4)
"""


def test_started_while_claude_runs(monkeypatch, tmp_path):
    release = tmp_path / "release"
    monkeypatch.setattr(launcher, "_claude_command", lambda args: [sys.executable, "-c", WAITS_FOR, str(release)])
    calls = []

    def started():
        calls.append(1)
        release.touch()  # only a claude still running sees it

    assert launcher._claude(RunsForReal(), str(tmp_path), [], started) == 3
    assert calls == [1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_claude_killed_by_a_signal_exits_as_a_shell_would(monkeypatch, tmp_path):
    kill = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"]
    monkeypatch.setattr(launcher, "_claude_command", lambda args: kill)
    assert launcher._claude(RunsForReal(), str(tmp_path), []) == 128 + 9
    assert launcher._claude(RunsForReal(), str(tmp_path), [], lambda: None) == 128 + 9


def test_not_started_when_claude_cant_start(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(launcher, "_claude_command", lambda args: ["claude", *args])
    calls = []
    assert launcher._claude(RunsForReal(), str(tmp_path), [], lambda: calls.append(1)) is None
    assert launcher._claude(RunsForReal(), str(tmp_path / "gone"), [], lambda: calls.append(1)) is None
    assert calls == []


class CapturesArgv:
    """subprocess.run and Popen as _Signals.run calls them: records argv."""

    def __init__(self, monkeypatch):
        self.argv = []
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr(subprocess, "Popen", self.popen)

    def run(self, argv, **kwargs):
        self.argv.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    def popen(self, argv, **kwargs):
        self.argv.append(argv)
        return FakeChild(argv)


class FakeChild:
    def __init__(self, args):
        self.args = args

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        return 0


@pytest.mark.parametrize("started", [None, lambda: None], ids=["run", "popen"])
def test_a_command_line_reaches_subprocess_as_it_is(monkeypatch, started):
    # npm's claude.cmd on Windows: one string for cmd, not a list of its characters.
    captured = CapturesArgv(monkeypatch)
    line = '"C:\\Windows\\System32\\cmd.exe" /d /e:on /v:off /s /c ""C:\\npm\\claude.cmd" "-c""'
    launcher._Signals.run(RunsForReal(), line, started)
    launcher._Signals.run(RunsForReal(), ("claude", "-c"), started)
    assert captured.argv == [line, ["claude", "-c"]]


class FakeRestart:
    dropped: list = []

    def __init__(self, paths):
        pass

    def drop(self, keys):
        FakeRestart.dropped.append(list(keys))


@pytest.fixture
def restart(monkeypatch):
    FakeRestart.dropped = []
    monkeypatch.setattr(launcher, "RestartState", FakeRestart)
    return FakeRestart


def test_a_resumed_session_leaves_the_group_once_claude_starts(loop, restart, tmp_path):
    FakePicker.result = Pick(str(tmp_path), "sid", ["sid", "older"])
    loop(0, 0, launcher.QUIT)
    assert restart.dropped == [["sid", "older"]]


def test_a_session_not_in_the_group_leaves_its_file_alone(loop, restart, tmp_path):
    FakePicker.result = Pick(str(tmp_path), "sid")
    loop(0, 0, launcher.QUIT)
    assert restart.dropped == []  # no lock taken, no rewrite
    assert launcher._ungroup([]) is None


def test_a_session_that_cant_start_stays_in_the_group(loop, restart, tmp_path):
    FakePicker.result = Pick(str(tmp_path / "gone"), "sid", ["sid"])
    loop(0, launcher.QUIT)
    assert restart.dropped == []


def test_leaving_the_group_failing_doesnt_stop_claude(loop, restart, tmp_path, monkeypatch):
    def fail(self, keys):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(FakeRestart, "drop", fail)
    FakePicker.result = Pick(str(tmp_path), "sid", ["sid"])
    code, signals = loop(0, 0, launcher.QUIT)
    assert code == 0 and len(signals.runs) == 3


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

    def test_windows_not_found_never_runs_it_by_name(self):
        # By its bare name, CreateProcess would look in the current directory first.
        with pytest.raises(FileNotFoundError) as e:
            launcher._claude_command(["-c"], which=lambda name: None, windows=True)
        assert e.value.filename == "claude"

    def test_windows_never_takes_one_from_the_current_directory(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / "claude.cmd").write_text("")
        monkeypatch.chdir(project)
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        with pytest.raises(FileNotFoundError):
            launcher._claude_command(["-c"], windows=True)
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "claude.exe").write_text("")
        assert launcher._claude_command(["-c"], windows=True) == [str(tmp_path / "bin" / "claude.exe"), "-c"]

    def test_windows_not_found_says_so(self, monkeypatch, tmp_path, capsys):
        real = launcher._claude_command
        monkeypatch.setattr(launcher, "_claude_command", lambda args: real(args, which=lambda n: None, windows=True))
        assert launcher._claude(FakeSignals(0), str(tmp_path), []) is None
        assert capsys.readouterr().err == "claude isn't on your PATH\n"

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


@pytest.mark.parametrize("arg", ["--help", "-h"])
def test_help(monkeypatch, capsys, arg):
    monkeypatch.setattr(platforms, "_current", None)
    monkeypatch.setattr(sys, "platform", "darwin")  # it says how to run it anywhere
    assert launcher.main([arg, "--model", "opus"]) == 0
    assert capsys.readouterr().out == launcher.USAGE + "\n"


def test_version(capsys):
    from claude_launcher import __version__

    assert launcher.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith(f"claude-launcher {__version__}")


def test_version_without_a_commit(monkeypatch, tmp_path):
    from claude_launcher import __version__, paths

    monkeypatch.setattr(paths, "REPO_DIR", tmp_path)
    assert launcher.version() == __version__  # not a clone
    (tmp_path / ".git").mkdir()
    assert launcher.version() == __version__  # git can't read it
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert launcher.version() == __version__  # no git


def test_help_names_the_short_options():
    assert "claude-launcher -r <id>" in launcher.USAGE and "--help, -h" in launcher.USAGE


@pytest.mark.parametrize(
    ("described", "shown"),
    [
        ("v{v}", "{v}"),  # exactly the release: no need to say it twice
        ("v{v}-dirty", "{v} (v{v}-dirty)"),
        ("v{v}-3-gabc1234", "{v} (v{v}-3-gabc1234)"),
        ("v0.0.9", "{v} (v0.0.9)"),  # another release's tag
        ("abc1234", "{v} (abc1234)"),  # no tags at all
    ],
)
def test_version_from_git(monkeypatch, tmp_path, described, shown):
    from claude_launcher import __version__, paths

    monkeypatch.setattr(paths, "REPO_DIR", tmp_path)
    (tmp_path / ".git").mkdir()
    out = described.format(v=__version__) + "\n"
    monkeypatch.setattr(launcher, "_which", lambda name, windows: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=out))
    assert launcher.version() == shown.format(v=__version__)


def test_version_on_windows_never_runs_git_from_the_current_directory(monkeypatch, tmp_path):
    from claude_launcher import __version__, paths

    monkeypatch.setattr(paths, "REPO_DIR", tmp_path / "repo")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    (project / "git.bat").write_text("")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    ran = []
    out = subprocess.CompletedProcess([], 0, stdout="abc1234\n")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv) or out)
    assert launcher.version(windows=True) == __version__  # no git but the planted one: no commit
    assert ran == []
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "git.exe").write_text("")
    assert launcher.version(windows=True) == f"{__version__} (abc1234)"
    assert ran[0][0] == str(tmp_path / "bin" / "git.exe")


def test_help_after_another_argument_goes_to_claude(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(launcher.signal, "signal", lambda *a: None)
    monkeypatch.setattr(launcher, "_theme", lambda run, action: None)
    passed = []
    monkeypatch.setattr(launcher, "_loop", lambda signals, run, args: passed.append(args) or 0)
    assert launcher.main(["--model", "opus", "--help"]) == 0
    assert launcher.main(["--append-system-prompt", "--version"]) == 0
    assert passed == [["--model", "opus", "--help"], ["--append-system-prompt", "--version"]]


@pytest.fixture
def resume(claude_home, monkeypatch, tmp_path):
    """main() with `--resume <id>`, a session in its own directory, and a
    claude that runs until it's taken out of the group (WAITS_FOR), or `command`.
    The file is touched just after the group's, not checked by claude itself:
    on Windows an open file can't be replaced."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(launcher.signal, "signal", lambda *a: None)
    monkeypatch.setattr(launcher, "_theme", lambda run, action: None)
    proj = tmp_path / "proj"
    proj.mkdir()
    release = tmp_path / "release"
    real = launcher._ungroup

    def ungroup(keys):
        drop = real(keys)

        def started():
            if drop:
                drop()
                release.touch()

        return started

    monkeypatch.setattr(launcher, "_ungroup", ungroup)

    def run(sid=None, command=None):
        sid = sid or claude_home.log(proj, conversation("x", proj))
        monkeypatch.setattr(
            launcher, "_claude_command", lambda args: command or [sys.executable, "-c", WAITS_FOR, str(release)]
        )
        return launcher.main(["--resume", sid])

    run.proj = proj
    return run


def group(claude_home, *sids):
    claude_home.paths.state.mkdir(parents=True, exist_ok=True)
    closed = {"at": time.time() - 3600, "why": "restart"}
    claude_home.paths.restored.write_text(json.dumps({sid: closed for sid in sids}))


def in_group(claude_home):
    return set(json.loads(claude_home.paths.restored.read_text()))


def test_resume_takes_the_session_out_of_the_group_while_claude_runs(resume, claude_home):
    sid = claude_home.log(resume.proj, conversation("x", resume.proj))
    other = claude_home.log(resume.proj, conversation("y", resume.proj))
    group(claude_home, sid, other)
    assert resume(sid) == 3  # the group lost it while claude ran
    assert in_group(claude_home) == {other}


def test_resume_takes_out_the_session_it_continues(resume, claude_home):
    newer = claude_home.log(resume.proj, conversation("newer", resume.proj))
    entries = [*conversation("older", resume.proj), {"type": "continued-in", "continuedInSessionId": newer}]
    older = claude_home.log(resume.proj, entries)
    group(claude_home, older)
    assert resume(newer) == 3
    assert in_group(claude_home) == set()


def test_resume_that_cant_start_stays_in_the_group(resume, claude_home, tmp_path, capsys):
    sid = claude_home.log(resume.proj, conversation("x", resume.proj))
    group(claude_home, sid)
    assert resume(sid, [str(tmp_path / "no-claude")]) == 1
    assert in_group(claude_home) == {sid}


def test_resume_outside_the_group_leaves_its_file_alone(resume, claude_home):
    group(claude_home)  # "{}", with no newline: a rewrite would add one
    assert resume(command=[sys.executable, "-c", ""]) == 0
    assert claude_home.paths.restored.read_text() == "{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_resume_killed_by_a_signal_exits_as_a_shell_would(resume):
    kill = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"]
    assert resume(command=kill) == 128 + 9


def test_resume_then_help_still_resumes(claude_home, capsys):
    assert launcher.main(["--resume", "0b2f0000-0000-0000-0000-000000000000", "--help"]) == 1
    assert capsys.readouterr().err.strip() == "No session with ID 0b2f0000-0000-0000-0000-000000000000"
