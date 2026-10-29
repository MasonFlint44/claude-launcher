# Linux

The Linux platform, `src/claude_launcher/platforms/linux/`, and the rules for it. Imported into CLAUDE.md.

## Layout

| Path | Role |
|---|---|
| `bin/claude-session-hook` | Bash. Claude Code SessionStart/SessionEnd hook. Runs on every session start and end. |
| `platforms/linux/__init__.py` | Linux's `Platform`: the parts below, the fzf build name, and how fzf's key bindings run the core (`bash -c`, `shlex`). |
| `platforms/linux/host.py` | `/proc` (boot ID, uptime, whether Claude is running), `flock`, the controlling terminal and `termios`. |
| `platforms/linux/journal.py` | Restarts and logouts from the systemd journal (`JournalEvents`). |
| `platforms/linux/setup.py` | What install.sh sets up after fzf and the hook (`install.py`): the links in `~/.local/bin`, the Ghostty theme link, then `desktop.py` (desktop entries, the icon) and `terminals.py` (the terminals the desktop launcher can open, one class each, and how). Also the core's `terminal open guake`, which Guake's desktop entry runs. |
| `tests/docker/` | The suite on other distros, every terminal on a virtual display, and real systemd per version. See its README. |

## Commands

```bash
tests/docker/run.sh distro       # in Docker: 8 distros (about 5 min)
tests/docker/run.sh terminals    # every terminal, live (about 1 min)
tests/docker/run.sh systemd      # logout and restart, systemd 249 to 262 (about 3 min)
```

Run the layer a change touches: install or setup (distro), terminals or colors (terminals), the hook or the journal (systemd).

## Rules

- **Installed paths don't move.** Installs point into the repo: `~/.local/bin` links to `bin/claude-launcher` and `bin/claude-session-hook` (and settings.json names the hook's link), desktop entries name `themes/claude-dark` and, for Guake, `libexec/claude-launcher-core terminal open guake`. Moving any of these breaks installs until install.sh runs again.
- **The hook stays bash, quick, and always exits 0.** No Python startup. Its record format is the contract in CLAUDE.md.
- **Restart and logout detection is systemd-specific, and has to work beyond one machine.** See the `restart.py` docstring, the comment on `LEAD`/`TAIL`, and `JournalEvents` and `SPAN_MAX` in `platforms/linux/journal.py`. Restarts and logouts are both events with a start and an end, under one rule: a Claude that ended from `LEAD` before the start to `TAIL` after the end was closed by it. Restart: logind's shutdown message (`SHUTDOWN_MESSAGE_ID`) to the boot's last entry. Logout: the user manager stopping `graphical-session.target` (`UNIT_STOPPED_MESSAGE_ID`, user journal, readable without any group; GNOME 3.34+ and Plasma 5.25+ use it). This is the main desktop signal, because logind's session `TYPE` field only exists from systemd 258, so Ubuntu 24.04 (255), Debian 13 and Fedora 42 (257) can't tell desktop sessions apart in logind's messages. logind's "Session N logged out" (`CODE_FUNC=session_stop_scope`) to "Removed session" (capped at `SPAN_MAX`) still counts, for desktop sessions where `TYPE` exists and for the Claude's own login session (SSH). Without logind's shutdown message (a crash, or no access to the system journal), the boot's last journal entry is the restart; with only the user journal that's near the desktop's end, so a logout and a later reboot can read as one restart. Keep the margins generous: desktops differ in what they close first (GNOME closes windows ~0.5 s before logout; KDE logs out before rebooting), and a false ↻ row is cheap while a missed session is not. Don't count non-desktop sessions in general: scheduled SSH logins (this machine has one every 30 minutes) would misfile ordinary tab closes. Use the journal's structured fields, not message text. Before systemd 251, `journalctl --list-boots` ignores `-o json` and prints text (Ubuntu 22.04); `journal.py` reads the IDs from that and looks up each boot's last entry. `tests/docker/systemd` checks all of this against each version.
- **Terminals stay a short table** (`TERMINALS` in `platforms/linux/terminals.py`). Add one only if it can run the launcher with options, a profile, or a remote control, and check from its source (or by running it) what a new tab inherits before claiming "every tab". Profiles are the only change made to a terminal's settings, and uninstall must take them out. Ptyxis treats the first profile as the default when its default doesn't name one, so a fresh Ptyxis gets a plain default profile before ours. Ptyxis paints its own background and ignores OSC 11 (it accepts it, but never shows it), so our Ptyxis profile also gets a Claude Dark palette, written from `themes/claude-dark` into Ptyxis's palettes directory; in other Ptyxis tabs only the text colors change. Guake's `-e` types into the current tab and, with `-n`, skips making the tab: two calls. Yakuake was left out: its only show is a toggle. All seven are tested live in `tests/docker/terminals`; Ghostty, Ptyxis, Guake and kitty also on a real GNOME desktop.
- **Claude may be the native binary or npm's package run by node**, whose process name is "MainThread". Recognize it by its command line (`is_claude` in `platforms/linux/host.py`, and in the hook), never by process name.
