import json

from claude_launcher import logs
from conftest import assistant, conversation, user


def titles(claude_home):
    sessions, _ = logs.scan_sessions(claude_home.paths.all_log_dirs())
    return [s.title for s in sessions]


def only_session(claude_home):
    sessions, _ = logs.scan_sessions(claude_home.paths.all_log_dirs())
    assert len(sessions) == 1
    return sessions[0]


class TestTitle:
    def test_first_prompt(self, claude_home):
        claude_home.log("/tmp", conversation("fix the flaky test", "/tmp"))
        assert titles(claude_home) == ["fix the flaky test"]

    def test_last_prompt_beats_first(self, claude_home):
        claude_home.log("/tmp", conversation("first", "/tmp") + [{"type": "last-prompt", "lastPrompt": "latest"}])
        assert titles(claude_home) == ["latest"]

    def test_ai_title_beats_prompts(self, claude_home):
        claude_home.log(
            "/tmp",
            conversation("first", "/tmp")
            + [{"type": "last-prompt", "lastPrompt": "latest"}, {"type": "ai-title", "aiTitle": "Fixing tests"}],
        )
        assert titles(claude_home) == ["Fixing tests"]

    def test_rename_beats_everything(self, claude_home):
        claude_home.log(
            "/tmp",
            conversation("first", "/tmp")
            + [{"type": "ai-title", "aiTitle": "Fixing tests"}, {"type": "custom-title", "customTitle": "my name"}],
        )
        assert titles(claude_home) == ["my name"]

    def test_rename_from_title_file(self, claude_home):
        sid = claude_home.log("/tmp", conversation("first", "/tmp") + [{"type": "ai-title", "aiTitle": "AI"}])
        title_dir = claude_home.project("/tmp") / sid
        title_dir.mkdir()
        (title_dir / "custom-title.json").write_text(json.dumps({"customTitle": "📌 pinned"}))
        assert titles(claude_home) == ["📌 pinned"]

    def test_latest_rename_wins(self, claude_home):
        claude_home.log(
            "/tmp",
            conversation("first", "/tmp")
            + [{"type": "custom-title", "customTitle": "old"}, {"type": "custom-title", "customTitle": "new"}],
        )
        assert titles(claude_home) == ["new"]

    def test_command_only_session_shows_the_command(self, claude_home):
        cmd = "<command-name>/clear</command-name><command-message>clear</command-message><command-args></command-args>"
        claude_home.log("/tmp", [user(cmd, "/tmp"), assistant(cwd="/tmp")])
        assert titles(claude_home) == ["/clear"]

    def test_typed_skill_command_with_arguments(self, claude_home):
        cmd = "<command-name>/review</command-name><command-args>PR 12</command-args>"
        claude_home.log("/tmp", [user(cmd, "/tmp", origin={"kind": "human"}), assistant(cwd="/tmp")])
        assert titles(claude_home) == ["/review PR 12"]

    def test_bash_input(self, claude_home):
        claude_home.log("/tmp", [user("<bash-input>code .</bash-input>", "/tmp"), assistant(cwd="/tmp")])
        assert titles(claude_home) == ["! code ."]

    def test_skips_interrupt_notes_and_meta(self, claude_home):
        claude_home.log(
            "/tmp",
            [
                user("[Request interrupted by user]", "/tmp"),
                user("caveat", "/tmp", isMeta=True),
                user("the real prompt", "/tmp"),
                assistant(cwd="/tmp"),
            ],
        )
        assert titles(claude_home) == ["the real prompt"]

    def test_long_prompt_is_cut(self, claude_home):
        claude_home.log("/tmp", conversation("x" * 300, "/tmp"))
        assert titles(claude_home) == ["x" * 200 + "…"]

    def test_whitespace_is_collapsed(self, claude_home):
        claude_home.log("/tmp", conversation("two\n\nlines  here", "/tmp"))
        assert titles(claude_home) == ["two lines here"]

    def test_title_near_the_end_of_a_big_log(self, claude_home):
        filler = [assistant("y" * 1000, "/tmp") for _ in range(200)]  # well over 64 KB
        claude_home.log(
            "/tmp", conversation("first", "/tmp") + filler + [{"type": "ai-title", "aiTitle": "late title"}]
        )
        assert titles(claude_home) == ["late title"]


