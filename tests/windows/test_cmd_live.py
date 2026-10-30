"""cmd itself: the core's command line as fzf runs it, the .cmd files'
choice of Python, and the fetched fzf.exe."""

import json
import os
import shutil
import subprocess
import sys

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


def run_probe(cmd, *path):
    env = {"SystemRoot": os.environ["SYSTEMROOT"], "PATH": os.pathsep.join(str(p) for p in path)}
    return subprocess.run(
        f'"{CMD}" /d /c "{cmd}"', env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
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
