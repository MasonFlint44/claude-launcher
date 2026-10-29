"""The picker: what fzf shows, and what each key does.

bin/claude-launcher runs fzf with the options `start()` gives it. fzf calls
back into this module (through libexec/claude-launcher-core) for each key
binding, and those calls print fzf actions. Between calls, the picker's state
lives in a per-run directory:

    state.json     the view, the query, the last double-press key, ...
    projects.json  the directory list: [directory, last used] pairs
    sessions.json  the Sessions view's rows
    header         the header fzf is showing
    accept         the cursor position and highlighted row when Enter was pressed

Views: "projects" (the directory list, the start screen), "recent" (sessions
in every directory, Ctrl-S) and "directory" (the sessions in a directory and
anywhere inside it, newest first, under a "+ New session" row). Enter on a
directory shows its sessions, or opens it straight away when it has none.
Going back to the directory list puts back the search and cursor it had.

Path mode: while the query in the projects view starts with / or ~, the list
is the directories in the directory typed so far, like a shell's completion.
Tab fills in the highlighted one (in the plain list too, which starts path
mode) and Esc backs up a level.

Rows are "key<TAB>shown<TAB>note" in the directory list and
"key<TAB>when<TAB>directory and title" in a Sessions view. fzf shows all but
the key and matches only one field (`NTH`), so a note like
"→ git/claude-launcher" or an age like "23h ago" can't match a query.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import fzf, logs, pathtext, text
from .logs import is_session_id
from .paths import Paths
from .pathtext import (
    below,
    expand,
    first_part,
    is_inside,
    split_path,
    typed_path_like,
    up_a_level,
    with_sep,
    without_sep,
)
from .restart import Group, RestartState
from .text import color, pad, truncate, truncate_left
from .text import width as text_width
from .theme import Theme

if TYPE_CHECKING:
    from .platforms import Platform

MAX_WIDTH = 120  # widest the picker gets, in columns
DIR_COLUMN = 50  # the directory list's first column
DOUBLE_PRESS_MS = 1000
HINT_GAP = 8  # the least space between the title and the hints beside it

# How fzf reads and matches rows, the same in the picker and in `_nth_match`.
MATCHING = ["--ansi", "--tiebreak=index", "--delimiter=\t", "--with-nth=2.."]
# The field fzf matches, after the key: the path, or a session's directory and title.
NTH = {"projects": "1", "sessions": "2"}

PROJECTS_PROMPT = "project> "
SESSIONS_PROMPT = "session> "
NEW_SESSION = "+"  # the "+ New session" row's key: this, then the directory

# Brackets fzf takes around an action's argument.
_BRACKETS = ["()", "[]", "{}", "<>", "~~", "!!", "@@", "##", "$$", "%%", "^^", "&&", "**", ";;", "//", "||"]


@dataclass
class State:
    view: str = "projects"
    dir: str = ""  # the directory, in the "directory" view
    title: str = ""
    title_path: str = ""  # a path after the title, shortened from the left to fit
    query: str = ""
    cols: int = 80
    width: int = 80
    key: list | None = None  # [key, time in ms] of a first press waiting for its second
    restored: list = field(default_factory=lambda: [0, 0.0, []])  # [count, latest, reasons]
    has_group_rows: bool = False  # the Sessions view shows ↻ rows
    back: list | None = None  # [query, cursor] the directory list had when left
    pending_pos: int = 0  # the cursor to put back once the list's rows are in
    fzf: str = "fzf"  # the fzf program the picker runs
    # How key bindings run the core, from the platform when the picker starts.
    shell: str = ""  # fzf's --with-shell
    core: str = ""  # the core's command line, with --run DIR
    listen: str = ""  # fzf's --listen: a socket path, or localhost:0 for a port


@dataclass
class DirRow:
    """A row in path mode."""

    path: str  # the row's key
    shown: str  # in the form typed, ~/... or /..., so fzf matches it
    note: str = ""
    is_parent: bool = False  # the directory typed so far


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
        restore_later=None,
    ):
        self.run = Path(run_dir)
        self.paths = paths or Paths.from_env()
        self.theme = theme or Theme.load()
        self.restart = restart or RestartState(self.paths)
        self.now = now
        self._restore_later = restore_later or _restore_header_later
        self.state = self._load_state()
        self._scanned: tuple[list[logs.Session], dict[str, str]] | None = None

    @property
    def core(self) -> str:
        """The core's command line, as key bindings run it."""
        if not self.state.core:  # only before start(), which sets it
            self._from_platform()
        return self.state.core

    def _from_platform(self, platform: Platform | None = None) -> None:
        platform = platform or _platform()
        self.state.shell = platform.fzf_shell
        self.state.core = platform.core_command(["--run", str(self.run)])
        self.state.listen = str(self._file("fzf.sock")) if platform.fzf_listen == "socket" else "localhost:0"

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

    def _hint(self, key: str, what: str) -> str:
        return f"{key} {self._dim(what)}"

    def _view_hints(self) -> list[tuple[str, bool]]:
        """The view's key hints, each with whether it always goes beside the title."""
        h = self._hint
        if self.state.view == "projects" and typed_path_like(self.state.query):
            return [
                (h("enter", "open"), True),
                (h("tab", "complete"), True),
                (h("ctrl-s", "sessions"), False),
                (h("esc", "back"), True),
                (h("ctrl-u", "clear"), False),
            ]
        if self.state.view == "projects":
            return [
                (h("enter", "open"), True),
                (h("tab", "complete"), False),
                (h("ctrl-s", "sessions"), True),
                (h("esc", "quit"), True),
            ]
        dismiss = [(h("ctrl-x", "dismiss ↻"), True), (h("alt-x", "dismiss all"), False)]
        all_sessions = [(h("ctrl-s", "all sessions"), False)] if self.state.view == "directory" else []
        return [
            (h("enter", "resume"), True),
            *(dismiss if self.state.has_group_rows else []),
            *all_sessions,
            (h("esc", "back"), True),
        ]

    def _hint_lines(self, title: str, hints: list[tuple[str, bool]]) -> list[str]:
        """The title and the hints, right-aligned: beside the title as many as
        fit (the main ones first), and the rest on lines under it rather than
        left out."""
        room = self.state.width - 3

        def joined(chosen: set[int]) -> str:
            return "   ".join(hint for i, (hint, _) in enumerate(hints) if i in chosen)

        def fits(chosen: set[int]) -> bool:
            return text_width(title) + HINT_GAP + text_width(joined(chosen)) <= room

        top = {i for i, (_, main) in enumerate(hints) if main}
        if not fits(top):
            top = set()
        for i, (_, main) in enumerate(hints):
            if not top or main:
                continue
            if not fits(top | {i}):
                break  # this one and the extras after it go under, in order
            top.add(i)
        below: list[str] = []
        for i, (hint, _) in enumerate(hints):
            if i in top:
                continue
            if below and text_width(below[-1]) + 3 + text_width(hint) <= room:
                below[-1] += "   " + hint
            else:
                below.append(hint)
        lines = [title + " " * (room - text_width(title) - text_width(joined(top))) + joined(top) if top else title]
        return lines + [" " * max(0, room - text_width(line)) + line for line in below]

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
            return self._accent(f"↻ {what} {self._closed_by()} ({when})") + "   ctrl-s " + self._dim("to see them")
        if self.state.has_group_rows:
            return self._accent("↻") + " " + self._dim(self._closed_by())
        return ""

    def make_header(self, hints: list[str] | None = None) -> str:
        """The title with the key hints (see `_hint_lines`), then the restart
        line if there is one, then a blank line. `hints` takes the hints'
        place beside the title, with the lines under it left blank so the
        list doesn't move."""
        lines = self._hint_lines(self._title(), self._view_hints())
        if hints is not None:
            usual = len(lines)
            lines = self._hint_lines(self._title(), [(h, True) for h in hints])
            lines += [" "] * (usual - len(lines))
        nudge = self._nudge()
        if nudge:
            lines.append(nudge)
        lines.append(" ")
        return "\n".join(lines)

    def _title(self) -> str:
        """The title, with any path in it shortened from the left to fit, so
        the end of the path stays in view."""
        title, path = self.state.title, self.state.title_path
        query = self.state.query
        if self.state.view == "projects" and typed_path_like(query):
            typed = split_path(query)[0]
            if os.path.isdir(expand(typed) or ""):
                title, path = "Directories in ", typed
            else:
                title, path = "No such directory: ", without_sep(typed)
        path = text.printable(path)
        return title + truncate_left(path, max(10, self.state.width - 3 - text_width(title))) if path else title

    def _set_header(self, hints: list[str] | None = None) -> str:
        header = self.make_header(hints)
        self._write("header", header)
        return header

    # --- Rows ----------------------------------------------------------------

    def _build_projects(self) -> None:
        """Save the directories Claude ran in, ~ first, then most recent first."""
        home = str(self.paths.home)
        home_used = 0.0
        rows = []
        for used, directory in logs.recent_projects(self.paths):
            if directory == home:
                home_used = used
            else:
                rows.append([directory, used])
        self._write("projects.json", json.dumps([[home, home_used], *rows]))

    def _projects(self) -> list[list]:
        try:
            return json.loads(self._file("projects.json").read_text())
        except (OSError, ValueError):
            return [[str(self.paths.home), 0.0]]

    def _age(self, used: float) -> str:
        return text.age(used, self.now()) if used else ""

    def _project_row(self, key: str, shown: str, note: str = "") -> str:
        if not note:
            return f"{key}\t{shown}"
        return f"{key}\t{pad(shown, DIR_COLUMN)}\t{self._dim(note)}"

    def projects_rows(self, query: str) -> list[str]:
        """The directory list, header row first: the directories in a typed path
        (path mode), or every directory Claude ran in."""
        header = "\t" + self._dim(pad("Directory", DIR_COLUMN) + " Last used")
        if typed_path_like(query):
            return [header] + [self._dir_row(d) for d in self.directories(query)]
        return [header] + [
            self._project_row(d, text.printable(self.paths.tilde(d)), self._age(used)) for d, used in self._projects()
        ]

    def _dir_row(self, d: DirRow) -> str:
        return self._project_row(d.path, text.printable(d.shown), d.note)

    def _note(self, own: float, inside: tuple[float, str]) -> str:
        """A directory's note in path mode: when Claude last ran in it, else
        which project inside it was used last, and when."""
        if own:
            return self._age(own)
        used, where = inside
        if not used:
            return ""
        room = max(10, self.state.width - DIR_COLUMN - 14)
        return f"→ {truncate_left(text.printable(where), room)}, {self._age(used)}"

    def directories(self, query: str) -> list[DirRow]:
        """Path mode's rows: the directory typed so far (until a name is
        started), then the directories in it. Those Claude ran in, or in
        something inside, come first, most recent first; then the rest A to Z,
        hidden ones last. One named exactly as typed goes to the top."""
        typed, partial = split_path(query)
        parent = expand(typed)
        if parent is None or not os.path.isdir(parent):
            return []

        none = (0.0, "")
        parent_own, parent_inside = 0.0, none
        own: dict[str, float] = {}  # name: when Claude last ran in it
        inside: dict[str, tuple[float, str]] = {}  # name: (when, where) for the latest project inside it
        prefix = with_sep(parent)
        for directory, used in self._projects():
            rest = below(directory, parent) if used else None
            if rest is None:
                continue
            if not rest:
                parent_own = max(parent_own, used)
                continue
            if used > parent_inside[0]:
                parent_inside = (used, rest)
            name, deeper = first_part(rest)
            if not deeper:
                own[name] = max(own.get(name, 0.0), used)
            elif used > inside.get(name, none)[0]:
                inside[name] = (used, deeper)

        rows = [] if partial else [DirRow(parent, typed, self._note(parent_own, parent_inside), is_parent=True)]
        try:
            with os.scandir(parent) as entries:
                # A name with control characters can't be typed or shown as it is.
                names = [e.name for e in entries if _is_dir(e) and text.printable(e.name) == e.name]
        except OSError:
            return rows

        def order(name: str) -> tuple:
            latest = max(own.get(name, 0.0), inside.get(name, (0.0, ""))[0])
            if name == partial:
                return (0,)
            if latest:
                return (1, -latest)
            return (2, name.startswith("."), name.casefold(), name)

        for name in sorted(names, key=order):
            rows.append(DirRow(prefix + name, typed + name, self._note(own.get(name, 0.0), inside.get(name, none))))
        return rows

    def _build_sessions(self, within: str | None = None) -> bool:
        """Write the Sessions view's rows: every session, or those that ran in
        `within` or anywhere inside it. False if there are none."""
        sessions, continued_from = self._scan()
        if within is not None:
            sessions = [s for s in sessions if s.cwd and is_inside(s.cwd, within)]
        group = self.restart.load()
        rows = [{**asdict(s), "group": _group_key(s.id, group, continued_from)} for s in sessions]
        self._save_sessions(rows)
        return bool(rows)

    def _scan(self) -> tuple[list[logs.Session], dict[str, str]]:
        """Every session, read once per call into the picker. Claude's log
        directory names can't be turned back into paths, so this reads where
        each session ran."""
        if self._scanned is None:
            self._scanned = logs.scan_sessions(self.paths.all_log_dirs())
        return self._scanned

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
        out = ["\t" + self._dim("  " + pad("Last active", 11) + " " + pad("Directory", 24) + " Session")]
        if self.state.view == "directory":
            out.append(f"{NEW_SESSION}{self.state.dir}\t \t{self._accent('+ New session')}")
        for r in self._sessions():
            cwd = r["cwd"]
            missing = not os.path.isdir(cwd)
            note = " (missing)" if missing else ""
            directory = truncate_left(self._session_dir(cwd), 24 - len(note)) + note
            directory = pad(self._dim(directory) if missing else directory, 24)
            when = pad(text.age(r["active"], now), 11)
            if r["group"]:
                mark, when = self._accent("↻") + " ", self._accent(when)
            else:
                mark, when = "  ", self._dim(when)
            out.append(f"{r['id']}\t{mark}{when}\t{directory} {truncate(r['title'], title_width)}")
        return out

    def _session_dir(self, cwd: str) -> str:
        """A session's directory as its row shows it. In a directory's view,
        the path from that directory's name down (claude-launcher/tests), or
        from ~ when that directory is ~ or above it; elsewhere, the name."""
        home = str(self.paths.home)
        within = self.state.dir
        if self.state.view == "directory" and within:
            if is_inside(home, within):
                return text.printable(self.paths.tilde(cwd))
            rest, top = below(cwd, within), pathtext.name(within)
            return text.printable(top + pathtext.SEP + rest if rest else top)
        if pathtext.same(cwd, home):
            return "~"
        return text.printable(pathtext.name(cwd))

    def rows(self, view: str | None = None, query: str | None = None) -> list[str]:
        view = view or self.state.view
        if view == "projects":
            return self.projects_rows(self.state.query if query is None else query)
        return self.sessions_rows()

    # --- Views ---------------------------------------------------------------

    def _set_view(self, view: str, directory: str = "", restore: bool = True, cursor: int = 0) -> str:
        """Switch to a view; the fzf actions that show it. Leaving the
        directory list saves its search and cursor (`cursor`, or fzf's), and
        coming back puts them back (unless `restore` is False)."""
        if self.state.view == "projects" and view != "projects":
            self.state.back = [self.state.query, cursor or _cursor()]
        query, pos = "", 0
        if view == "projects" and restore and self.state.back:
            query, pos = self.state.back
        self.state.view, self.state.dir, self.state.key, self.state.query = view, directory, None, query
        self.state.title_path = ""
        prompt = SESSIONS_PROMPT
        if view == "projects":
            self.state.title, prompt = "Select a project directory for Claude", PROJECTS_PROMPT
        elif view == "recent":
            found = self._build_sessions()
            self.state.title = "Recent Claude sessions" if found else "No Claude sessions yet"
        else:
            found = self._build_sessions(within=directory)
            self.state.title = "Claude sessions in " if found else "No Claude sessions in "
            self.state.title_path = self.paths.tilde(directory)
        # fzf moves the cursor to the top when the rows for the search come in,
        # so a cursor to put back waits for them (`on_result`).
        self.state.pending_pos = pos if pos > 1 else 0
        header = self._set_header()
        self._save_state()
        query_action = _action("change-query", query) if query else "clear-query"
        wait = "rebind(result)+" if self.state.pending_pos else ""
        nth = NTH["projects" if view == "projects" else "sessions"]
        return (
            f"{wait}{query_action}+change-prompt({prompt})+change-nth({nth})+reload-sync({self.core} ui menu)+first"
            f"+change-header:{header}"
        )

    def start(self, cols: int, again: bool = False) -> list[str]:
        """Get the projects view ready; the fzf options that show it. `again`
        reopens the picker after a pick that didn't work out, without
        re-reading the logs."""
        if not again:
            platform = _platform()
            path, version = fzf.find(fzf.private_dir(platform.data_dir(os.environ)))
            if path is None:
                raise FzfTooOld(
                    f"claude-launcher needs fzf {fzf.show(fzf.MIN_VERSION)} or newer, and found "
                    f"{fzf.show(version)}. Run install.sh in the claude-launcher repo to get one."
                )
            for name in ("accept", "sessions.json"):
                self._file(name).unlink(missing_ok=True)
            group = self.restart.sort()
            self.state = State(restored=_restored(group), fzf=path)
            self._from_platform(platform)
            self._build_projects()
        self.state.cols = cols
        self.state.width = min(cols, MAX_WIDTH)
        self._file("accept").unlink(missing_ok=True)
        self._set_view("projects", restore=False)
        self._write("rows", "".join(r + "\n" for r in self.rows()))
        return self.fzf_options()

    def fzf_options(self) -> list[str]:
        t = self.theme
        c = self.core
        return [
            self.state.fzf,
            "--print-query",
            "--layout=reverse",
            *MATCHING,
            f"--nth={NTH['projects']}",
            "--tabstop=1",  # the tab before a note shows as one space
            "--header-lines=1",
            "--header-first",
            f"--header={self._file('header').read_text()}",
            f"--prompt={PROJECTS_PROMPT}",
            "--info=inline-right",
            f"--margin=0,{self.state.cols - self.state.width},0,0",
            f"--listen={self.state.listen}",
            f"--with-shell={self.state.shell}",
            f"--bind=change:transform:{c} ui change {{q}}",
            "--bind=start:unbind(result)",  # bound only while a cursor waits to be put back
            f"--bind=result:transform:{c} ui result {{1}}",
            f"--bind=enter:transform:{c} ui enter {{1}} {{q}}",
            f"--bind=tab:transform:{c} ui tab {{q}}",
            f"--bind=ctrl-s:transform:{c} ui sessions",
            f"--bind=ctrl-x:transform:{c} ui dismiss {{1}}",
            *(f"--bind={key}:transform:{c} ui key {key} {{q}}" for key in ("alt-x", "esc", "ctrl-c", "ctrl-d")),
            f"--color=fg:{t.text},fg+:{t.bright},bg+:{t.line},hl:{t.accent},hl+:{t.accent},query:{t.bright}",
            f"--color=prompt:{t.accent},pointer:{t.accent},spinner:{t.accent},info:{t.dim},header:{t.text}",
        ]

    # --- Key bindings ----------------------------------------------------------

    def on_change(self, query: str) -> str:
        """Typing. In the projects view, a typed path has rows of its own, so reload."""
        was_path = typed_path_like(self.state.query)
        same = query == self.state.query  # going back to the list put this query back with its rows
        self.state.query, self.state.key = query, None
        if not same:
            self.state.pending_pos = 0  # typed before the list was back: the old cursor no longer fits
        self._save_state()
        reload = ""
        if self.state.view == "projects" and not same and (was_path or typed_path_like(query)):
            reload = f"reload-sync({self.core} ui menu)+"
        return f"{reload}change-header:{self._set_header()}"

    def on_tab(self, query: str | None = None) -> str:
        """Tab, in the directory list: fill in the highlighted directory, like
        a shell's completion. From the plain list, that starts path mode."""
        query = self.state.query if query is None else query
        if self.state.view != "projects":
            return ""
        # Tab right behind the typing reaches fzf while it's still loading
        # the rows, so take the row at the cursor from what the query
        # matches, as Enter does.
        if typed_path_like(query):
            rows = self.directories(query)
            key = _nth_match(self.state.fzf, [self._dir_row(d) for d in rows], query, _cursor())
            row = next((d for d in rows if d.path == key and not d.is_parent), None)
            return f"change-query:{with_sep(row.shown)}" if row else ""
        key = _nth_match(self.state.fzf, self.projects_rows(query)[1:], query, _cursor())
        if not key or not os.path.isdir(key):
            return ""
        return f"change-query:{with_sep(self.paths.tilde(key))}"

    def on_sessions(self) -> str:
        """Ctrl-S: every session; from there, back to the directory list."""
        return self._set_view("projects" if self.state.view == "recent" else "recent")

    def on_result(self, row: str) -> str:
        """The rows for the search are in: put back the directory list's
        cursor, once they're the list's and not a Sessions view's still."""
        if not self.state.pending_pos:
            return "unbind(result)"
        if not row or is_session_id(row) or row.startswith(NEW_SESSION):
            return ""  # a filter of the old rows, or nothing to put the cursor on
        pos, self.state.pending_pos = self.state.pending_pos, 0
        self._save_state()
        return f"pos({pos})+unbind(result)"

    def on_enter(self, row: str, query: str) -> str:
        """Enter. On a directory with sessions (in it or anywhere inside),
        show them; otherwise accept, and pick() works out what was chosen."""
        pos = _cursor()
        if self.state.view == "projects" and (is_session_id(row) or row.startswith(NEW_SESSION)):
            # Enter right behind going back: fzf still had a Sessions view's
            # rows. The cursor goes where it was in the directory list.
            pos, row = (self.state.back[1] if self.state.back else 1), ""
        self._write("accept", f"{pos}\n{row}\n")
        if self.state.view != "projects" or is_session_id(query):
            return "accept"
        # As in pick(): take the row at the cursor from what the query matches.
        choice = _nth_match(self.state.fzf, self.projects_rows(query)[1:], query, pos)
        if not choice or not os.path.isdir(choice) or not self._has_sessions(choice):
            return "accept"
        self.state.query = query
        return self._set_view("directory", choice, cursor=pos)

    def _has_sessions(self, directory: str) -> bool:
        return any(s.cwd and is_inside(s.cwd, directory) for s in self._scan()[0])

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
        it straight away and Ctrl-D deletes a character. In path mode Esc backs
        up a directory, back to the start screen from ~/ or /, so it never quits.
        In a Sessions view Esc goes straight back to the directory list, and Alt-X
        twice dismisses every ↻ session the view shows.
        """
        if key == "esc" and self.state.view != "projects":
            return self._set_view("projects")
        if key == "esc" and typed_path_like(query):
            up = up_a_level(query)
            return f"change-query:{up}" if up else "clear-query"
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

    # --- After fzf exits -------------------------------------------------------

    def pick(self, query: str) -> Pick | str:
        """What Enter chose, or a message saying why nothing was."""
        pos, row = 1, ""
        try:
            lines = self._file("accept").read_text().split("\n")
            pos = max(1, int(lines[0]))
            row = lines[1]
        except (OSError, ValueError, IndexError):
            pass
        # The view on screen: Enter right behind a switch can beat the new rows to fzf.
        if is_session_id(row) or row.startswith(NEW_SESSION):
            view = "sessions"
        elif row and self.state.view == "directory" and not is_session_id(query):
            # The directory row Enter was just pressed on: the second Enter of
            # a quick double press starts a new session there.
            return Pick(self.state.dir)
        elif row:
            view = "projects"
        else:
            view = self.state.view
        # If Enter arrives right behind the typing (a paste ending in a
        # newline), fzf can return a row from before it caught up. So take the
        # row at the cursor's position from what the final query matches.
        kind = "projects" if view == "projects" else "sessions"
        choice = _nth_match(self.state.fzf, self.rows(kind, query)[1:], query, pos, NTH[kind])

        session_id = ""
        if is_session_id(query):
            session_id = query
        elif choice.startswith(NEW_SESSION):
            directory = choice[len(NEW_SESSION) :]
            return Pick(directory) if os.path.isdir(directory) else f"Not a directory: {self.paths.tilde(directory)}"
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


def _platform() -> Platform:
    from .platforms import current

    return current()


def _restored(group: Group) -> list:
    latest = group.latest
    return [len(group), latest.at if latest else 0.0, sorted(group.reasons)]


def _action(name: str, arg: str) -> str:
    """An fzf action with an argument, in brackets the argument doesn't close."""
    for o, c in _BRACKETS:
        if c not in arg:
            return f"{name}{o}{arg}{c}"
    raise ValueError(f"no brackets for {arg!r}")


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()  # follows symlinks
    except OSError:
        return False


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