class TestLeftOut:
    def test_subagent(self, claude_home):
        claude_home.log("/tmp", [user("task", "/tmp", isSidechain=True), assistant(cwd="/tmp")])
        assert titles(claude_home) == []

    def test_team(self, claude_home):
        claude_home.log("/tmp", [user("task", "/tmp", teamName="crew"), assistant(cwd="/tmp")])
        assert titles(claude_home) == []

    def test_sdk(self, claude_home):
        entry = user("task", "/tmp")
        entry["entrypoint"] = "sdk-py"
        claude_home.log("/tmp", [entry, assistant(cwd="/tmp")])
        assert titles(claude_home) == []

    def test_loop(self, claude_home):
        claude_home.log(
            "/tmp",
            [
                user("<command-name>/loop</command-name><command-args>5m x</command-args>", "/tmp"),
                assistant(cwd="/tmp"),
            ],
        )
        assert titles(claude_home) == []

    def test_daemon(self, claude_home):
        claude_home.log("/tmp", [user("task", "/tmp", sessionKind="daemon"), assistant(cwd="/tmp")])
        assert titles(claude_home) == []

    def test_no_messages(self, claude_home):
        claude_home.log(
            "/tmp", [{"type": "mode", "mode": "normal"}, {"type": "permission-mode", "permissionMode": "auto"}]
        )
        assert titles(claude_home) == []

    def test_not_a_session_log(self, claude_home):
        (claude_home.project("/tmp") / "notes.jsonl").write_text(json.dumps(user("x")) + "\n")
        assert titles(claude_home) == []

    def test_continued_session_is_replaced_by_the_newer_one(self, claude_home):
        newer = claude_home.log("/tmp", conversation("newer", "/tmp"))
        older = claude_home.log(
            "/tmp", conversation("older", "/tmp") + [{"type": "continued-in", "continuedInSessionId": newer}]
        )
        sessions, continued_from = logs.scan_sessions(claude_home.paths.all_log_dirs())
        assert [s.title for s in sessions] == ["newer"]
        assert continued_from == {newer: older}

    def test_session_that_went_on_after_the_marker_stays(self, claude_home):
        newer = claude_home.log("/tmp", conversation("newer", "/tmp"))
        claude_home.log(
            "/tmp",
            conversation("older", "/tmp")
            + [{"type": "continued-in", "continuedInSessionId": newer}]
            + [user("kept going", "/tmp"), assistant(cwd="/tmp")],
        )
        assert sorted(titles(claude_home)) == ["newer", "older"]

    def test_marker_to_an_empty_session_is_ignored(self, claude_home):
        newer = claude_home.log("/tmp", [{"type": "mode", "mode": "normal"}])
        claude_home.log(
            "/tmp", conversation("older", "/tmp") + [{"type": "continued-in", "continuedInSessionId": newer}]
        )
        assert titles(claude_home) == ["older"]


class TestSessionFields:
    def test_cwd_and_relocation(self, claude_home):
        claude_home.log("/tmp", conversation("x", "/srv/a") + [{"type": "relocated", "relocatedCwd": "/srv/b"}])
        assert only_session(claude_home).cwd == "/srv/b"

    def test_active_is_the_last_message(self, claude_home):
        claude_home.log(
            "/tmp", [user("x", "/tmp", ts="2026-10-20T12:00:00Z"), assistant(cwd="/tmp", ts="2026-10-20T13:00:00Z")]
        )
        assert only_session(claude_home).active == 1792501200.0  # 13:00 UTC

    def test_active_is_never_after_the_file_changed(self, claude_home):
        claude_home.log("/tmp", conversation("x", "/tmp"), mtime=1000.0)
        assert only_session(claude_home).active == 1000.0

    def test_most_recent_first(self, claude_home):
        claude_home.log("/tmp", conversation("old", "/tmp"), mtime=1000.0)
        claude_home.log("/tmp", conversation("new", "/tmp"), mtime=2000.0)
        assert titles(claude_home) == ["new", "old"]

    def test_session_dir(self, claude_home):
        sid = claude_home.log("/tmp", conversation("x", "/srv/a"))
        assert logs.session_dir(claude_home.paths, sid) == "/srv/a"
        assert logs.session_dir(claude_home.paths, "00000000-0000-0000-0000-000000000000") is None


class TestRecentProjects:
    def test_newest_log_per_project_most_recent_first(self, claude_home, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(), b.mkdir()
        claude_home.log(a, conversation("x", a), mtime=1000.0)
        claude_home.log(a, conversation("x", a), mtime=3000.0)
        claude_home.log(b, conversation("x", b), mtime=2000.0)
        assert logs.recent_projects(claude_home.paths) == [(3000.0, str(a)), (2000.0, str(b))]

    def test_skips_directories_that_are_gone(self, claude_home, tmp_path):
        claude_home.log(tmp_path / "gone", conversation("x", tmp_path / "gone"))
        assert logs.recent_projects(claude_home.paths) == []

    def test_cwd_found_past_the_first_lines(self, claude_home, tmp_path):
        meta = [{"type": "mode", "mode": "normal"}] * 50
        claude_home.log(tmp_path, meta + conversation("x", tmp_path), mtime=5.0)
        assert logs.recent_projects(claude_home.paths) == [(5.0, str(tmp_path))]

    def test_path_with_quote(self, claude_home, tmp_path):
        odd = tmp_path / 'say "hi"'
        odd.mkdir()
        claude_home.log(odd, conversation("x", odd), mtime=5.0)
        assert logs.recent_projects(claude_home.paths) == [(5.0, str(odd))]


def test_long_path_log_dirs(claude_home):
    long = "/" + "a" * 250
    cut = claude_home.paths.projects / ("-" + "a" * 199 + "-deadbeef")
    cut.mkdir()
    assert claude_home.paths.log_dirs_for(long) == [cut]
