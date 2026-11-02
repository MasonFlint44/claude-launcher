"""The core's commands: what the installers, CI and the detached half of a
first key press run."""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest
from test_platforms import platform

from claude_launcher import cli, fzf, install, picker, platforms


@pytest.fixture
def mine(monkeypatch):
    p = platform("mine", fzf_os="linux", data_dir=lambda env: Path("/data"))
    monkeypatch.setattr(platforms, "_current", p)
    return p


def test_fzf_dir(mine, capsys):
    assert cli.main(["install", "fzf-dir"]) == 0
    assert capsys.readouterr().out.strip() == str(Path("/data") / "bin")


@pytest.mark.parametrize(
    ("found", "said"), [(("/x/fzf", (0, 70, 0)), "ok 0.70.0 /x/fzf"), ((None, (0, 44, 0)), "old 0.44.0")]
)
def test_fzf_status(mine, monkeypatch, capsys, found, said):
    monkeypatch.setattr(fzf, "find", lambda own: found)
    assert cli.main(["install", "fzf-status"]) == 0
    assert capsys.readouterr().out.strip() == said


def test_fetch_fzf(mine, monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(fzf, "fetch", lambda own, system: asked.append((own, system)) or own / "fzf")
    assert cli.main(["install", "fetch-fzf"]) == 0
    assert asked == [(Path("/data") / "bin", "linux")]
    assert capsys.readouterr().out.strip() == str(Path("/data") / "bin" / "fzf")


def test_fetch_fzf_fails(mine, monkeypatch, capsys):
    def fails(own, system):
        raise RuntimeError("no fzf build")

    monkeypatch.setattr(fzf, "fetch", fails)
    assert cli.main(["install", "fetch-fzf"]) == 1
    assert capsys.readouterr().err.strip() == "no fzf build"


def test_install_run_passes_its_options_and_shows_each_line(mine, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(install, "install", lambda p, terminals, icon: calls.append((p, terminals, icon)) or ["a", "b"])
    assert cli.main(["install", "run", "--terminal", "kitty", "--icon", ""]) == 0
    assert calls == [(mine, "kitty", None)]
    assert capsys.readouterr().out == "a\nb\n"


def test_install_undo_fails_after_an_error_line(mine, monkeypatch, capsys):
    monkeypatch.setattr(install, "uninstall", lambda p: ["error: could not", "went on"])
    assert cli.main(["install", "undo"]) == 1
    assert capsys.readouterr().out == "error: could not\nwent on\n"


@pytest.mark.parametrize("action", ["add-hook", "remove-hook", "nope"])
def test_unknown_install_actions(mine, capsys, action):
    assert cli.main(["install", action, "x"]) == 2
    assert capsys.readouterr().err.strip() == f"unknown install action: {action}"


@pytest.mark.parametrize("argv", [["install", "run"], ["terminal", "open", "guake"]])
def test_unsupported_os(monkeypatch, capsys, argv):
    monkeypatch.setattr(platforms, "_current", None)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.strip() == "claude-launcher doesn't run on darwin yet"


def test_restore_header_without_fork(tmp_path, monkeypatch):
    # Windows' way: the core, run detached, puts the hints back through fzf's port.
    server = socket.create_server(("127.0.0.1", 0))
    got = []

    def serve():
        conn, _ = server.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data or not data.endswith(b"change-header:the hints"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            got.append(data)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    token = ["esc", 1234]
    (tmp_path / "state.json").write_text(json.dumps({"key": token, "listen": "localhost:0", "core": "core"}))
    (tmp_path / "header").write_text("the hints")
    monkeypatch.setenv("FZF_PORT", str(server.getsockname()[1]))
    monkeypatch.setenv("FZF_API_KEY", "k3y")
    monkeypatch.setattr(picker.time, "sleep", lambda s: None)
    assert cli.main(["--run", str(tmp_path), "ui", "restore-header", json.dumps(token)]) == 0
    thread.join(5)
    server.close()
    assert got and b"x-api-key: k3y\r\n" in got[0] and got[0].endswith(b"\r\n\r\nchange-header:the hints")


def test_restore_header_after_another_key_does_nothing(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(json.dumps({"key": ["esc", 99], "listen": "localhost:0", "core": "core"}))
    (tmp_path / "header").write_text("the hints")
    monkeypatch.setattr(picker.time, "sleep", lambda s: None)
    monkeypatch.setattr(picker, "send_action", lambda *a: pytest.fail("sent"))
    assert cli.main(["--run", str(tmp_path), "ui", "restore-header", json.dumps(["esc", 1])]) == 0


class Recorder:
    def __init__(self, run):
        pass

    def rows(self):
        return ["a", "b"]

    def __getattr__(self, name):
        return lambda *args: f"{name}{list(args)}"


@pytest.mark.parametrize(
    ("args", "out"),
    [
        (["menu"], "a\nb\n"),
        (["change", "q"], "on_change['q']"),
        (["enter", "row", "q"], "on_enter['row', 'q']"),
        (["enter", "row"], "on_enter['row', '']"),
        (["tab", "q"], "on_tab['q']"),
        (["sessions"], "on_sessions[]"),
        (["result", "row"], "on_result['row']"),
        (["dismiss", "row"], "on_dismiss['row']"),
        (["key", "esc", "q"], "on_key['esc', 'q']"),
    ],
)
def test_ui_actions(monkeypatch, capsys, tmp_path, args, out):
    monkeypatch.setattr(cli, "Picker", Recorder)
    assert cli.main(["--run", str(tmp_path), "ui", *args]) == 0
    assert capsys.readouterr().out == out


def test_unknown_ui_action(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "Picker", Recorder)
    assert cli.main(["--run", str(tmp_path), "ui", "nope"]) == 2
    assert "unknown ui action: nope" in capsys.readouterr().err
