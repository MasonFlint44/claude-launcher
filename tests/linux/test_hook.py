"""claude-session-hook, run under a fake `claude` process in a pseudo-terminal."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from claude_launcher.paths import REPO_DIR
from term import Term

HOOK = REPO_DIR / "bin" / "claude-session-hook"
SID = "0b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def run_hook(tmp_path):
    """Run the hook from a process whose name is `claude`, as Claude Code would."""
    claude = tmp_path / "claude"
    claude.symlink_to("/bin/bash")  # the process name comes from the path it was run as
    config = tmp_path / "config"
    records = config / "state" / "claude-launcher" / "open"

    def run(*events: tuple[str, dict], entrypoint: str = "cli", tty: bool = True, as_=None) -> dict[str, dict]:
        """`as_`: the command line that runs the script, instead of the native `claude` binary."""
        script = "".join(
            f"printf '%s' {shlex.quote(json.dumps(payload))} | {shlex.quote(str(HOOK))} {arg}\n"
            for arg, payload in events
        )
        script += "echo $$ > pid; echo done\n"
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(config),
            "CLAUDE_CODE_ENTRYPOINT": entrypoint,
            **{k: v for k, v in os.environ.items() if k == "LC_ALL"},
        }
        argv = [str(claude), "-c", script]
        if as_ is not None:
            script_file = tmp_path / as_[1]
            script_file.parent.mkdir(parents=True, exist_ok=True)
            script_file.write_text(script)
            node = tmp_path / as_[0]
            if not node.exists():
                node.symlink_to("/bin/bash")
            argv = [str(node), str(script_file)]
        if tty:
            t = Term(argv, env, cwd=str(tmp_path))
            t.wait_for("done")
            t.wait_exit()
            t.close()
        else:
            subprocess.run(
                argv,
                env=env,
                cwd=tmp_path,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
            )
        if not records.exists():
            return {}
        pid = f"{boot_id()}_" + (tmp_path / "pid").read_text().strip()
        found = {p.name: json.loads(p.read_text()) for p in records.iterdir()}
        if as_ is not None and not as_[1].endswith(("claude", "cli.js")):
            # Walking up past a non-Claude process can reach a real Claude running these tests.
            return {k: v for k, v in found.items() if k == pid}
        assert set(found) <= {pid}
        return found

    return run


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def test_start_writes_a_record(run_hook):
    [record] = run_hook(("start", {"session_id": SID, "source": "startup"})).values()
    assert record["session_id"] == SID
    assert record["boot_id"] == boot_id()
    assert record["login_session"] == Path("/proc/self/sessionid").read_text().strip().replace("4294967295", "")
    assert "ended_at" not in record


def test_closed_tab_keeps_the_record_with_its_end(run_hook):
    [record] = run_hook(("start", {"session_id": SID}), ("end", {"session_id": SID, "reason": "other"})).values()
    assert record["end_reason"] == "other"
    assert record["ended_at"] >= record["started_at"]


@pytest.mark.parametrize("reason", ["prompt_input_exit", "clear", "resume", "logout"])
def test_chosen_exit_deletes_the_record(run_hook, reason):
    assert run_hook(("start", {"session_id": SID}), ("end", {"session_id": SID, "reason": reason})) == {}


def test_end_of_another_session_doesnt_reuse_its_record(run_hook):
    # The hook was added while this Claude ran another session, which left no end.
    other = "9b2f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
    [record] = run_hook(("start", {"session_id": other}), ("end", {"session_id": SID, "reason": "other"})).values()
    assert record["session_id"] == SID and "ended_at" in record


def test_times_are_json_numbers_in_any_locale(run_hook, monkeypatch):
    comma = next((loc for loc in ("de_DE.utf8", "en_DK.utf8", "fr_FR.utf8") if loc in _locales()), None)
    if comma is None:
        pytest.skip("needs a locale with a comma for the decimal point")
    monkeypatch.setenv("LC_ALL", comma)
    [record] = run_hook(("start", {"session_id": SID}), ("end", {"session_id": SID, "reason": "other"})).values()
    assert isinstance(record["started_at"], float) and isinstance(record["ended_at"], float)


def _locales() -> set[str]:
    out = subprocess.run(["locale", "-a"], capture_output=True, text=True)
    return set(out.stdout.split())


def test_end_without_a_start(run_hook):
    [record] = run_hook(("end", {"session_id": SID, "reason": "other"})).values()
    assert record["session_id"] == SID and "ended_at" in record


def test_not_interactive(run_hook):
    assert run_hook(("start", {"session_id": SID}), entrypoint="sdk-py") == {}


def test_not_in_a_terminal(run_hook):
    assert run_hook(("start", {"session_id": SID}), tty=False) == {}


def test_bad_session_id(run_hook):
    assert run_hook(("start", {"session_id": 'x"; rm -rf /'})) == {}


@pytest.mark.parametrize(
    "as_",
    [
        ("node", "lib/node_modules/@anthropic-ai/claude-code/cli.js"),  # npm's package, run by node
        ("node", "npm-global/bin/claude"),  # npm's `claude` link, run by node
    ],
)
def test_npm_install(run_hook, as_):
    [record] = run_hook(("start", {"session_id": SID}), as_=as_).values()
    assert record["session_id"] == SID


def test_some_other_node_program(run_hook):
    assert run_hook(("start", {"session_id": SID}), as_=("node", "some-app/index.js")) == {}


def test_records_are_what_the_launcher_reads(run_hook, tmp_path):
    """The hook's half of the record format: restart.Record loads what it
    writes, with this boot's ID as the platform's Host reports it."""
    from claude_launcher.platforms import current
    from claude_launcher.restart import Record, normalize_boot_id

    records = run_hook(("start", {"session_id": SID}), ("end", {"session_id": SID, "reason": "other"}))
    (name,) = records
    path = tmp_path / "record.json"
    path.write_text(json.dumps(records[name]))
    record = Record.load(path)
    assert record is not None
    assert record.session_id == SID
    assert record.boot_id == normalize_boot_id(current().host.boot_id())
    assert record.ended is not None and record.started <= record.ended
