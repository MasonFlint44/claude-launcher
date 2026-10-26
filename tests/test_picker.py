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
    d.mkdir(exist_ok=True)
    env.log(d, conversation(f"in {name}", d), mtime=mtime)
    return d


def plain(rows):
    return [strip_ansi(r) for r in rows]


def displayed(rows):
    """The second field: what fzf shows."""
    return [r.split("\t", 1)[1].rstrip() for r in plain(rows)]


def start(env, cols=100):
    p = env.picker()
    opts = p.start(cols)
    return p, opts


class TestProjects:
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

    def test_typed_path_not_listed(self, env):
        new = env.tmp / "new"
        new.mkdir()
        p, _ = start(env)
        rows = p.rows(query=str(new))
        assert rows[1].split("\t")[0] == str(new)
        assert displayed(rows)[1].split() == [str(new), "typed", "path"]

    def test_typed_path_listed_moves_up(self, env):
        a = project(env, "a", NOW - 600)
        project(env, "b", NOW - 60)
        p, _ = start(env)
        rows = p.rows(query=str(a))
        keys = [r.split("\t")[0] for r in rows[1:]]
        assert keys[0] == str(a) and keys.count(str(a)) == 1
        assert displayed(rows)[1].endswith("10m ago")

    def test_typed_tilde_path(self, env):
        (env.home / "code").mkdir()
        p, _ = start(env)
        assert p.rows(query="~/code")[1].split("\t")[0] == str(env.home / "code")

    def test_typed_path_that_doesnt_exist(self, env):
        p, _ = start(env)
        assert [r.split("\t")[0] for r in p.rows(query="/nope/nope")[1:]] == [str(env.home)]

    def test_rows_file_and_options(self, env):
        p, opts = start(env)
        assert (env.run / "rows").read_text().splitlines()[1].startswith(str(env.home) + "\t")
        assert "--header-lines=1" in opts and "--with-nth=2" in opts
        assert any(o.startswith("--bind=enter:execute-silent(") for o in opts)
        assert any(o.startswith(f"--listen={env.run}/fzf.sock") for o in opts)


class TestHeader:
    def test_hints_right_aligned(self, env):
        p, _ = start(env, cols=100)
        top = strip_ansi(p.make_header().split("\n")[0])
        assert top.startswith("Select a project directory for Claude")
        assert top.endswith("esc quit") and width(top) == 97

    def test_narrow_window_gets_short_hints(self, env):
        p, _ = start(env, cols=80)
        top = strip_ansi(p.make_header().split("\n")[0])
        assert "shift-tab" not in top and top.endswith("esc quit")

    def test_very_narrow_window_gets_no_hints(self, env):
        p, _ = start(env, cols=40)
        assert strip_ansi(p.make_header().split("\n")[0]) == "Select a project directory for Claude"

    def test_capped_width(self, env):
        p, opts = start(env, cols=200)
        assert "--margin=0,80,0,0" in opts

    def test_restart_nudge(self, env):
        sid = env.log("/tmp", conversation("x", "/tmp"))
        env.paths.state.mkdir(parents=True)
        env.paths.restored.write_text(json.dumps({sid: closed(NOW - 300)}))
        p, _ = start(env)
        lines = strip_ansi(p.make_header()).split("\n")
        assert "1 session was open when your computer restarted" in lines[1]
        assert lines[1].endswith("tab to see them")

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
