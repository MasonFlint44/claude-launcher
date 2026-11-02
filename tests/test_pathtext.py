"""The picker's path text, by POSIX rules (test_pathtext_windows.py has
Windows'). Pure text, so these run on any OS."""

import pytest

from claude_launcher.pathtext import PathText

p = PathText("posix")


@pytest.mark.parametrize(("path", "expected"), [("/a/b", "/a/b/"), ("/a/b/", "/a/b/"), ("/", "/")])
def test_with_sep(path, expected):
    assert p.with_sep(path) == expected


@pytest.mark.parametrize(("path", "expected"), [("/a/b/", "/a/b"), ("/a", "/a"), ("/", "/"), ("~/", "~")])
def test_without_sep(path, expected):
    assert p.without_sep(path) == expected


@pytest.mark.parametrize(("path", "expected"), [("/a/b", "b"), ("/a/b/", "b"), ("/", "/")])
def test_name(path, expected):
    assert p.name(path) == expected


def test_first_part():
    assert p.first_part("a/b/c") == ("a", "b/c")
    assert p.first_part("a") == ("a", "")


@pytest.mark.parametrize(
    ("path", "directory", "inside"),
    [
        ("/a/b", "/a", True),
        ("/a", "/a", True),
        ("/a/", "/a", True),
        ("/ab", "/a", False),  # a sibling that starts the same
        ("/a", "/a/b", False),
        ("/x", "/", True),
        ("/usr/lib", "//usr", True),  # Linux reads // as /
        ("//usr/lib", "/usr", True),
        ("/usr", "///", True),
    ],
)
def test_is_inside(path, directory, inside):
    assert p.is_inside(path, directory) is inside


@pytest.mark.parametrize(
    ("directory", "expected"), [("/h/me", "~"), ("/h/me/x", "~/x"), ("/h/meow", "/h/meow"), ("/etc", "/etc")]
)
def test_tilde(directory, expected):
    assert p.tilde(directory, "/h/me") == expected


@pytest.mark.parametrize(("query", "path_like"), [("/", True), ("~", True), ("~/x", True), ("x/", False), ("", False)])
def test_typed_path_like(query, path_like):
    assert p.typed_path_like(query) is path_like


@pytest.mark.parametrize(
    ("query", "expected"),
    [("~", ("~/", "")), ("~bob", ("~bob/", "")), ("~/gi", ("~/", "gi")), ("/usr/", ("/usr/", "")), ("/", ("/", ""))],
)
def test_split_path(query, expected):
    assert p.split_path(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("~/git/cl", "~/git/"),
        ("~/git/", "~/"),
        ("~/", ""),
        ("~", ""),
        ("/usr/lib/", "/usr/"),
        ("/usr/", "/"),
        ("/", ""),
    ],
)
def test_up_a_level(query, expected):
    assert p.up_a_level(query) == expected


def test_expand(monkeypatch):
    monkeypatch.setenv("HOME", "/h/me")
    assert p.expand("~/x/") == "/h/me/x"
    assert p.expand("/a//b/") == "/a/b"
    assert p.expand("//usr/") == "/usr"
    assert p.expand("///") == "/"
    assert p.expand("~nobody-here-xyz/") is None


@pytest.mark.parametrize(
    ("path", "expected"), [("//usr", "/usr"), ("///usr//lib/", "/usr/lib"), ("//", "/"), ("/a/../b", "/b")]
)
def test_normal(path, expected):
    assert p.normal(path) == expected
