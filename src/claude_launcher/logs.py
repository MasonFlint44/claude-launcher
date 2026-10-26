"""Reading Claude Code's session logs (~/.claude/projects/<project>/<id>.jsonl).

The session list matches what Claude Code's /resume picker shows (as of
2.1.310). It reads only the first and last 64 KB of each log, since Claude re-writes
a session's title entries near the end, and it leaves out the same sessions:
subagent and team sessions, `claude -p` and SDK runs, /loop and daemon
sessions, sessions continued in a newer one, and sessions with no messages.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import Paths

BUF = 65536  # how much of each end of a log is read
_MESSAGES_SCAN = 16 * 1024 * 1024  # how far into a log to look for a first message
SDK_ENTRYPOINTS = {"sdk-cli", "sdk-ts", "sdk-py"}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TAG_BLOCK = re.compile(r"<([a-z][\w-]*)(?:\s[^>]*)?>[\s\S]*?</\1>")
_NOT_A_PROMPT = re.compile(r"^(?:\s*<[a-z][\w-]*[\s>]|\[Request interrupted by user[^\]]*\])")
_TITLE_MAX = 200


def is_session_id(text: str) -> bool:
    return bool(_UUID.match(text))


@dataclass(frozen=True)
class Session:
    id: str
    active: float  # when it was last active, as Unix time
    cwd: str
    title: str


# --- Pulling fields out of raw log text without parsing every line ---------


def _unescape(value: str) -> str:
    if "\\" not in value:
        return value
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value


def _string_fields(text: str, key: str) -> list[str]:
    pattern = '"' + re.escape(key) + r'": ?"((?:[^"\\]|\\.)*)"'
    return [_unescape(v) for v in re.findall(pattern, text)]


def _first_field(text: str, key: str) -> str | None:
    found = _string_fields(text, key)
    return found[0] if found else None


def _last_field(text: str, key: str) -> str | None:
    found = _string_fields(text, key)
    return found[-1] if found else None


def _raw_field_start(text: str, key: str, limit: int = 200) -> str:
    """The first `limit` characters of a string field, even if the log was cut mid-value."""
    m = re.search('"' + re.escape(key) + '": ?"', text)
    if not m:
        return ""
    i = j = m.end()
    n = 0
    while j < len(text) and n < limit:
        if text[j] == "\\":
            j += 2
        elif text[j] == '"':
            break
        else:
            j += 1
        n += 1
    return text[i:j].replace("\\n", " ").replace("\\t", " ").strip()


def _parse(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _is_type(line: str, type_: str) -> bool:
    return f'"type":"{type_}"' in line or f'"type": "{type_}"' in line


def _last_of_type(text: str, type_: str, key: str) -> str | None:
    for line in reversed(text.split("\n")):
        if _is_type(line, type_) and f'"{key}":' in line:
            entry = _parse(line)
            if entry and entry.get("type") == type_ and isinstance(entry.get(key), str):
                return entry[key]
    return None


def _texts(entry: dict) -> list[str]:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
    return []


def _is_tool_result(entry: dict) -> bool:
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _is_typed_prompt(entry: dict) -> bool:
    """A user entry that's something typed, not a tool result or a note from Claude."""
    return (
        entry.get("type") == "user"
        and entry.get("isMeta") is not True
        and entry.get("isCompactSummary") is not True
        and not _is_tool_result(entry)
    )


def _tag(text: str, name: str) -> str | None:
    m = re.search(rf"<{name}>([\s\S]*?)</{name}>", text)
    return m.group(1) if m else None


def _user_lines(text: str) -> Iterator[str]:
    for line in text.split("\n"):
        if _is_type(line, "user") and '"tool_result"' not in line:
            yield line


