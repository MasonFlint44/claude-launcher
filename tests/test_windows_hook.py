"""bin/claude-session-hook.py, Windows' session hook: the same records as the
bash hook, read back by restart.Record. Its logic runs on any OS with the boot
ID given; tests/windows runs it for real."""

import importlib.util
import json

import pytest

from claude_launcher.paths import REPO_DIR
from claude_launcher.restart import Record

spec = importlib.util.spec_from_file_location("win_hook", REPO_DIR / "bin" / "claude-session-hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
BOOT = format(9, "032x")


@pytest.fixture
def config(tmp_path):
    return tmp_path / "claude"


def run(config, event, reason=None, entry="cli", pid="4242", session=SID, at=100.0):
    payload = {"session_id": session, "hook_event_name": "SessionEnd" if event == "end" else "SessionStart"}
    if reason:
        payload["reason"] = reason
    env = {"CLAUDE_CODE_ENTRYPOINT": entry, "CLAUDE_CONFIG_DIR": str(config)}
    if pid is not None:
        env["CLAUDE_PID"] = pid
    hook.run(event, json.dumps(payload), env, boot_id=lambda: BOOT, now=lambda: at, parent=lambda: 777)


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


def test_without_claude_pid_its_the_parent(config):
    # No shell runs the hook, so its parent is Claude.
    run(config, "start", pid=None)
    assert f"{BOOT}_777" in records(config)


def test_an_end_without_a_start(config):
    run(config, "end", "other", at=150.0)
    record = Record.load(records(config)[f"{BOOT}_4242"])
    assert (record.started, record.ended) == (150.0, 150.0)


def test_another_sessions_record_is_replaced(config):
    run(config, "start", session="11111111-2222-4333-8444-555555555555")
    run(config, "end", "other", at=150.0)
    record = Record.load(records(config)[f"{BOOT}_4242"])
    assert (record.session_id, record.started) == (SID, 150.0)


def test_bad_input_writes_nothing(config):
    hook.run("start", "not json", {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG_DIR": str(config)})
    run(config, "start", session="not-a-session-id")
    assert records(config) == {}


def test_main_never_fails(monkeypatch):
    monkeypatch.setattr(hook, "run", lambda *a, **k: 1 / 0)
    assert hook.main() == 0
