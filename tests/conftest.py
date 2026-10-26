"""Shared fixtures: a fake home and Claude config directory, and session logs."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from claude_launcher.paths import Paths


@dataclass
class ClaudeHome:
    paths: Paths

    @property
    def home(self) -> Path:
        return self.paths.home

    def project(self, cwd: Path | str) -> Path:
        """The log directory Claude uses for `cwd`."""
        d = self.paths.log_dirs_for(str(cwd))[0]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(
        self,
        cwd: Path | str,
        entries: list[dict | str],
        session_id: str | None = None,
        mtime: float | None = None,
        log_dir: Path | None = None,
    ) -> str:
        """Write a session log; returns its session ID."""
        session_id = session_id or str(uuid.uuid4())
        path = (log_dir or self.project(cwd)) / f"{session_id}.jsonl"
        lines = [e if isinstance(e, str) else json.dumps(e, separators=(",", ":")) for e in entries]
        path.write_text("".join(line + "\n" for line in lines))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return session_id


def user(text: str, cwd: Path | str = "/tmp", ts: str = "2026-10-20T12:00:00.000Z", **extra) -> dict:
    return {
        "type": "user",
        "parentUuid": None,
        "cwd": str(cwd),
        "timestamp": ts,
        "entrypoint": "cli",
        "isSidechain": False,
        "message": {"role": "user", "content": text},
        **extra,
    }


def assistant(text: str = "ok", cwd: Path | str = "/tmp", ts: str = "2026-10-20T12:00:05.000Z") -> dict:
    return {
        "type": "assistant",
        "parentUuid": "x",
        "cwd": str(cwd),
        "timestamp": ts,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"},
    }


def conversation(prompt: str, cwd: Path | str, **extra) -> list[dict]:
    return [{"type": "mode", "mode": "normal"}, user(prompt, cwd, **extra), assistant(cwd=cwd)]


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch) -> ClaudeHome:
    home = tmp_path / "home"
    home.mkdir()
    paths = Paths(config=home / ".claude", home=home)
    paths.projects.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(paths.config))
    return ClaudeHome(paths)