def _prompt_text(entry: dict, command: list[str], with_args: bool = False) -> str | None:
    """What Claude shows of a user entry as a prompt, or None.

    A slash command is only noted, in command[0], to stand in when nothing else
    was typed. With `with_args`, one with arguments counts, unless it's one of
    Claude's built-in commands: those are logged without the "human" origin
    that something you typed has, while plugin and skill commands have it.
    """
    for text in _texts(entry):
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        name = _tag(text, "command-name")
        if name:
            args = (_tag(text, "command-args") or "").strip()
            if with_args and args and (entry.get("origin") or {}).get("kind") == "human":
                return f"{name} {args}"
            command[0] = command[0] or name
            continue
        bash = _tag(text, "bash-input")
        if bash:
            return "! " + bash.strip()
        if _NOT_A_PROMPT.match(text):
            continue
        return text[:_TITLE_MAX].strip() + "…" if len(text) > _TITLE_MAX else text
    return None


# --- One session log -------------------------------------------------------


@dataclass
class _Log:
    path: Path
    size: int
    head: str
    tail: str

    @classmethod
    def read(cls, path: Path) -> _Log:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            head = f.read(BUF)
            tail = head
            if size > BUF:
                f.seek(size - BUF)
                tail = f.read(BUF)
        return cls(path, size, head.decode("utf-8", "replace"), tail.decode("utf-8", "replace"))

    @property
    def session_id(self) -> str:
        return self.path.name[: -len(".jsonl")]

    def has_messages(self) -> bool:
        marker = '"parentUuid":'
        if marker in self.head or marker in self.tail:
            return True
        if self.size <= BUF:
            return False
        if self.size > _MESSAGES_SCAN:
            return True
        with open(self.path, "rb") as f:
            return marker.encode() in f.read(_MESSAGES_SCAN)

    def is_hidden_kind(self) -> bool:
        """Subagent, team, daemon, SDK and /loop sessions aren't listed."""
        head = self.head
        if '"isSidechain":true' in head or '"isSidechain": true' in head:
            return True
        if _first_field(head, "teamName"):
            return True
        kind_line = next((line for line in head.split("\n") if '"parentUuid":' in line), head)
        if _first_field(kind_line, "sessionKind") in ("daemon", "daemon-worker"):
            return True
        entrypoint = _first_field(head, "entrypoint") or _last_field(self.tail, "entrypoint")
        return entrypoint in SDK_ENTRYPOINTS or self._is_loop()

    def _is_loop(self) -> bool:
        for line in _user_lines(self.head):
            entry = _parse(line)
            if entry and _is_typed_prompt(entry):
                return any("<command-name>/loop</command-name>" in t for t in _texts(entry))
        return False

    def continued_in(self) -> str | None:
        """The newer session this one was continued in, if it stopped here."""
        if '"continued-in"' not in self.tail:
            return None
        for line in reversed(self.tail.split("\n")):
            if _is_type(line, "continued-in"):
                entry = _parse(line)
                if entry:
                    cid = entry.get("continuedInSessionId")
                    return cid if isinstance(cid, str) and is_session_id(cid) else None
            elif _is_type(line, "user") or _is_type(line, "assistant"):
                # A finished reply or a prompt after the marker: the session went on here.
                entry = _parse(line)
                if not entry:
                    continue
                if entry.get("type") == "assistant":
                    if entry.get("isApiErrorMessage") is not True and isinstance(
                        (entry.get("message") or {}).get("stop_reason"), str
                    ):
                        return None
                elif _is_typed_prompt(entry) and _prompt_text(entry, [""]) is not None:
                    return None
        return None

    def title(self) -> str:
        """The /rename name, else Claude's title, else the last prompt, else the first."""
        custom = _last_field(self.tail, "customTitle")
        if custom is None:  # not re-written near the end: try the file /rename writes
            try:
                with open(self.path.parent / self.session_id / "custom-title.json") as f:
                    value = json.load(f).get("customTitle")
                custom = value if isinstance(value, str) else None
            except (OSError, ValueError, AttributeError):
                pass
        if custom is None:
            custom = _last_field(self.head, "customTitle")
        ai_title = _last_field(self.tail, "aiTitle") or _last_field(self.head, "aiTitle")
        summary = _last_of_type(self.tail, "summary", "summary")
        prompt = (
            _last_field(self.tail, "lastPrompt")
            or self._first_prompt()
            or _raw_field_start(self.head, "content")
            or _raw_field_start(self.head, "text")
        )
        if prompt:  # without tagged blocks such as <command-name>...</command-name>
            prompt = _TAG_BLOCK.sub("", prompt).strip() or self.session_id[:8]
        title = custom or ai_title or summary or prompt or "(session)"
        return " ".join(title.split())

    def _first_prompt(self) -> str:
        command = [""]
        for line in _user_lines(self.head):
            if '"isMeta":true' in line or '"isMeta": true' in line:
                continue
            entry = _parse(line)
            if entry and entry.get("type") == "user":
                prompt = _prompt_text(entry, command, with_args=True)
                if prompt is not None:
                    return prompt
        return command[0]

    def cwd(self) -> str:
        """Where the session works: where it was moved to, else where it started."""
        return _last_of_type(self.tail, "relocated", "relocatedCwd") or _first_field(self.head, "cwd") or ""

    def last_message_at(self) -> float | None:
        for line in reversed(self.tail.split("\n")):
            if '"timestamp":' in line and (_is_type(line, "user") or _is_type(line, "assistant")):
                entry = _parse(line)
                if entry and isinstance(entry.get("timestamp"), str):
                    try:
                        return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        pass
        return None


