"""cmd itself: the core's command line as fzf runs it, the .cmd files'
choice of Python, and the fetched fzf.exe."""

import json
import os
import shutil
import subprocess
import sys
import time
import warnings

import pytest

from claude_launcher import fzf
from claude_launcher.platforms import windows
from claude_launcher.platforms.windows import setup as windows_setup

CMD = os.path.join(os.environ["SYSTEMROOT"], "System32", "cmd.exe")
HOSTILE = ["%PATH%", "!x!", "a&b", '"q"', "C:\\x\\", "↻", "with spaces", "^", "a|b<c>d", "(x)", "", "\\\\srv\\s\\"]


def test_core_command_round_trips_through_cmd(tmp_path, monkeypatch):
    # As fzf runs a binding: cmd /s/c "COMMAND".
    probe = tmp_path / "probe.py"
    probe.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    monkeypatch.setattr(windows, "CORE", probe)
    line = windows.core_command(HOSTILE)
    done = subprocess.run(
        f'"{CMD}" /d /s /c "{line}"', capture_output=True, text=True, encoding="utf-8", env={**os.environ}, timeout=20
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == HOSTILE


def venv(where):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(where)], check=True, timeout=120)
    return where / "Scripts"


@pytest.fixture(scope="module")
def pythons(tmp_path_factory):
    """Python stand-ins: a real one, an "old" one, and one in a WindowsApps
    directory, like the Microsoft Store's."""
    root = tmp_path_factory.mktemp("pythons")
    return {
        "real": venv(root / "real"),
        "old": venv(root / "old"),
        "store": venv(root / "WindowsApps"),
    }


@pytest.fixture
def probe(tmp_path):
    """A .cmd as the installer writes one, running a script that says which
    Python ran it, and acts too old under the "old" one."""
    (tmp_path / "probe.py").write_text(
        "import os, sys\n"
        f"if os.sep + 'old' + os.sep in sys.executable: sys.exit({windows_setup.TOO_OLD})\n"
        "print(sys.executable)\n",
        encoding="utf-8",
    )
    cmd = tmp_path / "probe.cmd"
    cmd.write_text(windows_setup.run_cmd("probe.py", "-I"), encoding="ascii", newline="")
    return cmd


def run_probe(cmd, *path, cwd=None, **more):
    env = {"SystemRoot": os.environ["SYSTEMROOT"], "PATH": os.pathsep.join(str(p) for p in path), **more}
    return subprocess.run(
        f'"{CMD}" /d /c "{cmd}"', env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=30
    )


def ran(done, which):
    assert done.returncode == 0, done.stderr
    return os.path.normcase(done.stdout.strip()).startswith(os.path.normcase(str(which.parent)))


def py_dir():
    found = shutil.which("py")
    if not found:
        pytest.skip("no py launcher here")
    return os.path.dirname(found)


def test_the_first_python_on_path(probe, pythons):
    assert ran(run_probe(probe, pythons["real"], pythons["store"]), pythons["real"])


def test_the_store_python_when_its_first_and_theres_no_py(probe, pythons):
    # Or the stand-in, whose own message says how to get Python.
    assert ran(run_probe(probe, pythons["store"], pythons["real"]), pythons["store"])


def test_py_before_the_store_python(probe, pythons):
    done = run_probe(probe, pythons["store"], py_dir())
    assert done.returncode == 0 and not ran(done, pythons["store"])


def test_too_old_then_py(probe, pythons):
    done = run_probe(probe, pythons["old"], py_dir())
    assert done.returncode == 0 and not ran(done, pythons["old"])


def test_py_without_a_python_then_the_store_python(probe, pythons):
    # A py launcher left behind by an uninstalled Python: here, one asked for
    # a 3.x that isn't there.
    none = {"PY_PYTHON3": "3.99"}
    env = {"SystemRoot": os.environ["SYSTEMROOT"], **none}
    py = os.path.join(py_dir(), "py.exe")
    if (
        subprocess.run([py, "-3", "-c", ""], env=env, capture_output=True, timeout=30).returncode
        != windows_setup.PY_NONE
    ):
        pytest.skip("this py launcher doesn't exit 103 for a missing Python")
    assert ran(run_probe(probe, pythons["store"], py_dir(), **none), pythons["store"])


