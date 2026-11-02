"""bin/claude-session-hook.py, Windows' session hook: the same records as the
bash hook, read back by restart.Record. Its logic runs on any OS with the boot
ID given; tests/windows runs it for real."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from claude_launcher.paths import REPO_DIR
from claude_launcher.restart import _RECORD_NAME, Record

spec = importlib.util.spec_from_file_location("win_hook", REPO_DIR / "bin" / "claude-session-hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
BOOT = format(9, "032x")


@pytest.fixture
def config(tmp_path):
    return tmp_path / "claude"


def run(config, event, reason=None, entry="cli", pid="4242", session=SID, at=100.0, began=None):
    """`began`: when the Claude process began, or None if it can't be told."""
    payload = {"session_id": session, "hook_event_name": "SessionEnd" if event == "end" else "SessionStart"}
    if reason:
        payload["reason"] = reason
    env = {"CLAUDE_CODE_ENTRYPOINT": entry, "CLAUDE_CONFIG_DIR": str(config)}
    if pid is not None:
        env["CLAUDE_PID"] = pid
    hook.run(event, json.dumps(payload), env, boot_id=lambda: BOOT, now=lambda: at, created=lambda pid: began)


def records(config):
    folder = config / "state" / "claude-launcher" / "open"
    return {p.name: p for p in folder.iterdir()} if folder.exists() else {}


def test_start_writes_what_the_launcher_reads(config):
    run(config, "start")
    [(name, path)] = records(config).items()
    assert name == f"{BOOT}_4242"
    record = Record.load(path)
    assert (record.session_id, record.pid, record.boot_id, record.started, record.ended) == (
        SID,
        4242,
        BOOT,
        100.0,
        None,
    )
    assert b"\r" not in path.read_bytes()


def test_closed_tab_keeps_the_record_with_its_end(config):
    run(config, "start")
    run(config, "end", "other", at=150.0)
    record = Record.load(records(config)[f"{BOOT}_4242"])
    assert (record.started, record.ended) == (100.0, 150.0)


@pytest.mark.parametrize("reason", ["prompt_input_exit", "clear", "resume", "logout"])
def test_an_end_you_chose_removes_it(config, reason):
    run(config, "start")
    run(config, "end", reason)
    assert records(config) == {}


def test_not_for_claude_p_or_the_sdk(config):
    run(config, "start", entry="sdk-cli")
    assert records(config) == {}


@pytest.mark.parametrize("pid", [None, "", "abc"])
def test_without_claude_pid_no_record(config, pid):
    # The hook's parent is cmd or py, gone by the time the picker looks: a
    # record under its pid would be taken for a closed Claude.
    run(config, "start", pid=pid)
    run(config, "end", "other", pid=pid)
    assert records(config) == {}


def test_a_busy_record_is_retried(config, monkeypatch):
    # Windows refuses to replace or delete a file another process has open.
    real, refused = os.replace, []

    def busy_once(src, dst):
        if not refused:
            refused.append(dst)
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(hook.os, "replace", busy_once)
    monkeypatch.setattr(hook, "_PAUSE", 0)
    run(config, "start")
    assert refused and f"{BOOT}_4242" in records(config)


def test_a_busy_removal_is_retried(config, monkeypatch):
    run(config, "start")
    real, refused = os.remove, []

    def busy_twice(path):
        if len(refused) < 2:
            refused.append(path)
            raise PermissionError(13, "in use")
        real(path)

    monkeypatch.setattr(hook.os, "remove", busy_twice)
    monkeypatch.setattr(hook, "_PAUSE", 0)
    run(config, "end", "prompt_input_exit")
    assert len(refused) == 2 and records(config) == {}


def test_a_file_that_stays_busy_is_given_up_on(config, monkeypatch):
    def busy(*args):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(hook.os, "replace", busy)
    monkeypatch.setattr(hook, "_PAUSE", 0)
    with pytest.raises(PermissionError):
        run(config, "start")
    assert hook.main() == 0  # and main still never fails


