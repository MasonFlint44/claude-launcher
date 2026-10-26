import json

import pytest

from claude_launcher.journal import Journal
from claude_launcher.picker import Pick, Picker
from claude_launcher.restart import RestartState
from claude_launcher.text import strip_ansi, width
from claude_launcher.theme import Theme
from conftest import conversation

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
        Journal(lambda args: None),
        now=clock,
        boot_id=lambda: "b",
        boot_start=lambda now: now - 60,
        running=lambda pid: False,
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
    return not row.split("\t", 1)[0].endswith("/.claude")  # the fake home's Claude config


def displayed(rows):
    """What fzf shows: the fields after the first, a tab shown as a space (--tabstop=1)."""
    return [r.split("\t", 1)[1].replace("\t", " ").rstrip() for r in plain(rows) if _not_config(r)]


def start(env, cols=100):
    p = env.picker()
    opts = p.start(cols)
    return p, opts


class TestProjects:
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
        assert any(o.startswith("--bind=enter:execute-silent(") for o in opts)
        assert any(o.startswith(f"--listen={env.run}/fzf.sock") for o in opts)

    def test_notes_dont_match(self, env):
        project(env, "a", NOW - 600)
        start(env)
        env.picker().on_accept(str(env.home))
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
        assert listed(p.rows(query="~"))[1] == "~/a"
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
        assert shown[1].split(None, 1)[1] == "→ git/proj, 1m ago"
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
        assert note.startswith("→ …") and note.endswith("directory/directory, 1m ago")
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
        assert listed(p.rows(query="~/git/"))[0].split(None, 1) == ["~/git/", "→ a/b, 1m ago"]

    def test_hints(self, env):
        make_dirs(env, "git/claude-launcher")
        p, _ = start(env, cols=110)
        p.on_change("~/git/claude-launcher/")
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].endswith("enter open   tab complete   shift-tab dir sessions   esc back")
        assert lines[1].endswith("ctrl-u clear") and width(lines[1]) == 107
        p.on_change("abc")
        assert "tab sessions" in strip_ansi(p.make_header())


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

    def test_tab_outside_path_mode_switches_views(self, env):
        p, _ = start(env)
        assert "No Claude sessions yet" in p.on_tab("abc")

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
        env.picker().on_accept(str(env.home / "git/claude-launcher"))
        assert env.picker().pick("~/git/cla") == Pick(str(env.home / "git/claude-launcher"))

    def test_enter_on_the_typed_directory(self, env, monkeypatch):
        make_dirs(env, "git/x")
        start(env)
        monkeypatch.setenv("FZF_POS", "1")
        env.picker().on_accept(str(env.home / "git"))
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
        assert lines[0].endswith("enter open   tab sessions   shift-tab dir sessions   esc quit")
        assert width(lines[0]) == 107 and lines[1] == " "

    def test_narrow_window_moves_extra_hints_under(self, env):
        p, _ = start(env, cols=90)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0].endswith("enter open   tab sessions   esc quit")
        assert lines[1] == " " * (87 - len("shift-tab dir sessions")) + "shift-tab dir sessions"

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
        assert lines[1].endswith("shift-tab dir sessions   ctrl-u clear")

    def test_very_narrow_window_puts_all_hints_under(self, env):
        p, _ = start(env, cols=40)
        lines = strip_ansi(p.make_header()).split("\n")
        assert lines[0] == "Select a project directory for Claude"
        assert [line.strip() for line in lines[1:3]] == [
            "enter open   tab sessions",
            "shift-tab dir sessions   esc quit",
        ]
        assert all(width(line) <= 37 for line in lines)

    def test_sessions_hints(self, env):
        p, _ = start(env, cols=100)
        p.on_tab()
        assert strip_ansi(p.make_header()).split("\n")[0].endswith("enter resume   tab/esc back to projects")

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
        assert lines[1].strip() == "shift-tab dir sessions"
        assert "1 session was open when your computer restarted" in lines[2]
        assert lines[2].endswith("tab to see them")

    def test_logout_nudge(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300, "logout"), b: closed(NOW - 600, "logout")}))
        p, _ = start(env)
        assert "2 sessions were open when your session ended" in strip_ansi(p.make_header())
        p.on_tab()
        assert "↻ open when your session ended" in strip_ansi(p.make_header())

    def test_mixed_nudge(self, env):
        a = env.log("/tmp", conversation("a", "/tmp"))
        b = env.log("/tmp", conversation("b", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({a: closed(NOW - 300, "logout"), b: closed(NOW - 600)}))
        p, _ = start(env)
        assert "2 sessions were closed by a restart or logout" in strip_ansi(p.make_header())


class TestSessions:
    def test_tab_toggles(self, env):
        env.log("/tmp", conversation("hello", "/tmp"))
        p, _ = start(env)
        out = p.on_tab()
        assert out.startswith("clear-query+change-prompt(session> )+reload-sync(")
        assert "Recent Claude sessions" in out
        assert displayed(p.rows())[1].split()[-1] == "hello"
        assert "Select a project directory" in env.picker().on_tab()

    def test_no_sessions(self, env):
        p, _ = start(env)
        assert "No Claude sessions yet" in p.on_tab()

    def test_missing_directory_marked(self, env):
        env.log("/gone/proj", conversation("hello", "/gone/proj"))
        p, _ = start(env)
        p.on_tab()
        assert "proj (missing)" in displayed(p.rows())[1]

    def test_restart_group_first_and_marked(self, env):
        older = env.log("/tmp", conversation("open at restart", "/tmp"), mtime=NOW - 9000)
        env.log("/tmp", conversation("recent", "/tmp"), mtime=NOW - 60)
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({older: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_tab()
        shown = displayed(p.rows())
        assert shown[1].startswith("↻ ") and shown[1].endswith("open at restart")
        assert not shown[2].startswith("↻")
        assert shown[2].endswith("recent")
        assert "ctrl-x dismiss ↻" in strip_ansi(p.make_header())

    def test_shift_tab_lists_one_directory(self, env):
        a = project(env, "a", NOW - 60)
        project(env, "b", NOW - 120)
        p, _ = start(env)
        assert "Claude sessions in" in p.on_shift_tab(str(a))
        assert displayed(p.rows())[1].endswith("in a")
        assert len(p.rows()) == 2

    def test_long_directory_in_the_title_cut_from_the_left(self, env):
        d = project(env, "/".join(["directory"] * 12), NOW - 60)
        p, _ = start(env, cols=60)
        p.on_shift_tab(str(d))
        title = strip_ansi(p.make_header()).split("\n")[0]
        assert title.startswith("Claude sessions in …") and title.endswith("directory/directory")
        assert width(title) == 57

    def test_shift_tab_ignores_a_session_row(self, env):
        p, _ = start(env)
        assert p.on_shift_tab("0b2f0000-0000-0000-0000-000000000000") == ""


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
        p.on_tab()
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
        p.on_tab()
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
        p.on_tab()
        assert env.picker().on_dismiss(a).startswith("reload-sync(")
        assert list(json.loads(env.paths.restored.read_text())) == [b]


class TestPick:
    def accept(self, env, row, pos=1, monkeypatch=None):
        monkeypatch.setenv("FZF_POS", str(pos))
        env.picker().on_accept(row)

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
        sid = next(p.stem for p in env.paths.log_dirs_for(str(d))[0].glob("*.jsonl"))
        env.paths.state.mkdir(parents=True, exist_ok=True)
        env.paths.restored.write_text(json.dumps({sid: closed(NOW - 300)}))
        p, _ = start(env)
        p.on_tab()
        self.accept(env, sid, monkeypatch=monkeypatch)
        assert env.picker().pick("") == Pick(str(d), sid)
        assert json.loads(env.paths.restored.read_text()) == {}  # resuming takes it out of the group

    def test_pasted_session_id(self, env, monkeypatch):
        d = project(env, "proj", NOW - 60)
        sid = next(p.stem for p in env.paths.log_dirs_for(str(d))[0].glob("*.jsonl"))
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
        p.on_tab()
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
        p.on_tab()
        assert env.picker().pick("") == "No sessions to resume"


def test_title_skipped_on_dumb_terminals():
    from claude_launcher import title

    assert title.enabled({"TERM": "xterm-256color"})
    assert not title.enabled({"TERM": "dumb"})
    assert not title.enabled({})
