import dataclasses
import json
import os
import re
import sys
import time

import pytest

from claude_launcher.picker import Pick, Picker
from claude_launcher.restart import Reason, RestartState
from claude_launcher.text import strip_ansi, width
from claude_launcher.theme import Theme
from conftest import FakeEvents, FakeHost, conversation, posix_only

S = os.sep  # the separator the picker builds paths with
NOW = 1_800_000_000.0


def closed(at, why="restart"):
    """An entry in restored.json."""
    return {"at": at, "why": why}


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


@pytest.fixture
def env(claude_home, tmp_path):
    clock = Clock()
    run = tmp_path / "run"
    run.mkdir()
    restart = RestartState(
        claude_home.paths,
        FakeEvents(),
        FakeHost(),
        now=clock,
    )
    restored = []

    def picker():
        return Picker(
            run,
            claude_home.paths,
            Theme.load(),
            restart,
            now=clock,
            restore_later=lambda run, token: restored.append(token),
        )

    class Env:
        pass

    e = Env()
    e.home, e.paths, e.clock, e.run, e.picker, e.restored, e.tmp = (
        claude_home.home,
        claude_home.paths,
        clock,
        run,
        picker,
        restored,
        tmp_path,
    )
    e.log = claude_home.log
    return e


def project(env, name, mtime):
    d = env.tmp / name
    d.mkdir(parents=True, exist_ok=True)
    env.log(d, conversation(f"in {name}", d), mtime=mtime)
    return d


def plain(rows):
    return [strip_ansi(r) for r in rows]


def _not_config(row):
    return not row.split("\t", 1)[0].endswith((f"{os.sep}.claude", "/.claude"))  # the fake home's Claude config


def displayed(rows):
    """What fzf shows: the fields after the first, a tab shown as a space (--tabstop=1)."""
    return [r.split("\t", 1)[1].replace("\t", " ").rstrip() for r in plain(rows) if _not_config(r)]


# WSL's own reason, as platforms/wsl gives it (test_platforms checks).
WSL_STOP = Reason("wsl-stop", "WSL stopped", "WSL stop", 30)


@pytest.fixture
def wsl(monkeypatch):
    """The picker on a platform with WSL's reason."""
    from claude_launcher import picker

    real = picker._platform()
    monkeypatch.setattr(picker, "_platform", lambda: dataclasses.replace(real, reasons=(WSL_STOP,)))


# The local midnight that starts NOW's day.
MIDNIGHT = time.mktime((*time.localtime(NOW)[:3], 0, 0, 0, 0, 0, -1))


def start(env, cols=100):
    p = env.picker()
    opts = p.start(cols)
    return p, opts


class TestProjects:
    @posix_only  # Windows doesn't allow control characters in a name
    def test_directory_name_with_an_escape(self, env):
        odd = project(env, "a\x1b[31mb", NOW - 600)
        p, _ = start(env)
        [row] = [r for r in p.rows() if r.startswith(str(odd) + "\t")]
        assert "\x1b[31m" not in row.split("\t", 1)[1] and "/ab" in row

    def test_home_first_then_most_recent(self, env):
        a = project(env, "a", NOW - 600)
        b = project(env, "b", NOW - 7200)
        p, _ = start(env)
        rows = plain(p.rows())
        assert rows[0].startswith("\tDirectory")
        keys = [r.split("\t")[0] for r in rows[1:]]
        assert keys == [str(env.home), str(a), str(b)]
        assert displayed(p.rows())[2].endswith("10m ago")

    def test_home_shows_its_age_when_used(self, env):
        env.log(env.home, conversation("hi", env.home), mtime=NOW - 120)
        p, _ = start(env)
        assert displayed(p.rows())[1].split() == ["~", "2m", "ago"]

    def test_rows_file_and_options(self, env):
        p, opts = start(env)
        assert (env.run / "rows").read_text().splitlines()[1].startswith(str(env.home) + "\t")
        assert "--header-lines=1" in opts and "--with-nth=2.." in opts and "--nth=1" in opts
        assert any(o.startswith("--bind=enter:transform:") for o in opts)
        assert any(o.startswith("--bind=ctrl-s:transform:") for o in opts)
        assert not any(o.startswith("--bind=btab") for o in opts)
        listen = "localhost:0" if sys.platform == "win32" else str(env.run / "fzf.sock")  # no Unix sockets there
        assert f"--listen={listen}" in opts

    def test_files_fzf_reads_have_plain_newlines(self, env):
        # Windows' text mode would write \r\n, and fzf would show the \r.
        project(env, "a", NOW - 600)
        start(env)
        for name in ("rows", "header"):
            assert b"\r" not in (env.run / name).read_bytes()

    def test_notes_dont_match(self, env):
        project(env, "a", NOW - 600)
        start(env)
        assert env.picker().on_enter(str(env.home), "ago") == "accept"
        assert env.picker().pick("ago") == "Nothing matches: ago"


def keys(rows):
    return [r.split("\t")[0] for r in rows[1:] if _not_config(r)]


def listed(rows):
    """What fzf shows, without the header row."""
    return displayed(rows)[1:]


def make_dirs(env, *names):
    for name in names:
        (env.home / name).mkdir(parents=True)