def test_a_script_that_exits_103_runs_once(tmp_path, pythons):
    # py runs it and passes on its 103, which the .cmd must not take for py
    # having no Python: it would run it again with the store's python.exe.
    ran_log = tmp_path / "ran.txt"
    target = tmp_path / "target.py"
    target.write_text(
        f"import sys\nopen({str(ran_log)!r}, 'a').write('ran\\n')\nsys.exit({windows_setup.PY_NONE})\n",
        encoding="utf-8",
    )
    (tmp_path / "entry.py").write_text(windows_setup.entry_script(target), encoding="ascii")
    cmd = tmp_path / "entry.cmd"
    cmd.write_text(windows_setup.run_cmd("entry.py"), encoding="ascii", newline="")
    done = run_probe(cmd, pythons["store"], py_dir())
    assert done.returncode == windows_setup.RAN_INSTEAD, done.stderr
    assert ran_log.read_text() == "ran\n"


def test_the_cmds_own_variables_stay_out_of_pythons_environment(tmp_path, pythons):
    (tmp_path / "env.py").write_text(
        "import os, sys\n"
        f"if os.sep + 'old' + os.sep in sys.executable: sys.exit({windows_setup.TOO_OLD})\n"
        "print(sorted(k for k in os.environ if k.upper().startswith('CLAUDE_LAUNCHER_')))\n",
        encoding="utf-8",
    )
    cmd = tmp_path / "env.cmd"
    cmd.write_text(windows_setup.run_cmd("env.py"), encoding="ascii", newline="")
    # Run by each of the three: the first python.exe, py after a Python that
    # was too old, and the store's (reached only when it's the first python.exe).
    for path in ([pythons["real"]], [pythons["old"], py_dir()], [pythons["store"]]):
        done = run_probe(cmd, *path)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() in ("[]", "['CLAUDE_LAUNCHER_FALLBACK']")


def test_a_python_in_the_current_directory_isnt_taken(probe, pythons, tmp_path):
    # %~$PATH: may look in the current directory first; install.ps1's
    # Get-Command doesn't, so neither does the .cmd (it searches from its own).
    here = venv(tmp_path / "here")
    assert ran(run_probe(probe, pythons["real"], cwd=here), pythons["real"])
    done = run_probe(probe, tmp_path, cwd=here)  # and none on PATH: none at all
    assert done.returncode == 9009 and "needs Python 3.10 or newer" in done.stderr, (done.stdout, done.stderr)


def test_the_cmd_leaves_the_current_directory_as_it_was(tmp_path, pythons):
    # The hook and the launcher run in Claude's directory, not the .cmd's.
    (tmp_path / "cwd.py").write_text("import os\nprint(os.getcwd())\n", encoding="utf-8")
    cmd = tmp_path / "cwd.cmd"
    cmd.write_text(windows_setup.run_cmd("cwd.py"), encoding="ascii", newline="")
    here = tmp_path / "here (a&b)"
    here.mkdir()
    done = run_probe(cmd, pythons["real"], cwd=here)
    assert done.returncode == 0, done.stderr
    assert os.path.normcase(done.stdout.strip()) == os.path.normcase(str(here))


