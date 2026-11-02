"""Finding a program on Windows' PATH, never in the current directory (the
user's project, where a cloned repo could plant a fzf.bat or claude.cmd).
These run on any OS: the search is os.path's, on real directories."""

import os

import pytest

from claude_launcher import paths
from claude_launcher.platforms.windows import on_path
from conftest import posix_only


def program(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("")
    return str(directory / name)


@pytest.fixture
def project(tmp_path, monkeypatch):
    here = tmp_path / "project"
    here.mkdir()
    monkeypatch.chdir(here)
    return here


def test_a_program_in_the_current_directory_is_skipped(tmp_path, project):
    program(project, "fzf.bat")
    found = program(tmp_path / "bin", "fzf.exe")
    assert on_path({"PATH": str(tmp_path / "bin")}, "fzf") == found


def test_only_in_the_current_directory_is_none(project):
    program(project, "fzf.bat")
    assert on_path({"PATH": ""}, "fzf") is None


@pytest.mark.parametrize("entry", [".", "", "project", "{here}", "{HERE}"])
def test_path_entries_naming_the_current_directory_are_skipped(tmp_path, project, entry):
    # Relative entries are skipped too: they're found from the current directory.
    program(project, "claude.cmd")
    found = program(tmp_path / "bin", "claude.cmd")
    entry = entry.format(here=project, HERE=str(project).upper())
    path = os.pathsep.join([entry, str(tmp_path / "bin")])
    assert on_path({"PATH": path}, "claude") == found


def test_case_is_ignored_comparing_with_the_current_directory(tmp_path, project):
    program(project, "git.exe")
    assert on_path({"PATH": str(project)}, "git", cwd=str(project).upper()) is None
    assert on_path({"PATH": str(project).upper()}, "git", cwd=str(project)) is None


def test_pathext_in_order_and_whatever_the_case(tmp_path, project):
    program(tmp_path / "bin", "claude.cmd")
    exe = program(tmp_path / "bin", "claude.exe")
    env = {"path": str(tmp_path / "bin"), "PathExt": ".EXE;.CMD"}
    assert on_path(env, "claude") == exe
    assert on_path({**env, "PathExt": ".CMD;.EXE"}, "claude") == str(tmp_path / "bin" / "claude.cmd")


def test_a_name_with_its_extension(tmp_path, project):
    found = program(tmp_path / "bin", "cmd.exe")
    assert on_path({"PATH": str(tmp_path / "bin")}, "cmd.exe") == found


def test_first_path_entry_wins(tmp_path, project):
    first = program(tmp_path / "a", "fzf.exe")
    program(tmp_path / "b", "fzf.exe")
    path = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    assert on_path({"PATH": path}, "fzf") == first


@posix_only  # shutil.which looks in the current directory on Windows
def test_which_is_shutils_off_windows(tmp_path, project):
    # POSIX's which never looks in the current directory unless PATH says to.
    found = tmp_path / "bin" / "fzf"
    program(tmp_path / "bin", "fzf")
    found.chmod(0o755)
    program(project, "fzf")
    assert paths.which("fzf", {"PATH": str(tmp_path / "bin")}, windows=False) == str(found)
    assert paths.which("fzf", {"PATH": str(tmp_path / "nothing")}, windows=False) is None


def test_which_on_windows_skips_the_current_directory(tmp_path, project):
    program(project, "fzf.bat")
    found = program(tmp_path / "bin", "fzf.exe")
    assert paths.which("fzf", {"PATH": str(tmp_path / "bin")}, windows=True) == found
    assert paths.which("fzf", {"PATH": ""}, windows=True) is None