class TestPathMode:
    def test_directory_then_whats_in_it(self, env):
        make_dirs(env, "b", "a", "c/deep")
        (env.home / "file").write_text("")
        p, _ = start(env)
        rows = p.rows(query="~/")
        assert keys(rows) == [str(env.home), *(str(env.home / n) for n in ("a", "b", "c"))]
        shown = listed(rows)
        assert shown[0] == "~/"  # no note: Claude never ran in this home
        assert shown[1:] == ["~/a", "~/b", "~/c"]

    def test_typed_directory_only_until_a_name_is_started(self, env):
        make_dirs(env, "git/x")
        p, _ = start(env)
        assert keys(p.rows(query="~/g")) == [str(env.home / "git")]

    def test_tilde_alone_is_home(self, env):
        make_dirs(env, "a")
        p, _ = start(env)
        assert listed(p.rows(query="~"))[1] == f"~{S}a"  # ~ alone is home, in the OS's form
        assert keys(p.rows(query="~")) == keys(p.rows(query="~/"))

    def test_absolute_form_kept(self, env):
        make_dirs(env, "a")
        p, _ = start(env)
        rows = p.rows(query=f"{env.home}/")
        assert listed(rows)[1] == f"{env.home}/a"
        assert keys(rows)[1] == str(env.home / "a")

    def test_a_to_z_ignoring_case_hidden_last(self, env):
        make_dirs(env, ".cache", "Zed", "apple", "Banana", ".aaa")
        p, _ = start(env)
        assert listed(p.rows(query="~/"))[1:] == ["~/apple", "~/Banana", "~/Zed", "~/.aaa", "~/.cache"]

    def test_history_first_with_own_age_or_whats_inside(self, env):
        make_dirs(env, "aaa", "used", "parent/git/proj", "zzz")
        env.log(env.home / "used", conversation("x", env.home / "used"), mtime=NOW - 7200)
        env.log(env.home / "parent/git/proj", conversation("x", env.home / "parent/git/proj"), mtime=NOW - 60)
        p, _ = start(env)
        rows = p.rows(query="~/")
        shown = listed(rows)
        assert [r.split()[0] for r in shown[1:]] == ["~/parent", "~/used", "~/aaa", "~/zzz"]
        assert shown[1].split(None, 1)[1] == f"→ git{S}proj, 1m ago"
        assert shown[2].split(None, 1)[1] == "2h ago"
        assert shown[3] == "~/aaa"

    def test_own_age_ordered_by_newest_inside(self, env):
        make_dirs(env, "a/inner", "b")
        env.log(env.home / "a", conversation("x", env.home / "a"), mtime=NOW - 9000)
        env.log(env.home / "a/inner", conversation("x", env.home / "a/inner"), mtime=NOW - 60)
        env.log(env.home / "b", conversation("x", env.home / "b"), mtime=NOW - 600)
        p, _ = start(env)
        shown = listed(p.rows(query="~/"))
        assert shown[1].split() == ["~/a", "2h", "ago"]
        assert shown[2].split()[0] == "~/b"

    def test_long_inside_path_cut_from_the_left(self, env):
        deep = "x/" + "/".join(["directory"] * 10)
        make_dirs(env, deep)
        env.log(env.home / deep, conversation("x", env.home / deep), mtime=NOW - 60)
        p, _ = start(env)
        note = listed(p.rows(query="~/"))[1].split(None, 1)[1]
        assert note.startswith("→ …") and note.endswith(f"directory{S}directory, 1m ago")
        assert width(note) <= 100 - 50

    def test_exact_name_first(self, env):
        make_dirs(env, "proj-new", "proj")
        env.log(env.home / "proj-new", conversation("x", env.home / "proj-new"), mtime=NOW - 60)
        p, _ = start(env)
        assert keys(p.rows(query="~/proj")) == [str(env.home / "proj"), str(env.home / "proj-new")]

    def test_symlinked_directory_counts(self, env):
        make_dirs(env, "real")
        (env.home / "link").symlink_to(env.home / "real")
        (env.home / "broken").symlink_to(env.home / "nowhere")
        p, _ = start(env)
        assert listed(p.rows(query="~/"))[1:] == ["~/link", "~/real"]

    @posix_only  # Windows doesn't allow control characters in a name
    def test_odd_names_left_out(self, env):
        make_dirs(env, "ok", "bad\nname", "esc\x1b[31m")
        p, _ = start(env)
        assert listed(p.rows(query="~/"))[1:] == ["~/ok"]

    def test_missing_directory(self, env):
        p, _ = start(env)
        p.on_change("~/nope/x")
        assert p.rows() == p.rows()[:1]
        assert strip_ansi(p.make_header()).startswith("No such directory: ~/nope ")

    def test_unknown_user(self, env):
        p, _ = start(env)
        p.on_change("~no-such-user-here")
        assert len(p.rows()) == 1
        assert strip_ansi(p.make_header()).startswith("No such directory: ~no-such-user-here")

    @posix_only  # chmod can't lock a directory on Windows
    def test_unreadable_directory(self, env):
        make_dirs(env, "locked/inner")
        (env.home / "locked").chmod(0)
        try:
            p, _ = start(env)
            assert keys(p.rows(query="~/locked/")) == [str(env.home / "locked")]
            assert len(p.rows(query="~/locked/i")) == 1
        finally:
            (env.home / "locked").chmod(0o755)

    def test_title_names_the_directory(self, env):
        make_dirs(env, "git/x")
        p, _ = start(env, cols=110)
        p.on_change("~/git/x")
        assert strip_ansi(p.make_header()).startswith("Directories in ~/git/ ")
        p.on_change("")
        assert strip_ansi(p.make_header()).startswith("Select a project directory for Claude ")

    def test_long_path_in_the_title_cut_from_the_left(self, env):
        deep = "/".join(["directory"] * 12)
        make_dirs(env, deep)
        p, _ = start(env, cols=60)
        p.on_change(f"~/{deep}/")
        title = strip_ansi(p.make_header()).split("\n")[0]
        assert title.startswith("Directories in …") and title.endswith("directory/directory/")
        assert width(title) == 57

    def test_typed_directory_shows_its_own_age(self, env):
        make_dirs(env, "git/x")
        env.log(env.home / "git", conversation("x", env.home / "git"), mtime=NOW - 7200)
        p, _ = start(env)
        assert listed(p.rows(query="~/git/"))[0].split() == ["~/git/", "2h", "ago"]

    def test_typed_directory_shows_the_latest_project_inside(self, env):
        make_dirs(env, "git/a/b", "git/c")
        env.log(env.home / "git/a/b", conversation("x", env.home / "git/a/b"), mtime=NOW - 60)
        env.log(env.home / "git/c", conversation("x", env.home / "git/c"), mtime=NOW - 600)
        p, _ = start(env)
        assert listed(p.rows(query="~/git/"))[0].split(None, 1) == ["~/git/", f"→ a{S}b, 1m ago"]

    @posix_only  # on Windows // starts a share
    def test_doubled_leading_slash_is_the_same_directory(self, env):
        # Linux reads //home as /home, so its history still applies.
        make_dirs(env, "git/x")
        env.log(env.home / "git", conversation("x", env.home / "git"), mtime=NOW - 7200)
        p, _ = start(env)
        typed = "/" + str(env.home / "git") + "/"
        rows = p.rows(query=typed)
        assert keys(rows)[0] == str(env.home / "git")
        assert listed(rows)[0].split() == [typed, "2h", "ago"]
        assert listed(rows)[1] == typed + "x"  # shown as typed, so Tab completes it
        assert keys(rows)[1] == str(env.home / "git/x")

    def test_hints(self, env):
        make_dirs(env, "git/claude-launcher")
        p, _ = start(env, cols=110)
        p.on_change("~/git/claude-launcher/")
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].endswith("enter open   tab complete   ctrl-s sessions   esc back")
        assert lines[1].endswith("ctrl-u clear") and width(lines[1]) == 107
        p.on_change("abc")
        assert "ctrl-s sessions   esc quit" in strip_ansi(p.make_header())
        assert "shift-tab" not in strip_ansi(p.make_header())


