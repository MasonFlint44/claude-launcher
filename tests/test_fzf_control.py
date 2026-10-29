"""Talking to fzf while it runs: the key hints put back a second after a
first press, through fzf's --listen socket or port."""

import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from claude_launcher import cli, picker


class Listener:
    """Takes one request, as fzf's --listen does, and keeps it."""

    def __init__(self, server: socket.socket):
        self.server = server
        self.request = b""
        self.thread = threading.Thread(target=self._take, daemon=True)
        self.thread.start()

    def _take(self):
        conn, _ = self.server.accept()
        with conn:
            while b"\r\n\r\n" not in self.request or not self._complete():
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.request += chunk
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    def _complete(self):
        head, _, body = self.request.partition(b"\r\n\r\n")
        length = next(int(h.split(b":")[1]) for h in head.split(b"\r\n") if h.lower().startswith(b"content-length"))
        return len(body) >= length

    def got(self):
        self.thread.join(5)
        head, _, body = self.request.partition(b"\r\n\r\n")
        return head.decode(), body.decode()


def test_through_a_port():
    with socket.create_server(("127.0.0.1", 0)) as server:
        listener = Listener(server)
        env = {"FZF_PORT": str(server.getsockname()[1]), "FZF_API_KEY": "k1"}
        picker.send_action("localhost:0", "change-header:hi", env)
        head, body = listener.got()
    assert head.startswith("POST / HTTP/1.1") and "x-api-key: k1" in head
    assert body == "change-header:hi"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets")
def test_through_a_socket(tmp_path):
    path = str(tmp_path / "fzf.sock")
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        listener = Listener(server)
        picker.send_action(path, "change-header:hi", {"FZF_API_KEY": "k2"})
        head, body = listener.got()
    assert "x-api-key: k2" in head and body == "change-header:hi"


def test_without_fork_it_starts_the_core_detached(tmp_path, monkeypatch):
    # Windows has no fork: the second half of a first press is its own process.
    started = []
    monkeypatch.delattr(os, "fork", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    picker._restore_header_later(tmp_path, ["esc", 1.5])
    [(args, kw)] = started
    assert args[0] == sys.executable
    assert args[2:] == ["--run", str(tmp_path), "ui", "restore-header", json.dumps(["esc", 1.5])]
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL


def test_the_core_puts_the_header_back(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(picker, "restore_header", lambda run, token: calls.append((run, token)))
    assert cli.main(["--run", str(tmp_path), "ui", "restore-header", '["esc", 1.5]']) == 0
    assert calls == [(tmp_path, ["esc", 1.5])]
