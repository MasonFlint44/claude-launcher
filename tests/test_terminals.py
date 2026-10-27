"""The desktop launcher's terminals: their Exec lines, profiles, and Guake."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_launcher import install, terminals
from claude_launcher.terminals import APP_ID, PROFILE_UUID, TERMINALS, Context
from claude_launcher.theme import Theme


class FakeGSettings:
    """`gsettings get/set/reset-recursively`, kept in a dict."""

    LISTS = {("org.gnome.Ptyxis", "profile-uuids"), ("org.gnome.Terminal.ProfilesList", "list")}

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[0] != "gsettings":
            return subprocess.CompletedProcess(argv, 0, "", "")
        op, schema = argv[1], argv[2]
        if op == "get":
            if (schema, argv[3]) in self.values:
                return subprocess.CompletedProcess(argv, 0, self.values[(schema, argv[3])] + "\n", "")
            if (schema, argv[3]) in self.LISTS:
                return subprocess.CompletedProcess(argv, 0, "@as []\n", "")
            return subprocess.CompletedProcess(argv, 1, "", "No such key")
        if op == "set":
            self.values[(schema, argv[3])] = argv[4]
        elif op == "reset-recursively":
            self.values = {k: v for k, v in self.values.items() if k[0] != schema}
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def ctx(tmp_path):
    return Context(
        launcher=Path("/home/me/.local/bin/claude-launcher"),
        core=Path("/repo/libexec/claude-launcher-core"),
        home=Path("/home/me"),
        data_home=tmp_path / "share",
        run=FakeGSettings(),
    )


def exec_line(entry: str) -> list[str]:
    """The Exec line's arguments, read as the Desktop Entry spec says: string
    escapes first, then the quoting, then %%."""
    line = next(line for line in entry.splitlines() if line.startswith("Exec="))[5:]
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    line = re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(0)), line)
    args, arg, quoted, i = [], None, False, 0
    while i < len(line):
        c = line[i]
        if quoted and c == "\\":
            i += 1
            arg += line[i]
        elif c == '"':
            quoted, arg = not quoted, arg or ""
        elif c == " " and not quoted:
            if arg is not None:
                args.append(arg)
            arg = None
        else:
            arg = (arg or "") + c
        i += 1
    assert not quoted
    if arg is not None:
        args.append(arg)
    return [a.replace("%%", "%") for a in args]


def test_command(ctx):
    assert ctx.argv == ["bash", "-lc", "/home/me/.local/bin/claude-launcher || exec bash"]


ODD_PATH = Path("/home/me/odd dir/it's $HOME, `here` \\ 100%/claude-launcher")


@pytest.mark.parametrize("name", list(TERMINALS))
def test_exec_line_round_trips(ctx, name):
    ctx.launcher = ODD_PATH
    term = TERMINALS[name]
    assert exec_line(install.desktop_entry(term, ctx, primary=True)) == term.exec_argv(ctx)


GLIB_EXEC = """
import sys, gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib
kf = GLib.KeyFile()
kf.load_from_file(sys.argv[1], GLib.KeyFileFlags.NONE)
ok, argv = GLib.shell_parse_argv(kf.get_string("Desktop Entry", "Exec"))
print("\\0".join(a.replace("%%", "%") for a in argv), end="")
"""


def _system_glib() -> bool:
    return subprocess.run(["/usr/bin/python3", "-c", "import gi"], capture_output=True).returncode == 0


@pytest.mark.skipif(not os.path.exists("/usr/bin/python3") or not _system_glib(), reason="needs PyGObject")
@pytest.mark.parametrize("name", list(TERMINALS))
def test_glib_reads_the_exec_line(ctx, name, tmp_path):
    """What GNOME does with the entry: GLib's key file, then its shell parser."""
    ctx.launcher = ODD_PATH
    term = TERMINALS[name]
    entry = tmp_path / "x.desktop"
    entry.write_text(install.desktop_entry(term, ctx, primary=True))
    out = subprocess.run(
        ["/usr/bin/python3", "-I", "-c", GLIB_EXEC, str(entry)], capture_output=True, text=True, check=True
    )
    assert out.stdout.split("\0") == term.exec_argv(ctx)
    if shutil.which("desktop-file-validate"):
        check = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
        assert check.returncode == 0, check.stdout + check.stderr