class TestPathKeys:
    def test_tab_completes(self, env, monkeypatch):
        make_dirs(env, "git/claude-launcher", "git/command-center")
        monkeypatch.setenv("FZF_POS", "1")
        p, _ = start(env)
        p.on_change("~/git/cl")
        assert p.on_tab("~/git/cl") == "change-query:~/git/claude-launcher/"

    def test_tab_takes_the_row_at_the_cursor(self, env, monkeypatch):
        make_dirs(env, "git/aa", "git/ab", "git/b")
        monkeypatch.setenv("FZF_POS", "2")
        p, _ = start(env)
        assert p.on_tab("~/git/a") == "change-query:~/git/ab/"

    def test_tab_before_the_list_caught_up(self, env, monkeypatch):
        make_dirs(env, "git/claude-launcher", "git/command-center")
        monkeypatch.setenv("FZF_POS", "0")  # fzf was still loading the rows
        p, _ = start(env)
        assert p.on_tab("~/git/cl") == "change-query:~/git/claude-launcher/"

    def test_tab_on_the_typed_directory_or_nothing_does_nothing(self, env, monkeypatch):
        make_dirs(env, "git")
        monkeypatch.setenv("FZF_POS", "1")
        p, _ = start(env)
        assert p.on_tab("~/git/") == ""  # the cursor is on ~/git/ itself
        assert p.on_tab("~/git/zzz") == ""  # no rows
        assert p.on_tab("~/nope/") == ""

    def test_tab_in_the_plain_list_completes_the_highlighted_directory(self, env, monkeypatch):
        make_dirs(env, "git/proj")
        env.log(env.home / "git/proj", conversation("x", env.home / "git/proj"), mtime=NOW - 60)
        p, _ = start(env)
        monkeypatch.setenv("FZF_POS", "2")
        assert p.on_tab("") == f"change-query:~{S}git{S}proj{S}"  # from the row's own path
        assert p.on_tab("pro") == ""  # the second row of what "pro" matches: none
        monkeypatch.setenv("FZF_POS", "1")
        assert p.on_tab("") == f"change-query:~{S}"
        assert p.on_tab("pro") == f"change-query:~{S}git{S}proj{S}"

    def test_tab_in_a_sessions_view_does_nothing(self, env):
        env.log("/tmp", conversation("hello", "/tmp"))
        p, _ = start(env)
        p.on_sessions()
        assert env.picker().on_tab("") == ""

    @pytest.mark.parametrize(
        ("query", "up"),
        [
            ("~/git/cla", "~/git/"),
            ("~/git/", "~/"),
            ("~/", ""),
            ("~", ""),
            ("~mason", ""),
            ("/usr/lib/", "/usr/"),
            ("/usr/", "/"),
            ("/us", "/"),
            ("/", ""),
        ],
    )
    def test_esc_backs_up(self, env, query, up):
        start(env)
        out = env.picker().on_key("esc", query)
        assert out == (f"change-query:{up}" if up else "clear-query")

    def test_esc_out_of_a_path_never_quits(self, env):
        start(env)
        assert env.picker().on_key("esc", "~/") == "clear-query"
        env.picker().on_change("")
        env.clock.t += 0.2
        assert "esc again" in strip_ansi(env.picker().on_key("esc", ""))  # only now does it ask

    def test_enter_opens_the_highlighted_directory(self, env, monkeypatch):
        make_dirs(env, "git/claude-launcher", "git/other")
        start(env)
        monkeypatch.setenv("FZF_POS", "1")
        assert env.picker().on_enter(str(env.home / "git/claude-launcher"), "~/git/cla") == "accept"
        assert env.picker().pick("~/git/cla") == Pick(str(env.home / "git/claude-launcher"))

    def test_enter_on_the_typed_directory(self, env, monkeypatch):
        make_dirs(env, "git/x")
        start(env)
        monkeypatch.setenv("FZF_POS", "1")
        assert env.picker().on_enter(str(env.home / "git"), "~/git/") == "accept"
        assert env.picker().pick("~/git/") == Pick(str(env.home / "git"))

    def test_a_path_no_directory_matches_opens_nothing(self, env, monkeypatch):
        make_dirs(env, "git/command-center")
        project(env, "command-center", NOW - 60)  # history no longer matches a typed path
        start(env)
        (env.run / "accept").unlink(missing_ok=True)
        assert env.picker().pick("~/git/cmdx") == "Not a directory: ~/git/cmdx"