def _cursor() -> int:
    """The cursor's position in fzf's list, from 1. fzf says 0 when the list
    shows nothing, as when it hasn't caught up with the typing."""
    try:
        return max(1, int(os.environ.get("FZF_POS", "1")))
    except ValueError:
        return 1


def _nth_match(fzf_path: str, rows: list[str], query: str, pos: int, nth: str = NTH["projects"]) -> str:
    """The first field of the pos-th row fzf matches `query` to, or ""."""
    if not query:
        matched = rows
    else:
        done = subprocess.run(
            [fzf_path, f"--filter={query}", *MATCHING, f"--nth={nth}"],
            input="".join(r + "\n" for r in rows),
            capture_output=True,
            text=True,
        )
        matched = done.stdout.splitlines()
    if len(matched) < pos:
        return ""
    return matched[pos - 1].split("\t", 1)[0]


def _restore_header_later(run: Path, token: list) -> None:
    """In a second, put the key hints back through fzf's --listen address,
    unless another key came first. fzf can't time anything itself, so this
    runs on after the key press returns: forked where there's fork, else as a
    detached core process (Windows)."""
    if not hasattr(os, "fork"):
        from .paths import CORE

        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        args = [sys.executable, str(CORE), "--run", str(run), "ui", "restore-header", json.dumps(token)]
        subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
        )
        return
    if os.fork():
        return
    try:
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):  # let fzf stop waiting for this command's output
            os.dup2(devnull, fd)
        restore_header(run, token)
    finally:
        os._exit(0)


def restore_header(run: Path, token: list) -> None:
    time.sleep(DOUBLE_PRESS_MS / 1000)
    picker = Picker(run)
    header = picker.restore_header(token)
    if header is not None:
        send_action(picker.state.listen or str(run / "fzf.sock"), f"change-header:{header}")


def send_action(listen: str, action: str, env: dict[str, str] | os._Environ = os.environ) -> None:
    """Send fzf an action through its --listen address: a Unix socket, or
    localhost:0, whose port fzf gives its commands in FZF_PORT."""
    body = action.encode()
    key = env.get("FZF_API_KEY", "")
    request = (
        b"POST / HTTP/1.1\r\nHost: localhost\r\nx-api-key: %s\r\nContent-Length: %d\r\n\r\n" % (key.encode(), len(body))
        + body
    )
    if listen.startswith("localhost:"):
        s = socket.create_connection(("127.0.0.1", int(env.get("FZF_PORT", "0"))), timeout=2)
    else:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(listen)
    with s:
        s.sendall(request)
        with contextlib.suppress(OSError):
            s.recv(1024)
