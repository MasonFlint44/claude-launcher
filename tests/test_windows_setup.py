"""Windows' install: claude-launcher.cmd and the Windows Terminal profile.
Files only, so these run on any OS."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from claude_launcher import files
from claude_launcher.paths import REPO_DIR
from claude_launcher.platforms.windows import data_dir, terminal
from claude_launcher.platforms.windows import setup as windows_setup
from claude_launcher.platforms.windows.setup import DEFAULT_ICON, HOOK, LAUNCHER, TOO_OLD, WindowsSetup
from claude_launcher.theme import Theme

HOSTILE = "Jo Smith (A&B) ^1 100% !x! [é]"


@pytest.fixture
def env(tmp_path):
    return {"USERPROFILE": str(tmp_path / "me"), "LOCALAPPDATA": str(tmp_path / "local")}


@pytest.fixture
def setup(env, tmp_path):
    return WindowsSetup(tmp_path / "data", env=env)


@pytest.fixture
def installed(tmp_path):
    """As installed for real: the data directory in %LOCALAPPDATA%."""
    env = {"USERPROFILE": str(tmp_path / HOSTILE), "LOCALAPPDATA": str(tmp_path / HOSTILE / "AppData" / "Local")}
    return WindowsSetup(data_dir(env), env={**env, "SystemRoot": "D:\\Win"})


def fragment(env):
    return json.loads((terminal.fragments_dir(env) / "claude-launcher.json").read_text())


def test_scheme_is_claude_dark_from_the_theme_file():
    theme = Theme.load()
    scheme = terminal.scheme(theme)
    assert scheme["name"] == "Claude Dark"
    assert scheme["background"] == theme.colors["background"] and scheme["cursorColor"] == theme.accent
    assert scheme["brightBlack"] == theme.palette[8] and scheme["white"] == theme.palette[7]
    assert len(scheme) == 1 + 4 + 16


def test_install_writes_the_profile_and_the_command(setup, env, tmp_path):
    lines = list(setup.install(None, None))
    profile = fragment(env)["profiles"][0]
    assert profile["name"] == "Claude Code" and profile["colorScheme"] == "Claude Dark"
    # Somewhere other than %LOCALAPPDATA%: the path, plainly quoted.
    assert profile["commandline"] == f'"{tmp_path / "data" / "claude-launcher.cmd"}"'
    assert profile["startingDirectory"] == "%USERPROFILE%"
    assert Path(profile["icon"]).read_bytes() == DEFAULT_ICON.read_bytes()
    shim = Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    assert shim.read_bytes() == windows_setup.shim_cmd().encode()
    assert any("Claude Code" in line for line in lines)


def test_the_profile_names_its_cmd_by_localappdata(installed):
    # Windows Terminal expands it, so no % & é of the user's is in the command line.
    list(installed.install(None, None))
    profile = fragment(installed.env)["profiles"][0]
    assert profile["commandline"] == '"%LOCALAPPDATA%\\claude-launcher\\claude-launcher.cmd"'


def test_cmd_files_are_ascii_and_hold_no_paths(installed):
    # cmd reads a batch file in the console's code page: é would come out wrong.
    list(installed.install(None, None))
    shim = Path(installed.env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    for path in [installed.data_dir / "claude-launcher.cmd", installed.data_dir / "claude-session-hook.cmd", shim]:
        raw = path.read_bytes()
        raw.decode("ascii")
        text = raw.decode()
        assert "\r\n" in text and "\n" not in text.replace("\r\n", "")  # cmd wants CRLF
        for path_text in (str(REPO_DIR), str(installed.data_dir), sys.executable, HOSTILE):
            assert path_text not in text


def test_nothing_names_a_python(installed):
    # Each .cmd finds Python as it runs, so a new or moved Python just works.
    list(installed.install(None, None))
    for name, entry, options in (("claude-launcher", LAUNCHER, ""), ("claude-session-hook", HOOK, "-I ")):
        text = (installed.data_dir / f"{name}.cmd").read_text()
        assert "python.exe" in text and "py.exe" in text and "\\WindowsApps\\" in text
        assert f'"%CLAUDE_LAUNCHER_PYTHON%" {options}"%~dp0{name}.py" %*' in text
        assert f'"%CLAUDE_LAUNCHER_PY%" -3 {options}"%~dp0{name}.py" %*' in text
        assert f'"%CLAUDE_LAUNCHER_STORE%" {options}"%~dp0{name}.py" %*' in text
        assert 'if not "%CLAUDE_LAUNCHER_EXIT%"=="9310" if not "%CLAUDE_LAUNCHER_EXIT%"=="103" exit' in text
        assert "setlocal DisableDelayedExpansion" in text
        script = (installed.data_dir / f"{name}.py").read_bytes().decode("ascii")
        assert ascii(str(entry)) in script and sys.executable not in script


def test_the_python_search_is_from_the_cmds_own_directory(installed):
    # %~$PATH: may look in the current directory first: a python.exe in
    # Claude's directory would be taken. Each search is from %~dp0, and the
    # current directory is back before Python runs.
    list(installed.install(None, None))
    for name in ("claude-launcher", "claude-session-hook"):
        lines = (installed.data_dir / f"{name}.cmd").read_text().splitlines()
        inside = False
        for line in lines:
            if line == 'pushd "%~dp0."':
                inside = True
            elif line == "popd":
                inside = False
            elif "~$PATH:" in line:
                assert inside, line
            elif '"%~dp0' in line:
                assert not inside, line
        assert lines.count('pushd "%~dp0."') == lines.count("popd") == 3


def test_the_cmds_own_variables_never_reach_python(installed):
    # Each line that runs Python empties them first: its %...% were already
    # expanded when cmd read it. And each is unmistakably ours.
    list(installed.install(None, None))
    for name in ("claude-launcher", "claude-session-hook"):
        lines = (installed.data_dir / f"{name}.cmd").read_text().splitlines()
        names = set(re.findall(r'set "([A-Za-z_]+)=', "\n".join(lines)))
        assert names and all(n.startswith("CLAUDE_LAUNCHER_") for n in names)
        helpers = names - {"CLAUDE_LAUNCHER_FALLBACK"}  # for the entry script, which takes it out
        runs = [line for line in lines if '"%~dp0' in line and not line.startswith("pushd ")]
        assert len(runs) == 3
        for line in runs:
            cleared, command = line.split(' & "%', 1)
            assert set(re.findall(r'set "([A-Za-z_]+)="', cleared)) == helpers
            assert "set " not in command


def test_the_hook_names_its_cmd_by_localappdata(installed):
    # Claude quotes only arguments with spaces, so cmd would split a path
    # with & in it; delayed expansion puts the path in after cmd has parsed.
    assert installed.hook_command() == [
        "D:\\Win\\System32\\cmd.exe",
        "/d",
        "/v:on",
        "/c",
        "!LOCALAPPDATA!\\claude-launcher\\claude-session-hook.cmd",
    ]


def test_the_hook_by_userprofile_without_localappdata(tmp_path):
    env = {"USERPROFILE": str(tmp_path / HOSTILE), "SystemRoot": "D:\\Win"}
    command = WindowsSetup(data_dir(env), env=env).hook_command()
    assert command[-1] == "!USERPROFILE!\\AppData\\Local\\claude-launcher\\claude-session-hook.cmd"


@pytest.mark.parametrize(
    "local",
    ["C:\\Users\\A&B\\AppData\\Local\\", "C:/Users/A&B/AppData/Local", "c:/users/a&b/appdata/local/"],
)
def test_env_form_takes_localappdata_as_its_set(local):
    # A trailing separator, forward slashes, another case: the rest is the same.
    path = Path("C:\\Users\\A&B\\AppData\\Local\\claude-launcher\\claude-session-hook.cmd")
    assert windows_setup.env_form(path, {"LOCALAPPDATA": local}) == (
        "LOCALAPPDATA",
        "\\claude-launcher\\claude-session-hook.cmd",
    )


def test_env_form_needs_the_path_below_the_variable():
    path = Path("D:\\Users\\me\\AppData\\Local\\claude-launcher\\x.cmd")
    assert windows_setup.env_form(path, {"LOCALAPPDATA": "C:\\Users\\me\\AppData\\Local"}) is None
    assert windows_setup.env_form(path, {}) is None


def test_without_either_variable_a_plain_path_is_used(tmp_path):
    data = tmp_path / "Jo Smith" / "AppData" / "Local" / "claude-launcher"
    command = WindowsSetup(data, env={"SystemRoot": "D:\\Win"}).hook_command()
    assert command == ["D:\\Win\\System32\\cmd.exe", "/d", "/c", str(data / "claude-session-hook.cmd")]


@pytest.mark.parametrize("name", ["A&B", "a^b", "100%", "Jo (work)", "x,y", HOSTILE])
def test_without_either_variable_a_path_cmd_would_split_is_refused(tmp_path, name):
    # cmd /c would split it at & (even quoted: it drops the quotes then),
    # so the installer warns and adds no hook rather than a broken one.
    data = tmp_path / name / "AppData" / "Local" / "claude-launcher"
    with pytest.raises(ValueError, match="set LOCALAPPDATA"):
        WindowsSetup(data, env={"SystemRoot": "D:\\Win"}).hook_command()


@pytest.mark.parametrize("key", ["SystemRoot", "SYSTEMROOT", "systemroot"])
def test_system32s_cmd_whatever_the_case(installed, key):
    env = {k: v for k, v in installed.env.items() if k != "SystemRoot"}
    s = WindowsSetup(installed.data_dir, env={**env, key: "E:\\Windows", "ComSpec": "C:\\tcc\\tcc.exe"})
    assert s.hook_command()[0] == "E:\\Windows\\System32\\cmd.exe"  # not ComSpec, and no C: assumed


def test_without_systemroot_cmd_from_path(installed):
    env = {k: v for k, v in installed.env.items() if k != "SystemRoot"}
    assert WindowsSetup(installed.data_dir, env=env).hook_command()[0] == "cmd.exe"


def test_a_hook_elsewhere_is_its_path(setup, tmp_path):
    # Only tests put it there: one quoted path, which cmd /c keeps.
    assert setup.hook_command() == ["cmd.exe", "/d", "/c", str(tmp_path / "data" / "claude-session-hook.cmd")]


def test_path_check_ignores_case_and_a_trailing_backslash(tmp_path):
    me = tmp_path / "Me"
    shim_dir = str(me / ".local" / "bin")
    env = {"USERPROFILE": str(me), "Path": "C:\\x;" + shim_dir.upper() + "\\;D:\\y"}
    lines = list(WindowsSetup(tmp_path / "data", env=env).install(None, None))
    assert not any("PATH" in line for line in lines)
    lines = list(WindowsSetup(tmp_path / "data", env={**env, "Path": "C:\\x"}).install(None, None))
    assert any("is not on your PATH" in line for line in lines)


def test_path_check_takes_a_quoted_entry(tmp_path):
    # PATH entries may be quoted, as a directory with ; in it must be.
    me = tmp_path / "Me"
    env = {"USERPROFILE": str(me), "Path": f'C:\\x; "{me / ".local" / "bin"}" ;D:\\y'}
    lines = list(WindowsSetup(tmp_path / "data", env=env).install(None, None))
    assert not any("PATH" in line for line in lines)


@pytest.mark.parametrize("name", ["claude-launcher.cmd", "claude-session-hook.cmd", "claude-launcher.py"])
def test_a_failed_write_leaves_the_old_file(setup, env, monkeypatch, name):
    # A disk that fills up partway: a .cmd cut short would run half its lines.
    list(setup.install(None, None))
    shim = Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    before = {p: p.read_bytes() for p in [*setup.data_dir.iterdir(), shim]}
    write_text = Path.write_text

    def disk_full(self, text, *args, **kwargs):
        if self.name.startswith(name):
            write_text(self, text[:20], *args, **kwargs)
            raise OSError(28, "No space left on device")
        return write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError):
        list(WindowsSetup(setup.data_dir, env=env).install(None, None))
    assert {p: p.read_bytes() for p in before} == before
    assert not list(setup.data_dir.glob("*.tmp")) and not list(shim.parent.glob("*.tmp"))


def test_a_file_in_use_for_a_moment_is_still_written(setup, env, monkeypatch):
    # A virus scanner or indexer can hold a file just written; Windows then
    # refuses to replace it until it lets go.
    monkeypatch.setattr(files, "_PAUSE", 0)
    real, failures = os.replace, []

    def busy(src, dst):
        if len(failures) < 3:
            failures.append(dst)
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(src, dst)

    monkeypatch.setattr(os, "replace", busy)
    list(setup.install(None, None))
    assert len(failures) == 3
    assert (setup.data_dir / "claude-launcher.cmd").read_bytes() == windows_setup.run_cmd("claude-launcher.py").encode()
    assert not list(setup.data_dir.glob("*.tmp"))


def run_entry(tmp_path, target: Path, *args, env=None):
    entry = tmp_path / "entry.py"
    entry.write_text(windows_setup.entry_script(target), encoding="ascii")
    return subprocess.run(
        [sys.executable, "-I", str(entry), *args], capture_output=True, text=True, encoding="utf-8", env=env
    )


def test_the_entry_script_runs_its_target_from_any_path(tmp_path):
    # The repo's path goes only into the entry script, ASCII-escaped.
    target = tmp_path / HOSTILE / "probe.py"
    target.parent.mkdir()
    target.write_text("import json, sys\nprint(json.dumps([__name__, __file__, sys.argv[1:]]))\nsys.exit(3)\n")
    done = run_entry(tmp_path, target, "start", "a&b")
    assert done.returncode == 3
    assert json.loads(done.stdout) == ["__main__", str(target), ["start", "a&b"]]


# Windows keeps the whole exit code; POSIX only its low byte.
EXIT_TOO_OLD = TOO_OLD if os.name == "nt" else TOO_OLD % 256


def test_a_python_too_old_says_so_unless_another_is_left(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_setup, "MIN_PYTHON", (99, 0))
    target = tmp_path / "probe.py"
    target.write_text("print('ran')\n")
    done = run_entry(tmp_path, target)
    assert done.returncode == EXIT_TOO_OLD and done.stdout == ""
    assert "needs Python 99.0 or newer (found" in done.stderr and sys.executable in done.stderr
    quiet = run_entry(tmp_path, target, env={**os.environ, "CLAUDE_LAUNCHER_FALLBACK": "1"})
    assert quiet.returncode == EXIT_TOO_OLD and quiet.stderr == ""


@pytest.mark.parametrize("code", [TOO_OLD, windows_setup.PY_NONE])
def test_a_script_that_ran_never_exits_with_a_retry_code(tmp_path, code):
    # The .cmd would run it again with the next Python.
    target = tmp_path / "probe.py"
    target.write_text(f"import sys\nprint('ran')\nsys.exit({code})\n")
    done = run_entry(tmp_path, target)
    assert done.returncode == windows_setup.RAN_INSTEAD and done.stdout == "ran\n"


def test_profile_guid_never_changes(setup, env):
    # Windows Terminal keeps the user's own changes to the profile under it.
    list(setup.install(None, None))
    list(setup.install(None, None))
    assert fragment(env)["profiles"][0]["guid"] == terminal.PROFILE_GUID


def test_own_icon_kept_until_default(setup, env, tmp_path):
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"png")
    list(setup.install(None, str(mine)))
    mine.unlink()  # a copy is kept
    list(setup.install(None, None))
    assert Path(fragment(env)["profiles"][0]["icon"]).read_bytes() == b"png"
    list(setup.install(None, "default"))
    assert Path(fragment(env)["profiles"][0]["icon"]).read_bytes() == DEFAULT_ICON.read_bytes()


def test_icon_must_be_a_png(setup, tmp_path):
    with pytest.raises(ValueError, match="^the icon for Windows Terminal must be a .png$"):
        list(setup.install(None, str(tmp_path / "x.svg")))


def test_check_turns_down_a_bad_icon_before_anything(setup, tmp_path):
    (tmp_path / "x.svg").write_text("<svg/>")
    with pytest.raises(ValueError, match="^the icon for Windows Terminal must be a .png$"):
        setup.check(None, str(tmp_path / "x.svg"))
    with pytest.raises(ValueError, match="^not a file: "):
        setup.check(None, str(tmp_path / "missing.png"))
    (tmp_path / "mine.png").write_bytes(b"png")
    setup.check(None, str(tmp_path / "mine.png"))
    setup.check("ghostty", "Default")  # --terminal is only warned about
    assert not (tmp_path / "data").exists()


def test_someone_elses_cmd_is_left_alone(setup, env):
    shim = Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"@echo mine\r\n")
    lines = list(setup.install(None, None))
    assert any("isn't one of ours" in line for line in lines)
    list(setup.uninstall())
    assert shim.read_bytes() == b"@echo mine\r\n"


def test_uninstall_takes_it_all_out(setup, env):
    list(setup.install(None, None))
    lines = list(setup.uninstall())
    assert not terminal.fragments_dir(env).exists()
    assert not (Path(env["USERPROFILE"]) / ".local" / "bin" / "claude-launcher.cmd").exists()
    assert len(lines) == 2


def test_terminal_option_is_for_linux(setup):
    assert any("--terminal is for Linux desktops; on Windows" in line for line in setup.install("ghostty", None))


def test_icon_can_be_the_copy_already_made(setup, env, tmp_path):
    # Run again with --icon naming the installed copy, or the profile's own.
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"png")
    list(setup.install(None, str(mine)))
    list(setup.install(None, str(setup.data_dir / "icon.png")))
    shown = Path(fragment(env)["profiles"][0]["icon"])
    list(setup.install(None, str(shown)))
    assert Path(fragment(env)["profiles"][0]["icon"]).read_bytes() == b"png"