@pytest.fixture
def shim(tmp_path):
    """claude-launcher.cmd as on PATH, and where it hands over to: a .cmd
    that shows its arguments, under %USERPROFILE%\\AppData\\Local."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude-launcher.cmd").write_text(windows_setup.shim_cmd(), encoding="ascii", newline="")
    profile = tmp_path / "A&B 100%"
    target = profile / "AppData" / "Local" / "claude-launcher" / "claude-launcher.cmd"
    target.parent.mkdir(parents=True)
    target.write_text("@echo off\r\necho ran %*\r\nexit /b 7\r\n", encoding="ascii", newline="")
    return bin_dir / "claude-launcher.cmd", profile


def run_shim(cmd, **env):
    env = {"SystemRoot": os.environ["SYSTEMROOT"], **env}
    return subprocess.run(
        f'"{CMD}" /d /c ""{cmd}" --resume x"', env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
    )


def test_the_shim_without_localappdata_takes_the_profiles(shim):
    cmd, profile = shim
    done = run_shim(cmd, USERPROFILE=str(profile))
    assert done.returncode == 7 and done.stdout.strip() == "ran --resume x", (done.stdout, done.stderr)


def test_the_shim_with_localappdata(shim):
    cmd, profile = shim
    done = run_shim(cmd, LOCALAPPDATA=str(profile / "AppData" / "Local"), USERPROFILE="C:\\nowhere")
    assert done.returncode == 7 and done.stdout.strip() == "ran --resume x", (done.stdout, done.stderr)


@pytest.mark.parametrize("where", ["localappdata", "profile"])
def test_the_shim_with_nothing_to_hand_over_to(shim, tmp_path, where):
    cmd, _ = shim
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {"LOCALAPPDATA": str(empty)} if where == "localappdata" else {"USERPROFILE": str(empty)}
    done = run_shim(cmd, **env)
    assert done.returncode == 9009 and "isn't set up for this user; run install.ps1 again" in done.stderr
    assert "ran" not in done.stdout


def test_no_python_at_all(probe, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_probe(probe, empty)
    assert done.returncode == 9009 and "needs Python 3.10 or newer" in done.stderr


def test_too_old_and_nothing_else(tmp_path, pythons, monkeypatch):
    # The real entry script, against a Python it finds too old.
    monkeypatch.setattr(windows_setup, "MIN_PYTHON", (99, 0))
    target = tmp_path / "target.py"
    target.write_text("print('ran')\n", encoding="utf-8")
    (tmp_path / "entry.py").write_text(windows_setup.entry_script(target), encoding="ascii")
    cmd = tmp_path / "entry.cmd"
    cmd.write_text(windows_setup.run_cmd("entry.py"), encoding="ascii", newline="")
    done = run_probe(cmd, pythons["real"])
    assert done.returncode == windows_setup.TOO_OLD and "needs Python 99.0 or newer (found" in done.stderr


def test_a_running_cmd_replaced_with_other_text(tmp_path):
    # Why the installer leaves a .cmd that's unchanged alone: cmd reads a
    # running batch file a line at a time, from a byte offset, so an open
    # launcher tab could run part of a new one. Here the .cmd is replaced
    # while a command in it runs, and the new text has, at the old offset of
    # the next line, a line of its own. Which ran says what cmd did.
    sleep = f'"{sys.executable}" -c "import time; time.sleep(3)"'
    old = f"@echo off\r\n{sleep}\r\necho AFTER-OLD\r\n"
    new = f"@echo off\r\n{sleep}XXecho MIDLINE\r\necho AFTER-NEW\r\n"
    assert new.index("echo MIDLINE") == old.index("echo AFTER-OLD")
    cmd = tmp_path / "running.cmd"
    cmd.write_text(old, encoding="ascii", newline="")
    running = subprocess.Popen(
        f'"{CMD}" /d /c "{cmd}"', stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
    )
    time.sleep(1)
    tmp = tmp_path / "running.cmd.tmp"
    tmp.write_text(new, encoding="ascii", newline="")
    try:
        os.replace(tmp, cmd)
        replaced = "replaced"
    except PermissionError:
        replaced = "refused (cmd holds it open)"
    out, err = running.communicate(timeout=30)
    ran_after = out.split()
    known = {
        ("MIDLINE", "AFTER-NEW"): "carried on at its byte offset in the new file",
        ("AFTER-OLD",): "carried on in the old file",
        ("AFTER-NEW",): "carried on at the next line of the new file",
    }
    outcome = known.get(tuple(ran_after), f"ran {ran_after!r}, said {err.strip()!r}")
    warnings.warn(f"a running .cmd: {replaced}; cmd {outcome}", stacklevel=1)
    assert replaced == "replaced" or ran_after == ["AFTER-OLD"]


def test_fzf_find_takes_a_fetched_fzf_exe(tmp_path):
    system = shutil.which("fzf")
    if not system:
        pytest.skip("no fzf here")
    own = tmp_path / "bin"
    own.mkdir()
    shutil.copyfile(system, own / "fzf.exe")
    if fzf.version(str(own / "fzf.exe")) is None:
        pytest.skip(f"{system} doesn't run as a copy (a package manager's shim?)")
    path, version = fzf.find(own, env={"PATH": ""})
    assert path == str(own / "fzf.exe") and version >= fzf.MIN_VERSION


def test_fzf_find_takes_the_installed_one():
    own = fzf.private_dir(windows.data_dir(os.environ))
    if not (own / "fzf.exe").is_file():
        pytest.skip("no fetched fzf.exe")
    path, version = fzf.find(own, env={"PATH": ""})
    assert path == str(own / "fzf.exe") and version >= fzf.MIN_VERSION


def test_npms_claude_cmd_gets_its_arguments_as_they_were(tmp_path):
    # npm installs claude as claude.cmd; the launcher runs it through cmd.
    import json
    import subprocess
    import sys

    from claude_launcher import launcher

    d = tmp_path / "npm claude (x)"
    d.mkdir()
    show = d / "show.py"
    show.write_text("import json, sys; print(json.dumps(sys.argv[1:]))")
    cmd = d / "claude.cmd"
    cmd.write_text(f'@echo off\r\n"{sys.executable}" "{show}" %*\r\n', newline="")
    args = ["--resume", "a b", "x&y", "100%", '"q"', "^caret", "!x!", "%PATH%", "C:\\x\\", "end\\\\"]
    line = launcher._claude_command(args, which=lambda name: str(cmd), windows=True)
    out = subprocess.run(line, capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == args
