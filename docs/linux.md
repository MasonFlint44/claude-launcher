# claude-launcher on Linux

How to install claude-launcher on Linux, what the installer sets up, the **Claude Code** app for each terminal, and how the picker tells which sessions a restart or logout closed. For the picker itself, see the [README](../README.md).

## Install

You need Linux with systemd (tested on Ubuntu 22.04 to 26.04, Debian 12 and 13, Fedora 42 and 43, and Arch), Python 3.10+, and [Claude Code](https://docs.claude.com/en/docs/claude-code), installed natively or with npm.

```bash
git clone https://github.com/MasonFlint44/claude-launcher.git ~/git/claude-launcher
~/git/claude-launcher/install.sh
```

The installer:

1. Checks for fzf 0.67 or newer. Ubuntu 24.04 and Debian ship older ones, so if yours is older (or missing), it downloads fzf 0.74.5 from fzf's GitHub releases, checks it against the release's checksum, and keeps it in `~/.local/share/claude-launcher/bin` for the launcher alone. Your own fzf stays as it is.
2. Adds `claude-session-hook` to Claude Code's user settings (`~/.claude/settings.json`, or `$CLAUDE_CONFIG_DIR/settings.json`) as a SessionStart and SessionEnd hook. Your other settings and hooks are left as they are. If `settings.json` is a link (into a dotfiles repo, say), the file it points to is edited and the link stays. Sessions started after this use the hook.
3. Links `~/.local/bin/claude-launcher` and `~/.local/bin/claude-session-hook` to the scripts in this repo, so `git pull` picks up changes.
4. Links the theme to `~/.config/ghostty/themes/claude-dark`.
5. Adds the app icon and a **Claude Code** desktop launcher for your terminal (see below), with a Claude Code profile in terminals that use profiles.

Re-running it is safe; it updates what's already there, and remembers the options you gave it last time. It never replaces a file that isn't one of its own links.

| Option | Does |
|---|---|
| `--terminal NAME[,NAME...]` | The terminal(s) **Claude Code** opens: `ghostty`, `kitty`, `wezterm`, `ptyxis`, `gnome-terminal`, `konsole`, `guake` or `default`. The first gets the plain name, the others "Claude Code (Guake)" and so on. `auto` goes back to picking. |
| `--icon FILE` | Your own app icon, an `.svg` or a square `.png`. A copy is kept, so the original can move. `--icon default` goes back to the one in `share/icons`. |
| `--uninstall` | Takes everything out again. |

## The Claude Code app

The installer picks the first of these that's installed, unless you give `--terminal` (drop-down terminals are only used when you ask for them):

| Terminal | What Claude Code opens | How |
|---|---|---|
| Ghostty | Its own app, with its own dock icon and windows, in Claude Dark. Every new tab and split opens the picker. | command-line options |
| kitty, WezTerm | The same, in their own window. | command-line options |
| Ptyxis, GNOME Terminal | A window with the **Claude Code** profile. New tabs from the new-tab shortcut keep it and open the picker; Ptyxis's + button and New Tab menu item open your default profile. | a profile |
| Konsole | A window with the **Claude Code** profile. Konsole opens new tabs with your default profile; pick Claude Code from the New Tab menu for another picker. | a profile |
| Guake | A new tab in Guake with the picker in it, and Guake shown (started first if it isn't running). Quitting the picker leaves you at a shell in that tab. | Guake's remote control |
| any other | Your desktop's default terminal, with the picker in its first window. | the desktop's default-terminal setting |

In each, quitting the picker closes its tab (except in Guake), and if the launcher fails, the tab drops to a shell instead.

Profiles are the only change made to a terminal's own settings (Ptyxis's also gets a Claude Dark palette, since Ptyxis ignores the background the launcher sets), and `--uninstall` (or switching `--terminal` away) takes them out. A Ghostty Flatpak can't run your `claude` from its sandbox; use Ghostty from your distro or a snap. Under GNOME on Wayland, Guake's own show/hide hotkey doesn't work; set a GNOME keyboard shortcut that runs `guake -t` instead.

## Colors in Linux terminals

Switching a tab to Claude Dark is tested in Ghostty, kitty, WezTerm, GNOME Terminal, Konsole, Guake, xterm, and tmux, where only that pane changes. Ptyxis (Ubuntu and Fedora's terminal) keeps its own background and changes only the text colors, so in its light style the picker is hard to read; the **Claude Code** launcher's Ptyxis profile is Claude Dark throughout. GNU screen is left alone, since it would change the colors of every window at once.

**The theme alone.** `theme = claude-dark` in a Ghostty config uses it anywhere.

## How it knows what a restart or logout closed

`claude-session-hook` keeps a small record for each interactive Claude in `~/.claude/state/claude-launcher/open/`. An exit you chose (`/exit`, `/clear`, switching with `/resume`) deletes the record straight away. Closing the tab, or the desktop closing every window, keeps it, with the time the session ended. When the picker opens, it checks those times against the systemd journal:

- **Restart or shutdown:** from when it began (logind's "System is rebooting." or "System is powering down.", or after a crash or power cut, the last thing logged) to the end of that boot.
- **Logout:** from when your desktop session ended (GNOME and KDE stop `graphical-session.target`, just before the windows close) to when the system was done with it. A Claude in an SSH session also counts when that SSH session ends. Other logins, like a script that connects over SSH on a schedule, don't count.

A session that ended in that span, or up to 30 seconds before it began (desktops close windows before the system hears about the restart or logout) or 10 seconds after it ended, was closed by it. So was one that never recorded an end at all.

Anything else, like a tab you closed yourself, isn't listed. The group is kept in `~/.claude/state/claude-launcher/restored.json`.

This works out of the box on Ubuntu, Debian and Fedora with GNOME or KDE. Desktop logouts come from your own user journal, which you can always read. Restarts are most precise with the system journal, and the end of an SSH login is only in it; reading it needs your user in the `adm`, `wheel` or `systemd-journal` group, as the first user on those distros is. Without it (on Arch, say, until you add yourself to one), the last thing in your own journal, usually your desktop session ending, stands in for the restart. It needs systemd, and a journal kept across reboots, as these distros do by default.

Only interactive Claude sessions in a terminal are recorded: not `claude -p`, SDK runs or an editor's Claude panel.

## Updating

```bash
git -C ~/git/claude-launcher pull
```

Changes to the launcher, hook and theme apply right away. If the desktop launcher changed (`src/claude_launcher/platforms/linux/`), re-run `install.sh`.

## Uninstalling

```bash
~/git/claude-launcher/install.sh --uninstall
```

This removes the links, the desktop launcher and the session hook. It leaves the launcher's state in `~/.claude/state/claude-launcher` and says how to remove it.