# --- Lists ------------------------------------------------------------------


def _session_logs(log_dir: Path) -> Iterator[Path]:
    try:
        names = os.listdir(log_dir)
    except OSError:
        return
    for name in names:
        if name.endswith(".jsonl") and is_session_id(name[: -len(".jsonl")]):
            yield log_dir / name


def _continues(log: _Log) -> str | None:
    """The newer session `log` was continued in, if that one has messages."""
    cid = log.continued_in()
    if not cid:
        return None
    newer = log.path.parent / f"{cid}.jsonl"
    try:
        if newer.is_file() and _Log.read(newer).has_messages():
            return cid
    except OSError:
        pass
    return None


def scan_sessions(log_dirs: Iterable[Path]) -> tuple[list[Session], dict[str, str]]:
    """The sessions logged in `log_dirs`, most recently active first, and a
    map from each continuing session's ID to the one it continues."""
    sessions: list[Session] = []
    continued_from: dict[str, str] = {}
    for log_dir in log_dirs:
        for path in _session_logs(log_dir):
            try:
                log = _Log.read(path)
                if not log.head or log.is_hidden_kind() or not log.has_messages():
                    continue
                newer = _continues(log)
                if newer:
                    continued_from[newer] = log.session_id
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            active = min(log.last_message_at() or mtime, mtime)
            sessions.append(Session(log.session_id, active, log.cwd(), log.title()))
    sessions.sort(key=lambda s: s.active, reverse=True)
    return sessions, continued_from


def session_dir(paths: Paths, session_id: str) -> str | None:
    """The directory a session works in, or None if there's no such session."""
    log = paths.log_for(session_id)
    if log is None:
        return None
    try:
        return _Log.read(log).cwd() or None
    except OSError:
        return None


def _first_cwd(path: Path) -> str | None:
    """The first cwd in a log. It's usually near the top, but read on if not."""
    pattern = re.compile(r'"cwd": ?"((?:[^"\\]|\\.)*)"')
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                return _unescape(m.group(1))
    return None


def recent_projects(paths: Paths) -> list[tuple[float, str]]:
    """(last used, directory) for each directory Claude has been used in that
    still exists, most recent first. Each project's newest log says both."""
    found: dict[str, float] = {}
    for log_dir in paths.all_log_dirs():
        newest, newest_mtime = None, -1.0
        for path in _session_logs(log_dir):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = path, mtime
        if newest is None:
            continue
        try:
            cwd = _first_cwd(newest)
        except OSError:
            continue
        if cwd and os.path.isdir(cwd) and newest_mtime > found.get(cwd, -1.0):
            found[cwd] = newest_mtime
    return sorted(((t, d) for d, t in found.items()), reverse=True)
