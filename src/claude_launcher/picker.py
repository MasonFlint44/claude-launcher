"""The picker: what fzf shows, and what each key does.

bin/claude-launcher runs fzf with the options `start()` gives it. fzf calls
back into this module (through libexec/claude-launcher-core) for each key
binding, and those calls print fzf actions. Between calls, the picker's state
lives in a per-run directory:

    state.json     the view, the query, the last double-press key, ...
    projects.json  the directory list
    sessions.json  the Sessions view's rows
    header         the header fzf is showing
    accept         the cursor position and highlighted row when Enter was pressed

Views: "projects" (the directory list, the start screen), "recent" (sessions
in every directory) and "directory" (the highlighted directory's sessions).
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fzf, logs, text
from .logs import is_session_id
from .paths import CORE, Paths
from .restart import Group, RestartState
from .text import color, pad, truncate
from .text import width as text_width
from .theme import Theme

MAX_WIDTH = 120  # widest the picker gets, in columns
DIR_COLUMN = 50  # the directory list's first column
DOUBLE_PRESS_MS = 1000

PROJECTS_PROMPT = "project> "
SESSIONS_PROMPT = "session> "


@dataclass
class State:
    view: str = "projects"
    dir: str = ""  # the directory, in the "directory" view
    title: str = ""
    query: str = ""
    cols: int = 80
    width: int = 80
    key: list | None = None  # [key, time in ms] of a first press waiting for its second
    restored: list = field(default_factory=lambda: [0, 0.0, []])  # [count, latest, reasons]
    has_group_rows: bool = False  # the Sessions view shows ↻ rows
    fzf: str = "fzf"  # the fzf program the picker runs


class FzfTooOld(Exception):
    pass


@dataclass
class Pick:
    """What the picker chose: a directory, and a session to resume there."""

    dir: str
    session_id: str = ""


class Picker:
    def __init__(
        self,
        run_dir: Path,
        paths: Paths | None = None,
        theme: Theme | None = None,
        restart: RestartState | None = None,
        now=time.time,
        core: Path = CORE,
        restore_later=None,
    ):
        self.run = Path(run_dir)
        self.paths = paths or Paths.from_env()
        self.theme = theme or Theme.load()
        self.restart = restart or RestartState(self.paths)
        self.now = now
        self.core = shlex.join([str(core), "--run", str(self.run)])
        self._restore_later = restore_later or _restore_header_later
        self.state = self._load_state()

    # --- State ---------------------------------------------------------------

    def _file(self, name: str) -> Path:
        return self.run / name

    def _write(self, name: str, content: str) -> None:
        tmp = self._file(name + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, self._file(name))

    def _load_state(self) -> State:
        try:
            data = json.loads(self._file("state.json").read_text())
            return State(**{k: v for k, v in data.items() if k in State.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return State()

    def _save_state(self) -> None:
        self._write("state.json", json.dumps(asdict(self.state)))

    # --- Colors and hints ----------------------------------------------------

    def _dim(self, s: str) -> str:
        return color(s, self.theme.dim)

    def _accent(self, s: str) -> str:
        return color(s, self.theme.accent)

    def _hints(self, *pairs: tuple[str, str]) -> str:
        return "   ".join(f"{key} {self._dim(what)}" for key, what in pairs)

    def _view_hints(self) -> list[str]:
        if self.state.view == "projects":
            return [
                self._hints(("enter", "open"), ("tab", "sessions"), ("shift-tab", "dir sessions"), ("esc", "quit")),
                self._hints(("enter", "open"), ("tab", "sessions"), ("esc", "quit")),
            ]
        plain = self._hints(("enter", "resume"), ("tab/esc", "back to projects"))
        if self.state.has_group_rows:
            return [
                self._hints(
                    ("enter", "resume"),
                    ("ctrl-x", "dismiss ↻"),
                    ("alt-x", "dismiss all"),
                    ("tab/esc", "back to projects"),
                ),
                self._hints(("enter", "resume"), ("ctrl-x", "dismiss ↻"), ("tab/esc", "back")),
                plain,
            ]
        return [plain]

    def _closed_by(self) -> str:
        """What happened to the ↻ sessions, for the restart line."""
        reasons = set(self.state.restored[2])
        if reasons == {"restart"}:
            return "open when your computer restarted"
        if reasons == {"logout"}:
            return "open when your session ended"
        return "closed by a restart or logout"

    def _nudge(self) -> str:
        """The line under the title about sessions a restart or logout closed."""
        if self.state.view == "projects":
            count, latest, _ = self.state.restored
            if not count:
                return ""
            what = "1 session was" if count == 1 else f"{count} sessions were"
            when = text.clock(latest, self.now())
            return self._accent(f"↻ {what} {self._closed_by()} ({when})") + "   tab " + self._dim("to see them")
        if self.state.has_group_rows:
            return self._accent("↻") + " " + self._dim(self._closed_by())
        return ""

    def make_header(self, hints: list[str] | None = None) -> str:
        """The title, with the first set of key hints that fits right-aligned,
        then the restart line if there is one, then a blank line."""
        title = self.state.title
        top = title
        for option in hints if hints is not None else self._view_hints():
            gap = self.state.width - text_width(title) - text_width(option) - 3
            if gap >= 3:
                top = title + " " * gap + option
                break
        lines = [top]
        nudge = self._nudge()
        if nudge:
            lines.append(nudge)
        lines.append(" ")
        return "\n".join(lines)

    def _set_header(self, hints: list[str] | None = None) -> str:
        header = self.make_header(hints)
        self._write("header", header)
        return header

    # --- Rows ----------------------------------------------------------------

    def _build_projects(self) -> None:
        now = self.now()
        home = str(self.paths.home)
        home_age = ""
        rows = []
        for used, directory in logs.recent_projects(self.paths):
            if directory == home:
                home_age = text.age(used, now)
            else:
                rows.append([directory, text.age(used, now)])
        self._write("projects.json", json.dumps([[home, home_age], *rows]))

    def _projects(self) -> list[list[str]]:
        try:
            return json.loads(self._file("projects.json").read_text())
        except (OSError, ValueError):
            return [[str(self.paths.home), ""]]

    def _project_row(self, directory: str, label: str, note: str) -> str:
        return f"{directory}\t{pad(label, DIR_COLUMN)} {self._dim(note)}".rstrip(" ")

    def projects_rows(self, query: str) -> list[str]:
        """The directory list, header row first. When the query is the path of
        an existing directory, that directory comes first, shown as typed so
        it matches: with its age if it's listed, else labelled "typed path"."""
        header = "\t" + self._dim(pad("Directory", DIR_COLUMN) + " Last used")
        projects = self._projects()
        typed = typed_directory(query)
        rows = [header]
        if typed:
            age = next((a for d, a in projects if d == typed), None)
            rows.append(self._project_row(typed, query, "typed path" if age is None else age))
        rows += [self._project_row(d, self.paths.tilde(d), a) for d, a in projects if d != typed]
        return rows

    def _build_sessions(self, log_dirs: list[Path]) -> bool:
        """Write the Sessions view's rows; False if there are none."""
        sessions, continued_from = logs.scan_sessions(log_dirs)
        group = self.restart.load()
        rows = [{**asdict(s), "group": _group_key(s.id, group, continued_from)} for s in sessions]
        self._save_sessions(rows)
        return bool(rows)

    def _save_sessions(self, rows: list[dict]) -> None:
        # Sessions open at the restart first; most recently active first within each.
        rows.sort(key=lambda r: (r["group"] is not None, r["active"]), reverse=True)
        self.state.has_group_rows = any(r["group"] for r in rows)
        self._write("sessions.json", json.dumps(rows))

    def _sessions(self) -> list[dict]:
        try:
            return json.loads(self._file("sessions.json").read_text())
        except (OSError, ValueError):
            return []

    def sessions_rows(self) -> list[str]:
        """The Sessions view, header row first. A session whose directory isn't
        here (deleted, or only inside a devcontainer) has it dimmed and marked."""
        title_width = self.state.width - 42
        now = self.now()
        home = str(self.paths.home)
        out = ["\t" + self._dim("  " + pad("Last active", 11) + " " + pad("Directory", 24) + " Session")]
        for r in self._sessions():
            cwd = r["cwd"]
            short = "~" if cwd == home else (os.path.basename(cwd.rstrip("/")) or cwd)
            missing = not os.path.isdir(cwd)
            note = " (missing)" if missing else ""
            directory = truncate(short, 24 - len(note)) + note
            directory = pad(self._dim(directory) if missing else directory, 24)
            when = pad(text.age(r["active"], now), 11)
            if r["group"]:
                mark, when = self._accent("↻") + " ", self._accent(when)
            else:
                mark, when = "  ", self._dim(when)
            out.append(f"{r['id']}\t{mark}{when} {directory} {truncate(r['title'], title_width)}")
        return out

    def rows(self, view: str | None = None, query: str | None = None) -> list[str]:
        view = view or self.state.view
        if view == "projects":
            return self.projects_rows(self.state.query if query is None else query)
        return self.sessions_rows()

    # --- Views ---------------------------------------------------------------

    def _set_view(self, view: str, directory: str = "") -> str:
        """Switch to a view; the fzf actions that show it."""
        self.state.view, self.state.dir, self.state.key, self.state.query = view, directory, None, ""
        prompt = SESSIONS_PROMPT
        if view == "projects":
            self.state.title, prompt = "Select a project directory for Claude", PROJECTS_PROMPT
        elif view == "recent":
            found = self._build_sessions(self.paths.all_log_dirs())
            self.state.title = "Recent Claude sessions" if found else "No Claude sessions yet"
        else:
            found = self._build_sessions(self.paths.log_dirs_for(directory))
            where = self.paths.tilde(directory)
            self.state.title = f"Claude sessions in {where}" if found else f"No Claude sessions in {where}"
        header = self._set_header()
        self._save_state()
        return f"clear-query+change-prompt({prompt})+reload-sync({self.core} ui menu)+first+change-header:{header}"

    def start(self, cols: int, again: bool = False) -> list[str]:
        """Get the projects view ready; the fzf options that show it. `again`
        reopens the picker after a pick that didn't work out, without
        re-reading the logs."""
        if not again:
            path, version = fzf.find()
            if path is None:
                raise FzfTooOld(
                    f"claude-launcher needs fzf {fzf.show(fzf.MIN_VERSION)} or newer, and found "
                    f"{fzf.show(version)}. Run install.sh in the claude-launcher repo to get one."
                )
            for name in ("accept", "sessions.json"):
                self._file(name).unlink(missing_ok=True)
            group = self.restart.sort()
            self.state = State(restored=_restored(group), fzf=path)
            self._build_projects()
        self.state.cols = cols
        self.state.width = min(cols, MAX_WIDTH)
        self._file("accept").unlink(missing_ok=True)
        self._set_view("projects")
        self._write("rows", "".join(r + "\n" for r in self.rows()))
        return self.fzf_options()

    def fzf_options(self) -> list[str]:
        t = self.theme
        c = self.core
        return [
            self.state.fzf,
            "--print-query",
            "--layout=reverse",
            "--tiebreak=index",
            "--delimiter=\t",
            "--with-nth=2",
            "--header-lines=1",
            "--header-first",
            f"--header={self._file('header').read_text()}",
            f"--prompt={PROJECTS_PROMPT}",
            "--ansi",
            "--info=inline-right",
            f"--margin=0,{self.state.cols - self.state.width},0,0",
            f"--listen={self._file('fzf.sock')}",
            "--with-shell=bash -c",
            f"--bind=change:transform:{c} ui change {{q}}",
            f"--bind=enter:execute-silent({c} ui accept {{1}})+accept",
            f"--bind=tab:transform:{c} ui tab",
            f"--bind=btab:transform:{c} ui shift-tab {{1}}",
            f"--bind=ctrl-x:transform:{c} ui dismiss {{1}}",
            *(f"--bind={key}:transform:{c} ui key {key} {{q}}" for key in ("alt-x", "esc", "ctrl-c", "ctrl-d")),
            f"--color=fg:{t.text},fg+:{t.bright},bg+:{t.line},hl:{t.accent},hl+:{t.accent},query:{t.bright}",
            f"--color=prompt:{t.accent},pointer:{t.accent},spinner:{t.accent},info:{t.dim},header:{t.text}",
        ]

    # --- Key bindings ----------------------------------------------------------

    def on_change(self, query: str) -> str:
        """Typing. In the projects view, a typed path can add a row, so reload."""
        was_path = typed_path_like(self.state.query)
        self.state.query, self.state.key = query, None
        self._save_state()
        reload = ""
        if self.state.view == "projects" and (was_path or typed_path_like(query)):
            reload = f"reload-sync({self.core} ui menu)+"
        return f"{reload}change-header:{self._set_header()}"

    def on_tab(self) -> str:
        return self._set_view("recent" if self.state.view == "projects" else "projects")

    def on_shift_tab(self, row: str) -> str:
        # Keys pressed right behind a switch can reach fzf before the new rows
        # do, so check the row really is a directory.
        if self.state.view == "projects" and os.path.isdir(row):
            return self._set_view("directory", row)
        return ""

    def on_dismiss(self, row: str) -> str:
        """Ctrl-X: take the highlighted ↻ session out of the group."""
        if self.state.view == "projects":
            return ""
        key = next((r["group"] for r in self._sessions() if r["id"] == row and r["group"]), None)
        return self._dismiss([key]) if key else ""

    def _dismiss(self, keys: list[str] | None) -> str:
        """Take sessions out of the group (all of them, given None), and
        redraw the Sessions view in place."""
        group = self.restart.drop_all() if keys is None else self.restart.drop(keys)
        rows = self._sessions()
        for r in rows:
            if r["group"] and (keys is None or r["group"] in keys):
                r["group"] = None
        self._save_sessions(rows)
        self.state.restored = _restored(group)
        self.state.key = None
        header = self._set_header()
        self._save_state()
        return f"reload-sync({self.core} ui menu)+change-header:{header}"

    def on_key(self, key: str, query: str) -> str:
        """Esc, Ctrl-C, Ctrl-D and Alt-X.

        Like in Claude, Esc, Ctrl-C and Ctrl-D on an empty search quit with a
        second press within a second; the first press says so where the key
        hints are. Esc with text clears it on the second press; Ctrl-C clears
        it straight away and Ctrl-D deletes a character. In a Sessions view Esc
        goes straight back to the projects, and Alt-X twice dismisses every ↻
        session the view shows.
        """
        if key == "esc" and self.state.view != "projects":
            return self._set_view("projects")
        if key == "ctrl-d" and query:
            return "delete-char"
        if key == "ctrl-c" and query:
            return "clear-query"
        if key == "alt-x":
            if self.state.view == "projects" or not self.state.has_group_rows:
                return ""
            action, what = "dismiss", "dismiss all ↻"
        elif query:
            action, what = "clear-query", "clear"
        else:
            action, what = "abort", "quit"

        now_ms = int(self.now() * 1000)
        last = self.state.key
        if last and last[0] == key and now_ms - last[1] < DOUBLE_PRESS_MS:
            self.state.key = None
            self._save_state()
            if action == "dismiss":
                if self.state.view == "recent":
                    return self._dismiss(None)
                return self._dismiss(sorted({r["group"] for r in self._sessions() if r["group"]}))
            return f"{action}+change-header:{self._file('header').read_text()}"

        self.state.key = [key, now_ms]
        self._save_state()
        self._restore_later(self.run, [key, now_ms])
        hint = self.make_header([f"{key} again {self._dim('to ' + what)}"])
        return f"change-header:{hint}"

    def restore_header(self, token: list) -> str | None:
        """The header to put back after a first press, unless another key came since."""
        if self.state.key != token:
            return None
        return self._file("header").read_text()

    def on_accept(self, row: str) -> None:
        """Enter: note the cursor position and highlighted row for pick()."""
        self._write("accept", f"{os.environ.get('FZF_POS', '1')}\n{row}\n")

    # --- After fzf exits -------------------------------------------------------

    def pick(self, query: str) -> Pick | str:
        """What Enter chose, or a message saying why nothing was."""
        pos, row = 1, ""
        try:
            lines = self._file("accept").read_text().split("\n")
            pos = max(1, int(lines[0]))  # 0 when the list hadn't caught up and showed nothing
            row = lines[1]
        except (OSError, ValueError, IndexError):
            pass
        # The view on screen: Enter right behind Tab can beat the new rows to fzf.
        if is_session_id(row):
            view = "sessions"
        elif row:
            view = "projects"
        else:
            view = self.state.view
        # If Enter arrives right behind the typing (a paste ending in a
        # newline), fzf can return a row from before it caught up. So take the
        # row at the cursor's position from what the final query matches.
        rows = self.rows("projects" if view == "projects" else "sessions", query)[1:]
        choice = _nth_match(self.state.fzf, rows, query, pos)

        session_id = ""
        if is_session_id(query):
            session_id = query
        elif view != "projects":
            session_id = choice
        if session_id:
            directory = logs.session_dir(self.paths, session_id)
            if directory is None:
                return f"No session with ID {session_id}"
            group = next((r["group"] for r in self._sessions() if r["id"] == session_id and r["group"]), None)
            self.restart.drop([session_id] + ([group] if group else []))
            return Pick(resume_dir(directory, self.paths), session_id)
        if choice:
            return Pick(choice) if os.path.isdir(choice) else f"Not a directory: {choice}"
        if view != "projects" and not query:
            return "No sessions to resume"
        if typed_path_like(query):
            return f"Not a directory: {query}"
        return f"Nothing matches: {query}"


