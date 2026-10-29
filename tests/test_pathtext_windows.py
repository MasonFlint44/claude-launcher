"""Windows paths, as the picker sees them: any drive, UNC shares, either
slash typed, case ignored. Pure text, so these run on any OS."""

import pytest

from claude_launcher.pathtext import PathText

w = PathText("nt")


@pytest.mark.parametrize(
    ("query", "path_like"),
    [
        ("C:", True),
        ("d:\\x", True),
        ("Z:/x", True),
        ("\\\\server\\share", True),
        ("//server/share", True),
        ("\\Users", True),  # the current drive's root
        ("~", True),
        ("~\\git", True),
        ("CC:", False),
        ("git", False),
        ("1:", False),
    ],
)
def test_typed_path_like(query, path_like):
    assert w.typed_path_like(query) is path_like


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("C:", ("C:\\", "")),
        ("C:gi", ("C:\\", "gi")),
        ("D:\\", ("D:\\", "")),
        ("D:\\Users\\me\\gi", ("D:\\Users\\me\\", "gi")),
        ("D:/Users/me/gi", ("D:/Users/me/", "gi")),  # typed with /, kept as typed
        ("C:\\Users/me\\x", ("C:\\Users/me\\", "x")),
        ("\\\\srv\\share\\pro", ("\\\\srv\\share\\", "pro")),
        ("~", ("~\\", "")),
        ("~\\gi", ("~\\", "gi")),
    ],
)
def test_split_path(query, expected):
    assert w.split_path(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("D:\\Users\\me\\gi", "D:\\Users\\me\\"),
        ("D:\\Users\\me\\", "D:\\Users\\"),
        ("D:\\Users\\", "D:\\"),
        ("D:\\", ""),
        ("C:", ""),
        ("E:/data/", "E:/"),
        ("\\\\srv\\share\\x\\", "\\\\srv\\share\\"),
        ("\\\\srv\\share\\", ""),
        ("\\\\wsl.localhost\\Ubuntu\\home\\", "\\\\wsl.localhost\\Ubuntu\\"),
        ("~\\git\\", "~\\"),
        ("~\\", ""),
    ],
)
def test_up_a_level(query, expected):
    assert w.up_a_level(query) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:", "C:\\"),
        ("c:/users//me/", "c:\\users\\me"),
        ("D:\\a\\..\\b", "D:\\b"),
        ("\\\\srv\\share\\x\\", "\\\\srv\\share\\x"),
        ("//srv/share/x", "\\\\srv\\share\\x"),
    ],
)
def test_normal(path, expected):
    assert w.normal(path) == expected


@pytest.mark.parametrize(
    ("path", "directory", "inside"),
    [
        ("C:\\Users\\me\\git", "C:\\Users\\me", True),
        ("c:\\users\\ME\\git", "C:\\Users\\me", True),  # case doesn't matter
        ("C:/Users/me/git", "C:\\Users\\me", True),
        ("C:\\Users\\meow", "C:\\Users\\me", False),
        ("D:\\Users\\me", "C:\\Users\\me", False),  # another drive
        ("D:\\x", "D:\\", True),
        ("\\\\srv\\share\\a", "\\\\srv\\share", True),
        ("\\\\srv\\other\\a", "\\\\srv\\share", False),
    ],
)
def test_is_inside(path, directory, inside):
    assert w.is_inside(path, directory) is inside


def test_below_keeps_the_paths_own_case():
    assert w.below("C:\\Users\\Me\\Git\\Repo", "c:\\users\\me") == "Git\\Repo"
    assert w.below("C:\\Users\\me", "C:\\Users\\me\\") == ""
    assert w.below("C:\\Users", "C:\\Users\\me") is None


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("C:\\Users\\me", "~"),
        ("C:\\Users\\me\\git\\x", "~\\git\\x"),
        ("c:\\users\\me\\git", "~\\git"),
        ("C:\\Users\\meow", "C:\\Users\\meow"),
        ("D:\\Users\\me", "D:\\Users\\me"),
    ],
)
def test_tilde(directory, expected):
    assert w.tilde(directory, "C:\\Users\\me") == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:\\a\\b\\", "C:\\a\\b"),
        ("C:\\", "C:\\"),
        ("C:", "C:\\"),
        ("\\\\srv\\share\\", "\\\\srv\\share"),
        ("~\\", "~"),
    ],
)
def test_without_sep(path, expected):
    assert w.without_sep(path) == expected


@pytest.mark.parametrize(("path", "expected"), [("C:\\a", "C:\\a\\"), ("C:\\", "C:\\"), ("D:/x/", "D:/x\\")])
def test_with_sep(path, expected):
    assert w.with_sep(path) == expected


@pytest.mark.parametrize(
    ("path", "expected"), [("C:\\a\\b", "b"), ("C:\\a\\b\\", "b"), ("D:/x/y", "y"), ("C:\\", "C:\\")]
)
def test_name(path, expected):
    assert w.name(path) == expected


def test_first_part():
    assert w.first_part("a\\b\\c") == ("a", "b\\c")
    assert w.first_part("a/b") == ("a", "b")
    assert w.first_part("a") == ("a", "")


def test_expand(monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\me")
    assert w.expand("~\\git\\") == "C:\\Users\\me\\git"
    assert w.expand("D:") == "D:\\"
    assert w.expand("d:/a//b/") == "d:\\a\\b"


def test_home_is_the_profile_not_git_bashs_home():
    from claude_launcher.paths import Paths

    paths = Paths.from_env({"HOME": "/c/Users/me", "USERPROFILE": "C:\\Users\\me"}, windows=True)
    assert str(paths.home) == "C:\\Users\\me"


def test_log_directory_name_for_a_windows_path(tmp_path):
    # As Claude names it on Windows (seen with Claude Code 2.1.312).
    from claude_launcher.paths import Paths

    paths = Paths(config=tmp_path, home=tmp_path)
    assert paths.log_dirs_for("C:\\Users\\tester") == [tmp_path / "projects" / "C--Users-tester"]