# The hook as a process, with its boot ID given, reading stdin as Claude writes it.
PROCESS = """
import functools, importlib.util, sys
spec = importlib.util.spec_from_file_location("h", sys.argv[1])
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
boot = sys.argv[2]
hook.run = functools.partial(hook.run, boot_id=lambda: boot)
sys.argv = sys.argv[2:]
sys.exit(hook.main())
"""


def test_stdin_is_read_as_utf8(config):
    # As Windows' Python would read a pipe in the ANSI code page: Á and с are
    # bytes cp1252 has no character for.
    payload = json.dumps(
        {"session_id": SID, "hook_event_name": "SessionStart", "cwd": "C:\\Á\\с ↻"}, ensure_ascii=False
    )
    env = {
        **os.environ,
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_PID": "4242",
        "PYTHONIOENCODING": "cp1252",
    }
    script = str(REPO_DIR / "bin" / "claude-session-hook.py")
    done = subprocess.run(
        [sys.executable, "-c", PROCESS, script, BOOT, "start"],
        input=payload.encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert f"{BOOT}_4242" in records(config)


def test_an_end_without_a_start(config):
    run(config, "end", "other", at=150.0)
    record = Record.load(records(config)[f"{BOOT}_4242"])
    assert (record.started, record.ended) == (150.0, 150.0)


def test_an_end_keeps_another_sessions_record_aside(config):
    # No start was seen for this session: the record under the pid is
    # another's, which left no end. It's moved aside, as a start does.
    other = "11111111-2222-4333-8444-555555555555"
    run(config, "start", session=other)
    run(config, "end", "other", at=150.0)
    found = records(config)
    assert set(found) == {f"{BOOT}_4242", f"{BOOT}_4242_{other}"}
    record, kept = Record.load(found[f"{BOOT}_4242"]), Record.load(found[f"{BOOT}_4242_{other}"])
    assert (record.session_id, record.started, record.ended) == (SID, 150.0, 150.0)
    assert (kept.session_id, kept.started, kept.ended) == (other, 100.0, None)


def test_a_new_claude_with_the_pid_keeps_the_old_record(config):
    # A Claude a logoff or Fast Startup killed (no end), then a new one that
    # got its pid (Windows reuses them quickly) before the launcher sorted it.
    other = "11111111-2222-4333-8444-555555555555"
    run(config, "start", session=other)
    run(config, "start", at=150.0)
    found = records(config)
    assert set(found) == {f"{BOOT}_4242", f"{BOOT}_4242_{other}"}
    kept, new = Record.load(found[f"{BOOT}_4242_{other}"]), Record.load(found[f"{BOOT}_4242"])
    assert (kept.session_id, kept.started, kept.ended) == (other, 100.0, None)
    assert (new.session_id, new.started) == (SID, 150.0)
    assert _RECORD_NAME.fullmatch(f"{BOOT}_4242_{other}")


OTHER, THIRD = "11111111-2222-4333-8444-555555555555", "66666666-7777-4888-8999-aaaaaaaaaaaa"


def test_each_claude_the_pid_comes_back_to_keeps_its_record(config):
    run(config, "start", session=OTHER, at=100.0)
    run(config, "start", at=150.0)
    run(config, "start", session=THIRD, at=200.0)
    found = records(config)
    assert set(found) == {f"{BOOT}_4242", f"{BOOT}_4242_{OTHER}", f"{BOOT}_4242_{SID}"}
    assert Record.load(found[f"{BOOT}_4242_{OTHER}"]).started == 100.0
    assert Record.load(found[f"{BOOT}_4242_{SID}"]).started == 150.0
    assert Record.load(found[f"{BOOT}_4242"]).session_id == THIRD


def test_a_record_moved_aside_again_replaces_the_older_one(config):
    # OTHER, then SID, then OTHER resumed, then THIRD: the newer of OTHER's records wins.
    run(config, "start", session=OTHER, at=100.0)
    run(config, "start", at=150.0)
    run(config, "start", session=OTHER, at=200.0)
    run(config, "start", session=THIRD, at=250.0)
    found = records(config)
    assert set(found) == {f"{BOOT}_4242", f"{BOOT}_4242_{OTHER}", f"{BOOT}_4242_{SID}"}
    assert Record.load(found[f"{BOOT}_4242_{OTHER}"]).started == 200.0


def test_a_move_aside_that_stays_busy_still_writes_the_new_record(config, monkeypatch):
    # Windows refuses every try: the old record is lost, but the new Claude has one.
    run(config, "start", session=OTHER)
    real, refused = os.replace, []

    def busy_aside(src, dst):
        if str(dst).endswith(OTHER):
            refused.append(dst)
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(hook.os, "replace", busy_aside)
    monkeypatch.setattr(hook, "_PAUSE", 0)
    run(config, "start", at=150.0)
    assert len(refused) == hook._TRIES
    assert set(records(config)) == {f"{BOOT}_4242"}
    assert Record.load(records(config)[f"{BOOT}_4242"]).session_id == SID


@pytest.mark.parametrize("reason", ["prompt_input_exit", "clear", "resume", "logout"])
def test_a_chosen_end_leaves_another_sessions_record(config, reason):
    # No start was seen for this session: the record under its pid is another's.
    run(config, "start", session=OTHER)
    run(config, "end", reason)
    record = Record.load(records(config)[f"{BOOT}_4242"])
    assert (record.session_id, record.ended) == (OTHER, None)


class TestThisClaudesEarlierSession:
    """Another session's record that this same Claude wrote (it had begun by
    that record's start): its session before, ended by choice (/clear,
    /resume), with the end hook killed before it deleted the record. It
    goes, rather than being kept aside for the launcher to take for a
    closed tab."""

    def test_a_start_deletes_it(self, config):
        run(config, "start", session=OTHER, at=100.0, began=90.0)
        run(config, "start", at=150.0, began=90.0)
        assert set(records(config)) == {f"{BOOT}_4242"}
        assert Record.load(records(config)[f"{BOOT}_4242"]).session_id == SID

    def test_an_end_without_a_start_deletes_it(self, config):
        run(config, "start", session=OTHER, at=100.0, began=90.0)
        run(config, "end", "other", at=150.0, began=90.0)
        record = Record.load(records(config)[f"{BOOT}_4242"])
        assert set(records(config)) == {f"{BOOT}_4242"} and (record.session_id, record.ended) == (SID, 150.0)

    @pytest.mark.parametrize("reason", hook.CHOSEN_ENDS)
    def test_a_chosen_end_without_a_start_deletes_it(self, config, reason):
        run(config, "start", session=OTHER, at=100.0, began=90.0)
        run(config, "end", reason, at=150.0, began=90.0)
        assert records(config) == {}

    def test_a_process_that_began_after_it_isnt_its_claude(self, config):
        # A new Claude got the pid: the old record is kept aside, as before.
        run(config, "start", session=OTHER, at=100.0, began=90.0)
        run(config, "start", at=150.0, began=100.0 + hook._REUSED_AFTER + 1)
        assert set(records(config)) == {f"{BOOT}_4242", f"{BOOT}_4242_{OTHER}"}

    def test_the_same_margin_as_the_launcher(self):
        from claude_launcher.platforms.windows import host

        assert hook._REUSED_AFTER == host._REUSED_AFTER

    def test_a_busy_removal_still_writes_the_new_record(self, config, monkeypatch):
        run(config, "start", session=OTHER, at=100.0, began=90.0)

        def busy(path):
            raise PermissionError(13, "in use")

        monkeypatch.setattr(hook.os, "remove", busy)
        monkeypatch.setattr(hook, "_PAUSE", 0)
        run(config, "start", at=150.0, began=90.0)
        assert Record.load(records(config)[f"{BOOT}_4242"]).session_id == SID


def test_the_same_session_starting_again_keeps_one_record(config):
    run(config, "start")
    run(config, "start", at=150.0)
    assert set(records(config)) == {f"{BOOT}_4242"}


def test_bad_input_writes_nothing(config):
    hook.run("start", "not json", {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG_DIR": str(config)})
    run(config, "start", session="not-a-session-id")
    assert records(config) == {}


def test_main_never_fails(monkeypatch):
    monkeypatch.setattr(hook, "run", lambda *a, **k: 1 / 0)
    assert hook.main() == 0
