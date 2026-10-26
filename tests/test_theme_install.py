import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_terminal import DEFAULTS, FakeTerminal

from claude_launcher import install, theme
from claude_launcher.paths import THEME_FILE


class TestTheme:
    def test_parses_the_repo_theme(self):
        t = theme.Theme.load(THEME_FILE)
        assert len(t.palette) == 16
        assert t.colors["background"] == "#1f1e1d"
        assert t.accent == "#d77757"

    def test_ignores_comments_and_junk(self):
        t = theme.Theme.parse("# c\nbackground = 123456\npalette = 300=#ffffff\nfont-size = 12\nnonsense\n")
        assert t.colors == {"background": "#123456"} and t.palette == {}

    def test_slots(self):
        t = theme.Theme.parse("palette = 1=#ff0000\nbackground = #000000\ncursor-color = #010203\n")
        assert t.slots() == {"4;1": "#ff0000", "11": "#000000", "12": "#010203"}

    def test_ghostty_accepts_the_theme(self):
        ghostty = shutil.which("ghostty")
        if not ghostty:
            pytest.skip("ghostty not installed")
        done = subprocess.run(
            [ghostty, "+validate-config", f"--config-file={THEME_FILE}"], capture_output=True, text=True
        )
        assert done.returncode == 0, done.stdout + done.stderr


class TestHook:
    def test_add_keeps_other_settings_and_hooks(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"matcher": "x", "hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [other]}}))
        assert install.add_hook(path, Path("/bin/claude-session-hook"))
        settings = json.loads(path.read_text())
        assert settings["model"] == "opus"
        assert settings["hooks"]["SessionStart"][0] == other
        assert settings["hooks"]["SessionStart"][1]["hooks"][0]["command"] == "/bin/claude-session-hook start"
        assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "/bin/claude-session-hook end"

    def test_add_twice_changes_nothing(self, tmp_path):
        path = tmp_path / "settings.json"
        install.add_hook(path, Path("/x/claude-session-hook"))
        before = path.read_text()
        assert not install.add_hook(path, Path("/x/claude-session-hook"))
        assert path.read_text() == before

    def test_moved_hook_replaces_the_old_one(self, tmp_path):
        path = tmp_path / "settings.json"
        install.add_hook(path, Path("/old/claude-session-hook"))
        install.add_hook(path, Path("/new/claude-session-hook"))
        groups = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert [h["command"] for g in groups for h in g["hooks"]] == ["/new/claude-session-hook start"]

    def test_writes_through_a_symlink(self, tmp_path):
        real = tmp_path / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        os.chmod(real, 0o600)
        link = tmp_path / "settings.json"
        link.symlink_to(real)
        install.add_hook(link, Path("/x/claude-session-hook"))
        assert link.is_symlink()
        assert "SessionStart" in json.loads(real.read_text())["hooks"]
        assert real.stat().st_mode & 0o777 == 0o600

    def test_remove(self, tmp_path):
        path = tmp_path / "settings.json"
        other = {"hooks": [{"type": "command", "command": "other.sh"}]}
        path.write_text(json.dumps({"hooks": {"SessionEnd": [other]}}))
        install.add_hook(path, Path("/x/claude-session-hook"))
        assert install.remove_hook(path)
        assert json.loads(path.read_text()) == {"hooks": {"SessionEnd": [other]}}

    def test_remove_last_hook_removes_the_key(self, tmp_path):
        path = tmp_path / "settings.json"
        install.add_hook(path, Path("/x/claude-session-hook"))
        install.remove_hook(path)
        assert json.loads(path.read_text()) == {}

    def test_refuses_a_non_object(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[]")
        with pytest.raises(ValueError):
            install.add_hook(path, Path("/x/claude-session-hook"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rgb:eeee/eeee/ecec", "#eeeeec"),
        ("rgb:1f/1e/1d", "#1f1e1d"),
        ("rgb:f/0/8", "#ff0088"),
        ("rgba:ffff/0000/0000/ffff", "#ff0000"),
        ("#D77757", "#d77757"),
        ("nonsense", None),
    ],
)
def test_normalize(value, expected):
    assert theme.normalize(value) == expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TERM": "xterm-256color"}, True),
        ({"TERM": "tmux-256color", "TMUX": "/tmp/tmux-1000/default,1,0"}, True),
        ({"TERM": "screen", "STY": "123.pts-0"}, False),
        ({"TERM": "screen", "STY": "123.pts-0", "CLAUDE_LAUNCHER_COLORS": "1"}, True),
        ({"TERM": "xterm-256color", "CLAUDE_LAUNCHER_COLORS": "0"}, False),
        ({"TERM": "dumb"}, False),
        ({}, False),
    ],
)
def test_enabled(env, expected):
    assert theme.enabled(env) is expected


class TestTerminalColors:
    """Switching a terminal's colors and putting them back."""

    THEME = theme.Theme.load(THEME_FILE)

    @pytest.fixture
    def term(self):
        made = []

        def make(**kwargs):
            t = FakeTerminal(**kwargs)
            made.append(t)
            return t

        yield make
        for t in made:
            t.close()

    @staticmethod
    def settle(t):
        theme.ask(t.slave, [])  # the fake has handled everything sent before this

    def expected_applied(self, t):
        slots = self.THEME.slots()
        return {k: slots.get(k, v) for k, v in t.defaults.items()}

    def test_xterm_like(self, term, tmp_path):
        t = term()
        saved = tmp_path / "colors.json"
        assert theme.apply(t.slave, self.THEME, saved)
        self.settle(t)
        assert t.colors == self.expected_applied(t)
        theme.restore(t.slave, saved)
        self.settle(t)
        assert t.colors == DEFAULTS
        assert not saved.exists()
        after_resets = t.received[t.received.index("111") :]
        assert not any(";#" in r for r in after_resets)  # the resets were enough

    def test_konsole_like_has_no_dynamic_resets(self, term, tmp_path):
        t = term(reset_dynamic=False)
        saved = tmp_path / "colors.json"
        theme.apply(t.slave, self.THEME, saved)
        theme.restore(t.slave, saved)
        self.settle(t)
        assert t.colors == DEFAULTS  # set back by hand

    def test_terminal_that_wont_say_its_colors(self, term, tmp_path):
        t = term(answers=False)
        saved = tmp_path / "colors.json"
        assert not theme.apply(t.slave, self.THEME, saved)
        self.settle(t)
        assert t.colors == DEFAULTS and not saved.exists()
        assert not any(";#" in r for r in t.received)

    def test_only_what_it_reported_is_changed(self, term, tmp_path):
        t = term(only=["11"])
        saved = tmp_path / "colors.json"
        theme.apply(t.slave, self.THEME, saved)
        self.settle(t)
        assert t.colors == {"11": "#1f1e1d"}
        assert [r for r in t.received if ";#" in r] == ["11;#1f1e1d"]

    def test_no_answer_at_all(self, term, tmp_path, monkeypatch):
        monkeypatch.setattr(theme, "REPLY_TIMEOUT", 0.2)
        t = term(da1=False)
        assert not theme.apply(t.slave, self.THEME, tmp_path / "colors.json")

    def test_restore_without_apply(self, term, tmp_path):
        t = term()
        theme.restore(t.slave, tmp_path / "colors.json")
        self.settle(t)
        assert t.received == ["DA1"]