class TestHeader:
    def test_hints_right_aligned(self, env):
        p, _ = start(env, cols=110)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].startswith("Select a project directory for Claude")
        assert lines[0].endswith("enter open   tab complete   ctrl-s sessions   esc quit")
        assert width(lines[0]) == 107 and lines[1] == " "

    def test_narrow_window_moves_extra_hints_under(self, env):
        p, _ = start(env, cols=90)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].endswith("enter open   ctrl-s sessions   esc quit")
        assert lines[1] == " " * (87 - len("tab complete")) + "tab complete"

    def test_hints_keep_their_distance_from_the_title(self, env):
        make_dirs(env, "git/claude-launcher")
        p, _ = start(env, cols=110)
        p.on_change("~/git/claude-launcher/")
        top = strip_ansi(p.make_header()).split("\n")[0]
        assert "claude-launcher/" + " " * 8 in top  # ctrl-u too would leave only 4
        assert not top.endswith("ctrl-u clear")

    def test_extras_move_under_in_order(self, env):
        make_dirs(env, "git/claude-launcher")
        p, _ = start(env, cols=100)
        p.on_change("~/git/claude-launcher/")
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].endswith("enter open   tab complete   esc back")
        assert lines[1].endswith("ctrl-s sessions   ctrl-u clear")

    def test_very_narrow_window_puts_all_hints_under(self, env):
        p, _ = start(env, cols=40)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0] == "Select a project directory for Claude"
        assert [line.strip() for line in lines[1:3]] == [
            "enter open   tab complete",
            "ctrl-s sessions   esc quit",
        ]
        assert all(width(line) <= 37 for line in lines)

    def test_sessions_hints(self, env):
        p, _ = start(env, cols=100)
        p.on_sessions()
        assert strip_ansi(p.make_header()).split("\n")[0].endswith("enter resume   esc back")

    def test_directory_sessions_hints(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({sid_in(env, d): closed(NOW - 300)}))
        p, _ = start(env, cols=120)
        monkeypatch.setenv("FZF_POS", "2")
        p.on_enter(str(d), "")
        # Where the hints wrap depends on the title, which holds the test's own path.
        lines = strip_ansi(p.make_header()).split("\n")[:2]
        parts = {part for line in lines for part in re.split(r" {3,}", line.strip())}
        hints = {"enter resume", "ctrl-x dismiss ↻", "alt-x dismiss all", "ctrl-s all sessions", "esc back"}
        assert hints <= parts and len(parts - hints) == 1  # and the title

    def test_press_again_keeps_the_header_height(self, env):
        p, _ = start(env, cols=90)
        usual = strip_ansi(p.make_header()).split("\n")
        asked = strip_ansi(env.picker().on_key("esc", "").removeprefix("change-header:")).split("\n")
        assert asked[0].endswith("   esc again to quit") and width(asked[0]) == 87
        assert asked[1] == " " and len(asked) == len(usual)

    def test_capped_width(self, env):
        p, opts = start(env, cols=200)
        assert "--margin=0,80,0,0" in opts

    def test_restart_nudge(self, env):
        sid = env.log("/tmp", conversation("x", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({sid: closed(NOW - 300)}))
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[1].strip() == "tab complete"
        assert "1 session was open when your computer restarted" in lines[2]
        assert lines[2].endswith("ctrl-s to see them")

    def test_logout_nudge(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300, "logout"), b: closed(NOW - 300, "logout")}))
        p, _ = start(env)
        assert "2 sessions were open when you logged out" in strip_ansi(p.make_header())
        p.on_sessions()
        assert "↻ open when you logged out" in strip_ansi(p.make_header())

    def test_wsl_stop_nudge(self, env, wsl):
        a = env.log("/tmp", conversation("a", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300, "wsl-stop")}))
        p, _ = start(env)
        assert "1 session was open when WSL stopped" in strip_ansi(p.make_header())

    def test_one_line_per_event_newest_first(self, env, wsl):
        a, b, c = (env.log("/tmp", conversation(n, "/tmp")) for n in "abc")
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(
            json.dumps({a: closed(NOW - 300, "wsl-stop"), b: closed(NOW - 9000), c: closed(NOW - 9000)})
        )
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")
        assert "↻ 1 session was open when WSL stopped" in lines[2] and lines[2].endswith("ctrl-s to see them")
        assert lines[3].startswith("↻ 2 sessions were open when your computer restarted (")
        assert lines[4] == " "

    def test_events_past_three_share_a_line(self, env):
        ids = [env.log("/tmp", conversation(f"s{i}", "/tmp")) for i in range(5)]
        env.paths.state.mkdir(parents=True)
        restored = {sid: closed(NOW - 1000 * (i + 1), "logout") for i, sid in enumerate(ids[:3])}
        restored |= {ids[3]: closed(NOW - 9000), ids[4]: closed(NOW - 9000)}
        env.paths.restored.write_text(json.dumps(restored))
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")[2:-1]
        assert len(lines) == 3
        assert lines[1].startswith("↻ 1 session was open when you logged out")
        assert lines[2] == "↻ 3 more from earlier"

    def test_exactly_three_events_get_a_line_each(self, env, wsl):
        a, b, c = (env.log("/tmp", conversation(n, "/tmp")) for n in "abc")
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(
            json.dumps({a: closed(NOW - 300, "wsl-stop"), b: closed(NOW - 3000, "logout"), c: closed(NOW - 9000)})
        )
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")[2:-1]
        assert [line.split(" (")[0] for line in lines] == [
            "↻ 1 session was open when WSL stopped",
            "↻ 1 session was open when you logged out",
            "↻ 1 session was open when your computer restarted",
        ]

    def test_one_restart_read_at_slightly_different_times_is_one_line(self, env):
        a, b = (env.log("/tmp", conversation(n, "/tmp")) for n in "ab")
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300), b: closed(NOW - 300.01)}))
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")
        assert "↻ 2 sessions were open when your computer restarted" in lines[2] and lines[3] == " "

    def test_a_reason_this_platform_doesnt_know(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(MIDNIGHT + 60, "gone")}))
        p, _ = start(env)
        assert "↻ 1 session was open when Claude was stopped" in strip_ansi(p.make_header())
        p.on_sessions()
        assert "↻ open when Claude was stopped" in strip_ansi(p.make_header())
        assert displayed(p.rows())[1].startswith("↻ today stopped ")

    def test_sessions_view_says_all_three_reasons(self, env, wsl):
        a, b, c = (env.log("/tmp", conversation(n, "/tmp")) for n in "abc")
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(
            json.dumps({a: closed(NOW - 300, "wsl-stop"), b: closed(NOW - 3000, "logout"), c: closed(NOW - 9000)})
        )
        p, _ = start(env)
        p.on_sessions()
        header = strip_ansi(p.make_header())
        assert "↻ open when your computer restarted, you logged out or WSL stopped" in header

    def test_a_directorys_view_says_only_its_rows_reasons(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        elsewhere = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(
            json.dumps({sid_in(env, d): closed(NOW - 300, "logout"), elsewhere: closed(NOW - 9000)})
        )
        p, _ = start(env, cols=120)
        monkeypatch.setenv("FZF_POS", "2")
        p.on_enter(str(d), "")
        header = strip_ansi(p.make_header())
        assert "↻ open when you logged out" in header and "restarted" not in header

    def test_a_directorys_view_without_its_own_restored_rows_says_nothing(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        elsewhere = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({elsewhere: closed(NOW - 300)}))
        p, _ = start(env, cols=120)
        monkeypatch.setenv("FZF_POS", "2")
        p.on_enter(str(d), "")
        assert "↻" not in strip_ansi(p.make_header())

    def test_each_reason_has_its_texts(self, env, wsl):
        # Whatever the platform's reasons, the picker has a header and a row text for each, in the order they win.
        from claude_launcher.restart import SHARED, reasons

        p, _ = start(env)
        expected = reasons([WSL_STOP])
        assert [r[0] for r in p.state.reasons] == [r.key for r in expected] == ["restart", "logout", "wsl-stop"]
        assert p.state.reasons == [[r.key, r.header, r.row, r.rank] for r in expected]
        assert all(r.header and r.row for r in expected)
        assert {r.key for r in SHARED} <= {r[0] for r in p.state.reasons}

    def test_sessions_view_says_each_reason_shown(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300, "logout"), b: closed(NOW - 9000)}))
        p, _ = start(env)
        p.on_sessions()
        assert "↻ open when your computer restarted or you logged out" in strip_ansi(p.make_header())