def _restored(group: Group) -> list:
    latest = group.latest
    return [len(group), latest.at if latest else 0.0, sorted(group.reasons)]


def typed_path_like(query: str) -> bool:
    return query.startswith(("/", "~"))


def typed_directory(query: str) -> str | None:
    """The directory `query` names, if it's a path to an existing one."""
    if not typed_path_like(query):
        return None
    path = os.path.normpath(os.path.expanduser(query))
    return path if os.path.isdir(path) else None


def resume_dir(directory: str, paths: Paths) -> str:
    """Where to resume a session: its directory, or ~ if that isn't here.
    Claude finds the session from any directory; it then works in ~."""
    if os.path.isdir(directory):
        return directory
    print(f"{paths.tilde(directory)} not found, so resuming from ~", file=sys.stderr)
    time.sleep(1)  # Claude clears the screen when it starts
    return str(paths.home)


def _group_key(session_id: str, group: Group, continued_from: dict[str, str]) -> str | None:
    """The session's key in the restart group: itself, or an older session it continues."""
    seen = set()
    current: str | None = session_id
    while current and current not in seen:
        if current in group.sessions:
            return current
        seen.add(current)
        current = continued_from.get(current)
    return None


def _nth_match(fzf_path: str, rows: list[str], query: str, pos: int) -> str:
    """The first field of the pos-th row fzf matches `query` to, or ""."""
    if not query:
        matched = rows
    else:
        done = subprocess.run(
            [fzf_path, f"--filter={query}", "--ansi", "--tiebreak=index", "--delimiter=\t", "--with-nth=2"],
            input="".join(r + "\n" for r in rows),
            capture_output=True,
            text=True,
        )
        matched = done.stdout.splitlines()
    if len(matched) < pos:
        return ""
    return matched[pos - 1].split("\t", 1)[0]


def _restore_header_later(run: Path, token: list) -> None:
    """In a second, put the key hints back through fzf's --listen socket,
    unless another key came first. fzf can't time anything itself."""
    if os.fork():
        return
    try:
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):  # let fzf stop waiting for this command's output
            os.dup2(devnull, fd)
        time.sleep(DOUBLE_PRESS_MS / 1000)
        header = Picker(run).restore_header(token)
        if header is not None:
            send_action(run / "fzf.sock", f"change-header:{header}")
    finally:
        os._exit(0)


def send_action(sock_path: Path, action: str) -> None:
    body = action.encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(sock_path))
        s.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
        with contextlib.suppress(OSError):
            s.recv(1024)
