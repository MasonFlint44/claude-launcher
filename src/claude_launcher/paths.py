"""Where Claude Code keeps its files, and where the launcher keeps its own."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import pathtext

# Claude names a project's log directory after its path, with every character
# other than a letter or digit turned into "-". A name longer than this is cut
# to this length and given a hash suffix.
_LOG_DIR_NAME_MAX = 200

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent.parent
THEME_FILE = REPO_DIR / "themes" / "claude-dark"
CORE = REPO_DIR / "libexec" / "claude-launcher-core"


@dataclass(frozen=True)
class Paths:
    config: Path  # Claude's config directory, usually ~/.claude
    home: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Paths:
        home = Path(env.get("HOME") or Path.home())
        config = env.get("CLAUDE_CONFIG_DIR") or str(home / ".claude")
        return cls(config=Path(config), home=home)

    @property
    def projects(self) -> Path:
        """One directory of session logs per project."""
        return self.config / "projects"

    @property
    def state(self) -> Path:
        return self.config / "state" / "claude-launcher"

    @property
    def open_records(self) -> Path:
        """claude-session-hook's record of each running Claude, by pid."""
        return self.state / "open"

    @property
    def restored(self) -> Path:
        """The sessions that were open at a restart: {session id: restart time}."""
        return self.state / "restored.json"

    @property
    def lock(self) -> Path:
        return self.state / "lock"

    def log_dirs_for(self, directory: str) -> list[Path]:
        """The log directories Claude may keep `directory`'s sessions in."""
        name = re.sub(r"[^a-zA-Z0-9]", "-", directory)
        if len(name) <= _LOG_DIR_NAME_MAX:
            return [self.projects / name]
        prefix = name[:_LOG_DIR_NAME_MAX] + "-"
        try:
            return sorted(p for p in self.projects.iterdir() if p.name.startswith(prefix))
        except OSError:
            return []

    def all_log_dirs(self) -> list[Path]:
        try:
            return sorted(p for p in self.projects.iterdir() if p.is_dir())
        except OSError:
            return []

    def log_for(self, session_id: str) -> Path | None:
        """The log of the session with this ID, in whichever project it's in."""
        for d in self.all_log_dirs():
            log = d / f"{session_id}.jsonl"
            if log.is_file():
                return log
        return None

    def tilde(self, directory: str) -> str:
        """`directory` with the home directory shown as ~."""
        return pathtext.tilde(directory, str(self.home))