class TestOptions:
    def test_ghostty(self, ctx):
        argv = TERMINALS["ghostty"].exec_argv(ctx)
        assert f"--class={APP_ID}" in argv and f"--command={ctx.command}" in argv

    def test_kitty_shell_is_the_launcher(self, ctx):
        argv = TERMINALS["kitty"].exec_argv(ctx)
        shell = argv[argv.index("-o") + 1]
        assert shell.startswith("shell=") and shlex.split(shell[6:]) == ctx.argv

    def test_wezterm_default_prog_is_lua(self, ctx):
        ctx.launcher = Path('/tmp/a "quoted" \\ path/claude-launcher')
        argv = TERMINALS["wezterm"].exec_argv(ctx)
        prog = argv[argv.index("--config") + 1]
        assert prog.startswith("default_prog={") and argv[argv.index("start") + 1 :][:2] == ["--class", APP_ID]
        # Lua's "..." strings use the same escapes as JSON for these characters.
        assert json.loads("[" + prog[len("default_prog={") : -1] + "]") == ctx.argv


class TestProfiles:
    def test_ptyxis(self, ctx):
        ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")] = "['aaaa']"
        TERMINALS["ptyxis"].install(ctx)
        profile = f"org.gnome.Ptyxis.Profile:/org/gnome/Ptyxis/Profiles/{PROFILE_UUID.hex}/"
        assert ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")] == f"['aaaa', '{PROFILE_UUID.hex}']"
        assert ctx.run.values[(profile, "label")] == "'Claude Code'"
        assert ctx.run.values[(profile, "use-custom-command")] == "true"
        assert ctx.run.values[(profile, "custom-command")] == terminals._gvariant_string(ctx.command)
        assert ctx.run.values[(profile, "palette")] == f"'{APP_ID}'"
        assert TERMINALS["ptyxis"].exec_argv(ctx) == [
            "ptyxis", "--new-window", f"--tab-with-profile={PROFILE_UUID.hex}",
        ]  # fmt: skip

    def test_ptyxis_palette_is_claude_dark(self, ctx):
        # Ptyxis ignores a background set by a program, so the profile brings its own.
        TERMINALS["ptyxis"].install(ctx)
        keys = dict(
            line.split("=", 1) for line in TERMINALS["ptyxis"].palette_file(ctx).read_text().splitlines() if "=" in line
        )
        theme = Theme.load(ctx.theme)
        assert keys["Name"] == "Claude Dark"
        assert keys["Background"] == theme.colors["background"] and keys["Foreground"] == theme.colors["foreground"]
        assert keys["Cursor"] == theme.accent
        assert [keys[f"Color{i}"] for i in range(16)] == [theme.palette[i] for i in range(16)]
        palettes = ctx.data_home / "org.gnome.Ptyxis" / "palettes"
        assert TERMINALS["ptyxis"].palette_file(ctx) == palettes / f"{APP_ID}.palette"

    def test_ptyxis_palette_needs_every_color(self):
        with pytest.raises(RuntimeError, match="Color15"):
            terminals.ptyxis_palette(Theme.parse("background = #000000\nforeground = #ffffff\ncursor-color = #ff0000"))

    def test_ptyxis_uninstall_removes_the_palette(self, ctx):
        TERMINALS["ptyxis"].install(ctx)
        TERMINALS["ptyxis"].uninstall(ctx)
        assert not TERMINALS["ptyxis"].palette_file(ctx).exists()

    def test_ptyxis_keeps_its_default(self, ctx):
        ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")] = "['aaaa', 'bbbb']"
        ctx.run.values[("org.gnome.Ptyxis", "default-profile-uuid")] = "'bbbb'"
        TERMINALS["ptyxis"].install(ctx)
        assert ctx.run.values[("org.gnome.Ptyxis", "default-profile-uuid")] == "'bbbb'"

    def test_fresh_ptyxis_gets_a_plain_default_first(self, ctx):
        TERMINALS["ptyxis"].install(ctx)
        ids = json.loads(ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")].replace("'", '"'))
        assert len(ids) == 2 and ids[1] == PROFILE_UUID.hex
        assert ctx.run.values[("org.gnome.Ptyxis", "default-profile-uuid")] == f"'{ids[0]}'"

    def test_ptyxis_default_pointing_nowhere(self, ctx):
        ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")] = "['aaaa']"
        ctx.run.values[("org.gnome.Ptyxis", "default-profile-uuid")] = "''"
        TERMINALS["ptyxis"].install(ctx)
        assert ctx.run.values[("org.gnome.Ptyxis", "default-profile-uuid")] == "'aaaa'"

    def test_install_twice_lists_it_once(self, ctx):
        TERMINALS["ptyxis"].install(ctx)
        TERMINALS["ptyxis"].install(ctx)
        ids = ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")]
        assert ids.count(PROFILE_UUID.hex) == 1 and ids.count("'") == 4  # ours and the plain default

    def test_uninstall_leaves_other_profiles(self, ctx):
        ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")] = "['aaaa']"
        TERMINALS["ptyxis"].install(ctx)
        TERMINALS["ptyxis"].uninstall(ctx)
        assert ctx.run.values == {
            ("org.gnome.Ptyxis", "profile-uuids"): "['aaaa']",
            ("org.gnome.Ptyxis", "default-profile-uuid"): "'aaaa'",  # it pointed nowhere before
        }

    def test_gnome_terminal(self, ctx):
        TERMINALS["gnome-terminal"].install(ctx)
        profile = f"org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{PROFILE_UUID}/"
        assert ctx.run.values[("org.gnome.Terminal.ProfilesList", "list")] == f"['{PROFILE_UUID}']"
        assert ctx.run.values[(profile, "visible-name")] == "'Claude Code'"
        assert ctx.run.values[(profile, "exit-action")] == "'close'"

    def test_profiles_unreadable(self, ctx):
        ctx.run = lambda argv: subprocess.CompletedProcess(argv, 1, "", "No such schema")
        with pytest.raises(RuntimeError, match="can't read Ptyxis"):
            TERMINALS["ptyxis"].install(ctx)

    def test_konsole(self, ctx):
        konsole = TERMINALS["konsole"]
        konsole.install(ctx)
        path = ctx.data_home / "konsole" / "ClaudeLauncher.profile"
        text = path.read_text()
        assert "Name=Claude Code\n" in text and f"Command={ctx.command}\n" in text
        assert konsole.exec_argv(ctx) == ["konsole", "--profile", str(path)]
        konsole.uninstall(ctx)
        assert not path.exists()

    def test_gvariant_string_escapes(self):
        assert terminals._gvariant_string("it's a \\ path") == "'it\\'s a \\\\ path'"


class TestGuake:
    def fake(self, running):
        calls = []

        def run(argv):
            calls.append(list(argv))
            if argv[0] == "gdbus":
                return subprocess.CompletedProcess(argv, 0, f"({'true' if running() else 'false'},)\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        return run, calls

    def test_running_opens_a_new_tab(self, ctx):
        ctx.run, calls = self.fake(lambda: True)
        TERMINALS["guake"].open(ctx, spawn=lambda argv: pytest.fail("shouldn't start Guake"))
        guake = [c for c in calls if c[0] == "guake"]
        assert guake == [["guake", "-n", "/home/me"], ["guake", "-e", shlex.join(ctx.argv), "--show"]]

    def test_not_running_starts_it_with_the_launcher(self, ctx, monkeypatch):
        monkeypatch.setattr(terminals.time, "sleep", lambda s: None)
        started = []
        ctx.run, calls = self.fake(lambda: bool(started))
        TERMINALS["guake"].open(ctx, spawn=started.append)
        assert started == [["guake", "-e", shlex.join(ctx.argv), "--show"]]
        assert not [c for c in calls if c[0] == "guake"]

    def test_exec_line(self, ctx):
        assert TERMINALS["guake"].exec_argv(ctx) == [str(ctx.core), "terminal", "open", "guake", str(ctx.launcher)]


class TestPick:
    def test_explicit(self):
        assert [t.name for t in terminals.pick(["guake", "ghostty", "guake"])] == ["guake", "ghostty"]

    def test_unknown(self):
        with pytest.raises(ValueError, match="unknown terminal: alacritty"):
            terminals.pick(["alacritty"])

    def test_auto_takes_the_first_installed(self, monkeypatch):
        have = {"konsole", "guake", "ptyxis"}
        monkeypatch.setattr(terminals.shutil, "which", lambda p: f"/usr/bin/{p}" if p in have else None)
        assert [t.name for t in terminals.pick(None)] == ["ptyxis"]

    def test_auto_never_picks_a_drop_down(self, monkeypatch):
        monkeypatch.setattr(terminals.shutil, "which", lambda p: "/usr/bin/guake" if p == "guake" else None)
        assert [t.name for t in terminals.pick(None)] == ["default"]


class TestInstallTerminals:
    @pytest.fixture(autouse=True)
    def everything_installed(self, monkeypatch):
        monkeypatch.setattr(terminals.Terminal, "installed", lambda self: True)
        monkeypatch.setattr(install.shutil, "which", lambda p: None)  # no desktop-database tools

    def entries(self, ctx):
        return sorted(p.name for p in (ctx.data_home / "applications").iterdir())

    def test_primary_and_extra_entries(self, ctx):
        install.install_terminals(ctx, ["ghostty", "guake"])
        assert self.entries(ctx) == [f"{APP_ID}.desktop", f"{APP_ID}.guake.desktop"]
        primary = (ctx.data_home / "applications" / f"{APP_ID}.desktop").read_text()
        extra = (ctx.data_home / "applications" / f"{APP_ID}.guake.desktop").read_text()
        assert "Name=Claude Code\n" in primary and f"StartupWMClass={APP_ID}\n" in primary
        assert "Name=Claude Code (Guake)\n" in extra and "StartupWMClass" not in extra
        assert "Terminal=false\n" in primary and f"Icon={APP_ID}\n" in extra

    def test_fallback_uses_the_default_terminal(self, ctx):
        install.install_terminals(ctx, ["default"])
        entry = (ctx.data_home / "applications" / f"{APP_ID}.desktop").read_text()
        assert "Terminal=true\n" in entry and exec_line(entry) == ctx.argv

    def test_choice_is_remembered(self, ctx):
        install.install_terminals(ctx, ["ptyxis"])
        install.install_terminals(ctx, None)
        assert install.saved_terminals(ctx) == ["ptyxis"]
        assert "ptyxis" in (ctx.data_home / "applications" / f"{APP_ID}.desktop").read_text()

    def test_auto_forgets_the_choice(self, ctx):
        install.install_terminals(ctx, ["ptyxis"])
        install.install_terminals(ctx, ["auto"])
        assert install.saved_terminals(ctx) == ["ghostty"]  # the first in AUTO_ORDER

    def test_switching_away_removes_the_profile(self, ctx):
        install.install_terminals(ctx, ["ptyxis"])
        assert ("org.gnome.Ptyxis", "profile-uuids") in ctx.run.values
        install.install_terminals(ctx, ["ghostty"])
        assert PROFILE_UUID.hex not in ctx.run.values[("org.gnome.Ptyxis", "profile-uuids")]
        assert self.entries(ctx) == [f"{APP_ID}.desktop"]

    def test_not_installed(self, ctx, monkeypatch):
        monkeypatch.setattr(terminals.Terminal, "installed", lambda self: False)
        with pytest.raises(ValueError, match="not installed: kitty"):
            install.install_terminals(ctx, ["kitty"])

    def test_failed_profile_keeps_the_old_entries(self, ctx, monkeypatch):
        install.install_terminals(ctx, ["ghostty"])
        before = (ctx.data_home / "applications" / f"{APP_ID}.desktop").read_text()

        def no_dconf(self, ctx):
            raise subprocess.CalledProcessError(1, ["gsettings"])

        monkeypatch.setattr(terminals.Ptyxis, "install", no_dconf)
        with pytest.raises(subprocess.CalledProcessError):
            install.install_terminals(ctx, ["ptyxis"])
        assert self.entries(ctx) == [f"{APP_ID}.desktop"]
        assert (ctx.data_home / "applications" / f"{APP_ID}.desktop").read_text() == before
        assert install.saved_terminals(ctx) == ["ghostty"]

    def test_remove(self, ctx):
        install.install_terminals(ctx, ["konsole", "guake"])
        install.remove_terminals(ctx)
        assert self.entries(ctx) == []
        assert not (ctx.data_home / "konsole" / "ClaudeLauncher.profile").exists()
        assert install.saved_terminals(ctx) is None


class TestIcon:
    @pytest.fixture(autouse=True)
    def no_cache_tools(self, monkeypatch):
        monkeypatch.setattr(install.shutil, "which", lambda p: None)

    def installed(self, ctx):
        theme = ctx.data_home / "icons" / "hicolor"
        return sorted(str(p.relative_to(theme)) for p in theme.rglob(f"{APP_ID}.*"))

    def test_default(self, ctx):
        assert install.install_icon(ctx, None) == "Installed the default icon"
        assert self.installed(ctx) == sorted(
            ["scalable/apps/" + APP_ID + ".svg"] + [f"{s}x{s}/apps/{APP_ID}.png" for s in install.ICON_SIZES]
        )

    def test_your_svg_replaces_it(self, ctx, tmp_path):
        mine = tmp_path / "clawd.svg"
        mine.write_text("<svg/>")
        install.install_icon(ctx, None)
        install.install_icon(ctx, str(mine))
        assert self.installed(ctx) == ["scalable/apps/" + APP_ID + ".svg"]
        assert (ctx.data_home / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg").read_text() == "<svg/>"
        mine.unlink()  # a copy is kept, so the icon survives this
        install.install_icon(ctx, None)
        assert (ctx.data_home / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg").read_text() == "<svg/>"

    def test_your_png_goes_by_its_size(self, ctx):
        install.install_icon(ctx, str(install.ICONS_DIR / "claude-launcher-256.png"))
        assert self.installed(ctx) == [f"256x256/apps/{APP_ID}.png"]

    def test_odd_png_size_comes_out(self, ctx, tmp_path):
        big = tmp_path / "big.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (1024).to_bytes(4, "big") * 2)
        install.install_icon(ctx, str(big))
        assert self.installed(ctx) == [f"1024x1024/apps/{APP_ID}.png"]
        install.install_icon(ctx, "default")
        assert f"1024x1024/apps/{APP_ID}.png" not in self.installed(ctx)
        install.remove_icon(ctx)
        assert self.installed(ctx) == []

    def test_back_to_default(self, ctx, tmp_path):
        mine = tmp_path / "x.svg"
        mine.write_text("<svg/>")
        install.install_icon(ctx, str(mine))
        install.install_icon(ctx, "default")
        assert len(self.installed(ctx)) == 1 + len(install.ICON_SIZES)

    def test_bad_icons(self, ctx, tmp_path):
        (tmp_path / "x.jpg").write_text("")
        with pytest.raises(ValueError, match="svg or .png"):
            install.install_icon(ctx, str(tmp_path / "x.jpg"))
        wide = tmp_path / "wide.png"
        wide.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (64).to_bytes(4, "big") + (32).to_bytes(4, "big"))
        with pytest.raises(ValueError, match="square"):
            install.install_icon(ctx, str(wide))

    def test_shipped_pngs_are_the_sizes_installed(self):
        for size in install.ICON_SIZES:
            assert install._png_size(install.ICONS_DIR / f"claude-launcher-{size}.png") == size
