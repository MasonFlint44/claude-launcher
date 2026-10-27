"""Paths as text: what the picker does with paths typed into it and paths
read from Claude's logs.

Every separator the picker relies on is in this module, so a platform whose
paths look different (Windows: any drive letter, C:\\ or D:\\, and UNC paths
like \\\\server\\share) changes only this. For now they're POSIX paths.
"""

from __future__ import annotations

import os

SEP = "/"


def with_sep(directory: str) -> str:
    """`directory` ending in a separator, ready for a name: / stays /."""
    return directory.rstrip(SEP) + SEP


def without_sep(path: str) -> str:
    """`path` without a trailing separator, except the root itself."""
    return path.rstrip(SEP) or SEP


def name(path: str) -> str:
    """The last part of `path` (the path itself, for the root)."""
    return os.path.basename(path.rstrip(SEP)) or path


def first_part(rest: str) -> tuple[str, str]:
    """A relative path split into its first name and what's below it."""
    head, _, deeper = rest.partition(SEP)
    return head, deeper


def is_inside(path: str, directory: str) -> bool:
    """`path` is `directory` or somewhere inside it."""
    path, directory = os.path.normpath(path), os.path.normpath(directory)
    return path == directory or path.startswith(with_sep(directory))


def tilde(directory: str, home: str) -> str:
    """`directory` with the home directory shown as ~."""
    if directory == home:
        return "~"
    if directory.startswith(with_sep(home)):
        return "~" + directory[len(home) :]
    return directory


def typed_path_like(query: str) -> bool:
    """Whether a search is a path, which turns on path mode."""
    return query.startswith((SEP, "~"))


def split_path(query: str) -> tuple[str, str]:
    """A typed path split into the directory so far (ending in a separator)
    and the start of a name in it. `~` and `~user` on their own are that home."""
    if query.startswith("~") and SEP not in query:
        return query + SEP, ""
    head, _, partial = query.rpartition(SEP)
    return head + SEP, partial


def expand(typed: str) -> str | None:
    """The directory a typed path names, or None for an unknown ~user."""
    path = os.path.expanduser(typed)
    return None if path.startswith("~") else os.path.normpath(path)


def up_a_level(query: str) -> str:
    """Esc in path mode: drop a half-typed name, else the last directory.
    "" once there's nothing left above (~/, / or ~user)."""
    head, partial = split_path(query)
    if partial:
        return head
    if SEP not in query:  # ~ or ~user
        return ""
    above = head.rstrip(SEP)
    if not above or above.startswith("~") and SEP not in above:
        return ""
    return above.rpartition(SEP)[0] + SEP