class TestSessions:
    def test_ctrl_s_toggles(self, env):
        env.log("/tmp", conversation("hello", "/tmp"))
        p, _ = start(env)
        out = p.on_sessions()
        assert out.startswith("clear-query+change-prompt(session> )+change-nth(2)+reload-sync(")
        assert "Recent Claude sessions" in out
        assert displayed(p.rows())[1].split()[-1] == "hello"
        assert "Select a project directory" in env.picker().on_sessions()

    def test_no_sessions(self, env):
        p, _ = start(env)
        assert "No Claude sessions yet" in p.on_sessions()

    def test_missing_directory_marked(self, env):
        env.log("/gone/proj", conversation("hello", "/gone/proj"))
        p, _ = start(env)
        p.on_sessions()
        assert "proj (missing)" in displayed(p.rows())[1]

    def test_restart_group_first_and_marked(self, env):
        older = env.log("/tmp", conversation("open at restart", "/tmp"), mtime=NOW - 9000)
        env.log("/tmp", conversation("recent", "/tmp"), mtime=NOW - 60)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({older: closed(MIDNIGHT + 60)}))
        p, _ = start(env)
        p.on_sessions()
        shown = displayed(p.rows())
        assert shown[1].startswith("↻ ") and shown[1].endswith("open at restart")
        assert shown[1].split()[1:3] == ["today", "restart"]  # what closed it, not its age
        assert not shown[2].startswith("↻")
        assert shown[2].endswith("recent")
        assert "ctrl-x dismiss ↻" in strip_ansi(p.make_header())

    @pytest.mark.parametrize(
        ("at", "why", "when"),
        [
            (MIDNIGHT + 60, "restart", "today restart"),
            (MIDNIGHT - 60, "logout", "yesterday logout"),
            (MIDNIGHT - 86400 + 60, "logout", "yesterday logout"),
            (MIDNIGHT - 86400 - 60, "restart", time.strftime("%a", time.localtime(MIDNIGHT - 86400 - 60)) + " restart"),
            (MIDNIGHT - 5 * 86400, "wsl-stop", time.strftime("%a", time.localtime(MIDNIGHT - 5 * 86400)) + " WSL stop"),
        ],
    )
    def test_a_restored_rows_day_and_reason(self, env, wsl, at, why, when):
        sid = env.log("/tmp", conversation("x", "/tmp"), mtime=NOW - 9000)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({sid: closed(at, why)}))
        p, _ = start(env)
        p.on_sessions()
        assert displayed(p.rows())[1].startswith(f"↻ {when} ")

    @pytest.mark.parametrize(("why", "column"), [(None, 12), ("logout", 16), ("restart", 17), ("wsl-stop", 18)])
    def test_the_first_column_is_as_wide_as_its_rows(self, env, wsl, why, column):
        sid = env.log("/tmp", conversation("a title long enough to be cut " * 4, "/tmp"), mtime=NOW - 9000)
        if why:
            env.paths.state.mkdir(parents=True)
            env.paths.restored.write_text(json.dumps({sid: closed(MIDNIGHT - 60, why)}))
        p, _ = start(env, cols=80)
        p.on_sessions()
        heading, row = plain(p.rows())[:2]
        assert heading.split()[:2] == (["When", "Directory"] if why else ["Last", "active"])
        assert heading.index("Directory") == 1 + 2 + column + 1  # the tab, the mark, the column, a space
        shown = displayed(p.rows())[1]
        assert width(shown) == 77  # what fzf leaves of 80 columns, filled
        assert width(shown[shown.index("a title") :]) == 80 - 31 - column  # at 80 columns, at least 31 for the title

    def test_ages_and_marks_dont_match(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        sid = sid_in(env, d)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({sid: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_sessions()
        for query in ("ago", "↻"):
            assert env.picker().pick(query) == f"Nothing matches: {query}"
        assert env.picker().pick("proj") == Pick(str(d), sid)  # the directory
        assert env.picker().pick("in proj") == Pick(str(d), sid)  # the title

    def test_new_session_row_matches_its_name(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        start(env)
        monkeypatch.setenv("FZF_POS", "2")
        env.picker().on_enter(str(d), "")
        (env.run / "accept").write_text("1\n\n")  # Enter on what "new" matched
        assert env.picker().pick("new") == Pick(str(d))
        assert env.picker().pick("ago") == "Nothing matches: ago"

    def test_each_view_tells_fzf_what_to_match(self, env):
        p, opts = start(env)
        assert "--nth=1" in opts
        assert "+change-nth(2)+" in p.on_sessions()
        assert "+change-nth(1)+" in env.picker().on_sessions()

    def test_long_directory_in_the_title_cut_from_the_left(self, env, monkeypatch):
        d = project(env, "/".join(["directory"] * 12), NOW - 60)
        p, _ = start(env, cols=60)
        monkeypatch.setenv("FZF_POS", "2")
        p.on_enter(str(d), "")
        title = strip_ansi(p.make_header()).split("\n")[0]
        assert title.startswith("Claude sessions in …") and title.endswith(f"directory{S}directory")
        assert width(title) == 57


def sid_in(env, d):
    return next(p.stem for p in env.paths.log_dirs_for(str(d))[0].glob("*.jsonl"))


# Before the test logs' message timestamps, so a log's mtime is when it was last active.
EARLY = 1_700_000_000.0


class TestDirectorySessions:
    """Enter on a directory: its sessions, and those anywhere inside it."""

    def enter(self, env, monkeypatch, row, query="", pos=1):
        monkeypatch.setenv("FZF_POS", str(pos))
        return env.picker().on_enter(str(row), query)

    def test_enter_shows_a_directorys_sessions(self, env, monkeypatch):
        a = project(env, "a", NOW - 60)
        project(env, "b", NOW - 120)
        start(env)
        out = self.enter(env, monkeypatch, a, pos=2)
        assert "Claude sessions in" in out
        rows = env.picker().rows()
        assert displayed(rows)[1] == "  + New session"
        assert displayed(rows)[2].split()[2:] == ["a", "in", "a"]
        assert keys(rows) == [f"+{a}", sid_in(env, a)]

    def test_enter_takes_the_row_at_the_cursor_from_the_final_query(self, env, monkeypatch):
        a = project(env, "alpha", NOW - 60)
        project(env, "beta", NOW - 120)
        start(env)
        # fzf hadn't caught up with the paste: it reported home as highlighted.
        assert "Claude sessions in" in self.enter(env, monkeypatch, env.home, "alpha")
        assert env.picker().state.dir == str(a)

    def test_enter_opens_a_directory_without_sessions(self, env, monkeypatch):
        make_dirs(env, "new")
        start(env)
        assert self.enter(env, monkeypatch, env.home / "new", "~/new") == "accept"
        assert env.picker().pick("~/new") == Pick(str(env.home / "new"))

    def test_sessions_the_picker_leaves_out_dont_count(self, env, monkeypatch):
        make_dirs(env, "quiet")
        env.log(env.home / "quiet", [{"type": "mode", "mode": "normal"}])  # no messages
        start(env)
        assert self.enter(env, monkeypatch, env.home / "quiet", "~/quiet") == "accept"

    def test_sessions_only_in_a_subdirectory_count(self, env, monkeypatch):
        make_dirs(env, "proj/sub")
        env.log(env.home / "proj/sub", conversation("deeper", env.home / "proj/sub"), mtime=EARLY - 60)
        start(env)
        assert "Claude sessions in" in self.enter(env, monkeypatch, env.home / "proj", "~/proj")
        assert displayed(env.picker().rows())[2].split()[2:] == [f"proj{S}sub", "deeper"]

    def test_any_depth_and_nothing_outside(self, env, monkeypatch):
        make_dirs(env, "proj/a/b", "project-other")
        here = env.log(env.home / "proj", conversation("here", env.home / "proj"), mtime=EARLY - 600)
        deep = env.log(env.home / "proj/a/b", conversation("deep", env.home / "proj/a/b"), mtime=EARLY - 60)
        env.log(env.home / "project-other", conversation("sibling", env.home / "project-other"), mtime=EARLY - 30)
        start(env)
        self.enter(env, monkeypatch, env.home / "proj", "~/proj")
        assert keys(env.picker().rows()) == [f"+{env.home / 'proj'}", deep, here]

    def test_restart_group_first_then_newest_first(self, env, monkeypatch):
        make_dirs(env, "proj/sub")
        old = env.log(env.home / "proj", conversation("old", env.home / "proj"), mtime=EARLY - 9000)
        mid = env.log(env.home / "proj", conversation("mid", env.home / "proj"), mtime=EARLY - 600)
        new = env.log(env.home / "proj/sub", conversation("new", env.home / "proj/sub"), mtime=EARLY - 60)
        group = env.log(env.home / "proj/sub", conversation("open", env.home / "proj/sub"), mtime=EARLY - 99000)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({group: closed(NOW - 300)}))
        start(env)
        self.enter(env, monkeypatch, env.home / "proj", "~/proj")
        assert keys(env.picker().rows())[1:] == [group, new, mid, old]

    def test_home_follows_the_same_rule(self, env, monkeypatch):
        make_dirs(env, "x")
        at_home = env.log(env.home, conversation("at home", env.home), mtime=EARLY - 600)
        inside = env.log(env.home / "x", conversation("inside", env.home / "x"), mtime=EARLY - 60)
        elsewhere = env.tmp / "elsewhere"
        elsewhere.mkdir()
        env.log(elsewhere, conversation("elsewhere", elsewhere), mtime=EARLY - 30)
        start(env)
        self.enter(env, monkeypatch, env.home)
        rows = env.picker().rows()
        assert keys(rows) == [f"+{env.home}", inside, at_home]
        assert [r.split()[2] for r in displayed(rows)[2:]] == [f"~{S}x", "~"]

    @posix_only  # Windows has a root per drive; see test_pathtext_windows
    def test_root_holds_everything(self, env, monkeypatch):
        env.log("/tmp", conversation("elsewhere", "/tmp"), mtime=EARLY - 30)
        env.log(env.home, conversation("at home", env.home), mtime=EARLY - 60)
        start(env)
        self.enter(env, monkeypatch, "/", "/")
        rows = env.picker().rows()
        assert [r.split()[2] for r in displayed(rows)[2:]] == ["/tmp", "~"]

    def test_directory_column_starts_at_the_directorys_name(self, env, monkeypatch):
        make_dirs(env, "git/proj/tests")
        env.log(env.home / "git/proj", conversation("a", env.home / "git/proj"), mtime=EARLY - 600)
        env.log(env.home / "git/proj/tests", conversation("b", env.home / "git/proj/tests"), mtime=EARLY - 60)
        start(env)
        self.enter(env, monkeypatch, env.home / "git/proj", "~/git/proj")
        assert [r.split()[2] for r in displayed(env.picker().rows())[2:]] == [f"proj{S}tests", "proj"]

    def test_a_pasted_session_id_resumes(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        sid = sid_in(env, d)
        start(env)
        assert self.enter(env, monkeypatch, "", sid) == "accept"
        assert env.picker().pick(sid) == Pick(str(d), sid)

    def test_a_session_row_is_accepted(self, env, monkeypatch):
        start(env)
        assert self.enter(env, monkeypatch, "0b2f0000-0000-0000-0000-000000000000") == "accept"

    def test_new_session_row_starts_a_new_session(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        start(env)
        self.enter(env, monkeypatch, d, pos=2)
        assert self.enter(env, monkeypatch, f"+{d}") == "accept"
        assert env.picker().pick("") == Pick(str(d))

    def test_a_session_resumes_in_its_own_directory(self, env, monkeypatch):
        make_dirs(env, "proj/sub")
        sid = env.log(env.home / "proj/sub", conversation("x", env.home / "proj/sub"), mtime=EARLY - 60)
        start(env)
        self.enter(env, monkeypatch, env.home / "proj", "~/proj")
        self.enter(env, monkeypatch, sid, pos=2)
        assert env.picker().pick("") == Pick(str(env.home / "proj/sub"), sid)

    def test_second_enter_ahead_of_the_rows_starts_a_new_session(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        start(env)
        self.enter(env, monkeypatch, d, pos=2)
        # fzf still showed the directory row when the second Enter came.
        assert self.enter(env, monkeypatch, d, pos=2) == "accept"
        assert env.picker().pick("") == Pick(str(d))

    def test_new_session_row_can_be_filtered_out(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        start(env)
        self.enter(env, monkeypatch, d, pos=2)
        (env.run / "accept").unlink()
        assert env.picker().pick("zzzz") == "Nothing matches: zzzz"

    def test_going_back_puts_the_search_and_cursor_back(self, env, monkeypatch):
        project(env, "proj-a", NOW - 60)
        b = project(env, "proj-b", NOW - 120)
        start(env)
        self.enter(env, monkeypatch, b, "proj", pos=2)
        out = env.picker().on_key("esc", "")
        assert out.startswith("rebind(result)+change-query(proj)+change-prompt(project> )+change-nth(1)+reload-sync(")
        assert env.picker().state.query == "proj"
        # The cursor goes back once the list's rows are in, not a Sessions view's.
        assert env.picker().on_result(sid_in(env, b)) == ""
        assert env.picker().on_result("") == ""  # the old rows, filtered with the list's search
        assert env.picker().on_result(str(b)) == "pos(2)+unbind(result)"
        assert env.picker().on_result(str(b)) == "unbind(result)"

    def test_going_back_from_all_sessions_too(self, env, monkeypatch):
        project(env, "proj-b", EARLY - 120)
        project(env, "proj-a", EARLY - 60)
        start(env)
        monkeypatch.setenv("FZF_POS", "3")
        env.picker().on_change("proj")
        env.picker().on_sessions()
        out = env.picker().on_sessions()
        assert out.startswith("rebind(result)+change-query(proj)+")
        assert env.picker().on_result("x") == "pos(3)+unbind(result)"

    def test_enter_right_behind_going_back(self, env, monkeypatch):
        a = project(env, "proj-a", EARLY - 120)
        b = project(env, "proj-b", EARLY - 60)
        start(env)
        self.enter(env, monkeypatch, a, "proj", pos=2)
        env.picker().on_key("esc", "")
        # fzf still showed the Sessions view's rows, with the cursor on the first session.
        out = self.enter(env, monkeypatch, sid_in(env, b), "proj", pos=2)
        assert "Claude sessions in" in out and env.picker().state.dir == str(a)
        assert env.picker().state.back == ["proj", 2]

    def test_typing_before_the_list_is_back_drops_the_old_cursor(self, env, monkeypatch):
        a = project(env, "proj-a", EARLY - 120)
        project(env, "proj-b", EARLY - 60)
        start(env)
        self.enter(env, monkeypatch, a, "proj", pos=2)
        env.picker().on_key("esc", "")
        env.picker().on_change("proj-")
        assert env.picker().on_result(str(a)) == "unbind(result)"

    def test_a_pasted_session_id_beats_a_late_row(self, env, monkeypatch):
        d = project(env, "proj", EARLY - 60)
        sid = sid_in(env, d)
        start(env)
        self.enter(env, monkeypatch, d, pos=2)
        (env.run / "accept").write_text(f"1\n{d}\n")  # fzf still showed the directory row
        assert env.picker().pick(sid) == Pick(str(d), sid)

    def test_a_query_with_brackets_goes_back_intact(self, env, monkeypatch):
        d = project(env, "a(b)", NOW - 60)
        start(env)
        self.enter(env, monkeypatch, d, "a(b)")
        assert env.picker().on_key("esc", "").startswith("change-query[a(b)]+")

    def test_typing_the_restored_query_doesnt_reload_again(self, env, monkeypatch):
        make_dirs(env, "git/proj")
        env.log(env.home / "git/proj", conversation("x", env.home / "git/proj"), mtime=EARLY - 60)
        start(env)
        env.picker().on_change("~/git/p")
        self.enter(env, monkeypatch, env.home / "git/proj", "~/git/p")
        env.picker().on_key("esc", "")
        assert env.picker().on_change("~/git/p").startswith("change-header:")

    def test_ctrl_s_from_a_directorys_sessions_shows_all(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        env.log("/tmp", conversation("elsewhere", "/tmp"), mtime=EARLY - 30)
        start(env)
        self.enter(env, monkeypatch, d, pos=2)
        assert "Recent Claude sessions" in env.picker().on_sessions()
        assert len(keys(env.picker().rows())) == 2

    def test_a_failed_pick_starts_the_list_fresh(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        p, _ = start(env)
        self.enter(env, monkeypatch, d, "proj")
        p = env.picker()
        p.start(100, again=True)
        assert p.state.query == ""


class TestKeys:
    def test_esc_twice_quits(self, env):
        p, _ = start(env)
        first = env.picker().on_key("esc", "")
        assert "esc again" in strip_ansi(first) and "to quit" in strip_ansi(first)
        assert env.restored  # the hints come back after a second
        env.clock.t += 0.5
        assert env.picker().on_key("esc", "").startswith("abort+change-header:")

    def test_esc_twice_too_slowly(self, env):
        start(env)
        env.picker().on_key("esc", "")
        env.clock.t += 1.5
        assert env.picker().on_key("esc", "").startswith("change-header:")

    def test_esc_with_text_clears(self, env):
        start(env)
        env.picker().on_key("esc", "abc")
        env.clock.t += 0.2
        assert env.picker().on_key("esc", "abc").startswith("clear-query+")

    def test_typing_resets_a_first_press(self, env):
        start(env)
        env.picker().on_key("esc", "")
        env.picker().on_change("a")
        assert env.picker().on_key("esc", "").startswith("change-header:")

    def test_ctrl_c_and_ctrl_d_with_text(self, env):
        p, _ = start(env)
        assert p.on_key("ctrl-c", "abc") == "clear-query"
        assert p.on_key("ctrl-d", "abc") == "delete-char"

    def test_esc_in_sessions_goes_back(self, env):
        p, _ = start(env)
        p.on_sessions()
        assert "Select a project directory" in env.picker().on_key("esc", "x")

    def test_change_reloads_only_for_paths(self, env):
        p, _ = start(env)
        assert not p.on_change("abc").startswith("reload")
        assert p.on_change("/tm").startswith("reload-sync(")
        assert p.on_change("x").startswith("reload-sync(")  # the typed-path row goes away
        assert not p.on_change("xy").startswith("reload")

    def test_alt_x_twice_dismisses_all(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300), b: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_sessions()
        env.picker().on_key("alt-x", "")
        out = env.picker().on_key("alt-x", "")
        assert out.startswith("reload-sync(")
        assert json.loads(env.paths.restored.read_text()) == {}
        assert "↻" not in "".join(displayed(env.picker().rows()))

    def test_alt_x_in_projects_does_nothing(self, env):
        p, _ = start(env)
        assert p.on_key("alt-x", "") == ""

    def test_ctrl_x_dismisses_one(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300), b: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_sessions()
        assert env.picker().on_dismiss(a).startswith("reload-sync(")
        assert list(json.loads(env.paths.restored.read_text())) == [b]


class TestPick:
    def accept(self, env, row, pos=1, monkeypatch=None):
        """What Enter leaves for pick() when fzf exits."""
        (env.run / "accept").write_text(f"{pos}\n{row}\n")

    def test_enter_on_home(self, env, monkeypatch):
        start(env)
        self.accept(env, str(env.home), monkeypatch=monkeypatch)
        assert env.picker().pick("") == Pick(str(env.home))

    def test_enter_uses_the_final_query(self, env, monkeypatch):
        a = project(env, "alpha", NOW - 60)
        project(env, "beta", NOW - 120)
        start(env)
        # fzf hadn't caught up with the paste: it reported home as highlighted.
        self.accept(env, str(env.home), monkeypatch=monkeypatch)
        assert env.picker().pick("alpha") == Pick(str(a))

    def test_cursor_position(self, env, monkeypatch):
        project(env, "alpha", NOW - 60)
        b = project(env, "beta", NOW - 120)
        start(env)
        self.accept(env, str(env.home), pos=3, monkeypatch=monkeypatch)
        assert env.picker().pick("") == Pick(str(b))

    def test_typed_path(self, env, monkeypatch):
        new = env.tmp / "new"
        new.mkdir()
        start(env)
        self.accept(env, str(new), monkeypatch=monkeypatch)
        assert env.picker().pick(str(new)) == Pick(str(new))

    def test_resume_a_session_row(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        sid = sid_in(env, d)
        env.paths.state.mkdir(parents=True, exist_ok=True)
        env.paths.restored.write_text(json.dumps({sid: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_sessions()
        self.accept(env, sid, monkeypatch=monkeypatch)
        assert env.picker().pick("") == Pick(str(d), sid)
        assert json.loads(env.paths.restored.read_text()) == {}  # resuming takes it out of the group

    def test_pasted_session_id(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        sid = sid_in(env, d)
        start(env)
        self.accept(env, "", monkeypatch=monkeypatch)
        assert env.picker().pick(sid) == Pick(str(d), sid)

    def test_unknown_session_id(self, env, monkeypatch):
        start(env)
        self.accept(env, "", monkeypatch=monkeypatch)
        assert env.picker().pick("0b2f0000-0000-0000-0000-000000000000").startswith("No session with ID")

    def test_missing_directory_resumes_from_home(self, env, monkeypatch, capsys):
        monkeypatch.setattr("time.sleep", lambda s: None)
        sid = env.log("/gone/proj", conversation("x", "/gone/proj"))
        p, _ = start(env)
        p.on_sessions()
        self.accept(env, sid, monkeypatch=monkeypatch)
        assert env.picker().pick("") == Pick(str(env.home), sid)
        assert "not found, so resuming from ~" in capsys.readouterr().err

    def test_nothing_matches(self, env, monkeypatch):
        start(env)
        (env.run / "accept").unlink(missing_ok=True)  # fzf skips the command with no match
        assert env.picker().pick("zzzz") == "Nothing matches: zzzz"

    def test_typed_path_that_isnt_a_directory(self, env):
        start(env)
        assert env.picker().pick("/nope") == "Not a directory: /nope"

    def test_no_sessions_to_resume(self, env):
        p, _ = start(env)
        p.on_sessions()
        assert env.picker().pick("") == "No sessions to resume"


def test_title_skipped_on_dumb_terminals():
    from claude_launcher import title

    assert title.enabled({"TERM": "xterm-256color"})
    assert not title.enabled({"TERM": "dumb"})
    assert not title.enabled({}, windows=False)
    assert title.enabled({}, windows=True)  # Windows' consoles never set TERM
    assert not title.enabled({"TERM": "dumb"}, windows=True)


def test_nth_match_takes_rows_as_fzf_gives_them():
    # Only \n ends a row: a title can hold other line breaks (U+2028), and
    # bytes that aren't UTF-8 come back as they went.
    import shutil

    from claude_launcher.picker import _nth_match

    fzf = shutil.which("fzf")
    rows = ["a\tone two", "b\tone", "c\x80\tone\udc80"]
    rows = [r.encode("utf-8", "surrogateescape").decode("utf-8", "surrogateescape") for r in rows]
    assert _nth_match(fzf, rows, "one", 1) == "a"
    assert _nth_match(fzf, rows, "one", 3) == "c\x80"
    assert _nth_match(fzf, rows, "one", 4) == ""
