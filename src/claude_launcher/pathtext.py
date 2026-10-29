"""Paths as text: what the picker does with paths typed into it and paths
read from Claude's logs.

Every separator the picker relies on is in this module. Two sets of rules,
picked by the OS the launcher runs on (WSL is Linux):

- POSIX: `/`, and `~` for home. A leading // is /, as Linux reads it.
- Windows: any drive letter (C:\\, D:\\), UNC paths (\\\\server\\share,
  \\\\wsl.localhost\\Ubuntu), `\\` or `/` as typed, `~` for the user's
  profile, and no difference between upper and lower case.

Typed text stays as typed; paths are tidied (`normal`) only where they're
compared or opened. Both sets are tested on any OS (`PathText("nt")`).
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re

_DRIVE = re.compile(r"[A-Za-z]:")


class PathText:
    def __init__(self, flavor: str = os.name):
        self.windows = flavor == "nt"
        self.path = ntpath if self.windows else posixpath
        self.sep = "\\" if self.windows else "/"
        self.seps = "\\/" if self.windows else "/"

    def _last_sep(self, text: str) -> int:
        return max(text.rfind(s) for s in self.seps)

    def _is_root(self, head: str) -> bool:
        """Whether a typed directory can't be stepped out of: / or C:\\ or \\\\srv\\share\\."""
        stripped = head.rstrip(self.seps)
        if not stripped:
            return True
        if not self.windows:
            return False
        drive, rest = ntpath.splitdrive(stripped)
        return bool(drive) and not rest.strip(self.seps)

    def with_sep(self, directory: str) -> str:
        """`directory` ending in a separator, ready for a name: / stays /. On
        Windows, the slash it already uses, so ~/git completes to ~/git/."""
        sep = "/" if self.windows and "/" in directory and "\\" not in directory else self.sep
        return directory.rstrip(self.seps) + sep

    def without_sep(self, path: str) -> str:
        """`path` without a trailing separator, except a root itself."""
        stripped = path.rstrip(self.seps)
        if not stripped:
            return self.sep
        return stripped + self.sep if self.windows and _DRIVE.fullmatch(stripped) else stripped

    def name(self, path: str) -> str:
        """The last part of `path` (the path itself, for a root)."""
        return self.path.basename(path.rstrip(self.seps)) or path

    def first_part(self, rest: str) -> tuple[str, str]:
        """A relative path split into its first name and what's below it."""
        cut = min((i for i in (rest.find(s) for s in self.seps) if i >= 0), default=-1)
        return (rest, "") if cut < 0 else (rest[:cut], rest[cut + 1 :])

    def normal(self, path: str) -> str:
        """`path` tidied for opening and comparing: no doubled separators, `.`
        or `..`. POSIX lets a system give a leading // a meaning of its own, so
        normpath keeps it; Linux gives it none, so //usr is /usr. On Windows a
        leading \\\\ is a network share and stays, and C: is C:\\."""
        if self.windows and _DRIVE.fullmatch(path):
            path += self.sep
        path = self.path.normpath(path)
        if not self.windows and path.startswith("//"):
            path = "/" + path.lstrip("/")
        return path

    def _key(self, path: str) -> str:
        """How two paths compare: Windows ignores case and which slash."""
        return ntpath.normcase(path) if self.windows else path

    def same(self, a: str, b: str) -> bool:
        return self._key(self.normal(a)) == self._key(self.normal(b))

    def below(self, path: str, directory: str) -> str | None:
        """What's left of `path` below `directory` ("" for the directory
        itself), or None if it isn't inside it. Paths from a directory's
        listing and from logs both go through this, so they compare alike."""
        path, directory = self.normal(path), self.normal(directory)
        if self._key(path) == self._key(directory):
            return ""
        prefix = self.with_sep(directory)
        if self._key(path).startswith(self._key(prefix)):
            return path[len(prefix) :]
        return None

    def is_inside(self, path: str, directory: str) -> bool:
        """`path` is `directory` or somewhere inside it."""
        return self.below(path, directory) is not None

    def tilde(self, directory: str, home: str) -> str:
        """`directory` with the home directory shown as ~."""
        rest = self.below(directory, home) if home else None
        if rest is None:
            return directory
        return "~" + (self.sep + rest if rest else "")

    def typed_path_like(self, query: str) -> bool:
        """Whether a search is a path, which turns on path mode."""
        if query.startswith(("~", *self.seps)):
            return True
        return self.windows and _DRIVE.match(query) is not None

    def split_path(self, query: str) -> tuple[str, str]:
        """A typed path split into the directory so far (ending in a separator)
        and the start of a name in it. `~` and `~user` on their own are that
        home, and on Windows `C:` is C:\\."""
        cut = self._last_sep(query)
        if cut >= 0:
            return query[: cut + 1], query[cut + 1 :]
        if self.windows and _DRIVE.match(query):  # C: or C:name, from C:'s root
            return query[:2] + self.sep, query[2:]
        return query + self.sep, ""  # ~ or ~user

    def expand(self, typed: str) -> str | None:
        """The directory a typed path names, or None for an unknown ~user."""
        try:
            path = self.path.expanduser(typed)
        except (ImportError, KeyError):  # ~user by POSIX rules, on an OS without pwd
            return None
        return None if path.startswith("~") else self.normal(path)

    def up_a_level(self, query: str) -> str:
        """Esc in path mode: drop a half-typed name, else the last directory.
        "" once there's nothing left above (~/, /, C:\\, a share, or ~user)."""
        head, partial = self.split_path(query)
        if partial:
            return head
        above = head.rstrip(self.seps)
        if not above or (above.startswith("~") and self._last_sep(above) < 0):
            return ""
        if self._is_root(head):
            return ""
        return above[: self._last_sep(above) + 1]


_here = PathText()
SEP = _here.sep
with_sep = _here.with_sep
without_sep = _here.without_sep
name = _here.name
first_part = _here.first_part
normal = _here.normal
same = _here.same
below = _here.below
is_inside = _here.is_inside
tilde = _here.tilde
typed_path_like = _here.typed_path_like
split_path = _here.split_path
expand = _here.expand
up_a_level = _here.up_a_level
