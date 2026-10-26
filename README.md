# claude-launcher

A project and session picker for Claude Code on Linux, with a "Claude Code" app for your desktop and a Claude Dark look for your terminal.

Run `claude-launcher` (or open **Claude Code** from your apps) and you get a list of the directories you've used Claude in, most recent first. Pick one and Claude starts there, or press Tab to pick up a recent session instead. When Claude exits, you're back at the list.

```
  Select a project directory for Claude                   enter open   tab sessions   shift-tab dir sessions   esc quit

project>                                                                                                            3/3
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  Directory                                          Last used
▌ ~                                                  now
▌ ~/git/my-app                                       6m ago
▌ ~/git/website                                      2d ago
```

## What's included

| Path | What it is |
|---|---|
| `bin/claude-launcher` | The picker. Runs fzf, then `claude` in the chosen directory. |
| `bin/claude-session-hook` | A Claude Code hook that notes which sessions are open, so the picker can list the ones a restart or logout closed. |
| `themes/claude-dark` | The Claude Dark colors, as a Ghostty theme. |
| `share/icons/` | The app icon: a prompt in a speech bubble. `scripts/render-icons.sh` renders its PNGs from the SVG. |
| `install.sh` | Sets everything up, or with `--uninstall` takes it out. |
| `src/claude_launcher/` | The picker's logic, in Python (standard library only). |

## Install

You need Linux with systemd (tested on Ubuntu; written for Ubuntu, Debian, Fedora and Arch), Python 3.10+, and [Claude Code](https://docs.claude.com/en/docs/claude-code), installed natively or with npm.

```bash
git clone https://github.com/MasonFlint44/claude-launcher.git ~/git/claude-launcher
~/git/claude-launcher/install.sh
```

The installer:

1. Checks for fzf 0.67 or newer. Ubuntu 24.04 and Debian ship older ones, so if yours is older (or missing), it downloads fzf 0.74.5 from fzf's GitHub releases, checks it against the release's checksum, and keeps it in `~/.local/share/claude-launcher/bin` for the launcher alone. Your own fzf stays as it is.
2. Links `~/.local/bin/claude-launcher` and `~/.local/bin/claude-session-hook` to the scripts in this repo, so `git pull` picks up changes.
3. Adds `claude-session-hook` to Claude Code's user settings (`~/.claude/settings.json`, or `$CLAUDE_CONFIG_DIR/settings.json`) as a SessionStart and SessionEnd hook. Your other settings and hooks are left as they are. If `settings.json` is a link (into a dotfiles repo, say), the file it points to is edited and the link stays. Sessions started after this use the hook.
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

Profiles are the only change made to a terminal's own settings, and `--uninstall` (or switching `--terminal` away) takes them out. A Ghostty Flatpak can't run your `claude` from its sandbox; use Ghostty from your distro or a snap. Under GNOME on Wayland, Guake's own show/hide hotkey doesn't work; set a GNOME keyboard shortcut that runs `guake -t` instead.

## Colors

**Any terminal tab.** Run `claude-launcher` in a terminal tab and it switches to Claude Dark while the launcher runs, Claude included, then back to your exact colors when it exits. It asks the terminal for its colors first and only changes the ones it reports, so a terminal that can't do this is left alone. Tested in Ghostty, Ptyxis (Ubuntu and Fedora's terminal, built on the same VTE library as GNOME Terminal, GNOME Console and Xfce Terminal), xterm, and tmux, where only that pane changes; Konsole works the same way. GNU screen is left alone, since it would change the colors of every window at once. Set `CLAUDE_LAUNCHER_COLORS=0` to always keep your own colors, or `=1` to switch them inside screen anyway.

**The theme alone.** `theme = claude-dark` in a Ghostty config uses it anywhere.

## Using the picker

| Key | Does |
|---|---|
| Type | Filter the list |
| Enter | Open the highlighted directory |
| Tab | Show your sessions in all directories, most recent first; while typing a path, complete the highlighted directory |
| Shift-Tab | Show the highlighted directory's sessions |
| Ctrl-X | In a Sessions view, take the highlighted ↻ session out of the group a restart or logout closed |
| Alt-X, Alt-X | In a Sessions view, do that for every ↻ session it shows |
| Esc, Esc | Clear the search; on an empty search, quit. While typing a path, Esc backs up a directory instead |
| Ctrl-C | Clear the search; on an empty search, press twice to quit |
| Ctrl-D, Ctrl-D | On an empty search, quit |
| Ctrl-U | Clear the search at once, a path included |

Like in Claude, Esc, Ctrl-D and Ctrl-C on an empty search take two presses within a second. The first press shows what the second will do ("esc again to quit") where the key hints are. In a narrower window, hints that don't fit beside the title go on a line under them.

Your home directory is the top row, so Enter on an empty search opens it.

**Opening a directory by path:** type a path starting with `/` or `~` (e.g. `~/git/new-repo`), and the list becomes the directories in the one typed so far, like a shell's Tab completion:

```
  Directories in ~/git/                    enter open   tab complete   shift-tab dir sessions   esc back   ctrl-u clear

project> ~/git/
  Directory                                          Last used
▌ ~/git/                                             → claude-launcher, 2h ago
  ~/git/claude-launcher                              2h ago
  ~/git/dotfiles                                     → nvim/lua, 3d ago
  ~/git/archive
  ~/git/notes
```

- The title names the directory you've typed, and it's the first row until you start a name in it. A long path in the title is shortened from the left, so its end stays in view.
- Directories Claude ran in come first, most recent first, with when you last used them. One you've only used something inside of says which, dimmed (`→ nvim/lua, 3d ago`). The rest follow A to Z, hidden ones last.
- Typing filters by name as usual. Tab fills in the highlighted directory and lists what's in it; Enter opens it.
- Esc backs up a directory at a time (`~/git/cla` → `~/git/` → `~/` → the start screen), so it never quits from a path. Ctrl-U clears the whole path.
- Only real directories are listed. A path that matches none shows "Not a directory" on Enter, and one that doesn't exist says "No such directory: ~/x" in place of the title. Plain words (`launcher`) still search every directory Claude ran in, however deep.
- Relative paths (`./`, `../`) aren't supported.

Text that matches nothing shows "Nothing matches"; either way the list comes back.

**Where the list comes from:** each directory under `~/.claude/projects/` (or `$CLAUDE_CONFIG_DIR/projects/`) holds session logs for one project. The picker reads the working directory from the newest log in each, sorts by that log's date, and skips directories that no longer exist. Your home directory is always first. A directory you've never used Claude in won't be listed until you open it once by typing its path.

**Options for Claude:** `claude-launcher --model opus` passes `--model opus` to every Claude it starts.

## Resuming a session

Claude keeps sessions per directory, so `claude --resume <id>` only finds a session when it's run from the directory the session started in. The launcher finds that directory for you.

**Sessions view:** press Tab for your sessions in all directories, or Shift-Tab for the highlighted directory's sessions, most recently active first. Type to filter, and press Enter to resume the highlighted session in its directory. Esc or Tab goes back to the directory list.

```
    Last active Directory                Session
▌ ↻ 5m ago      my-app                   Playback scrubber fix
▌   2h ago      ~                        Dimming lights at sunset
▌   2w ago      workspace (missing)      Queue configuration
```

The list follows Claude Code's own `/resume` picker:

- **Title:** the name you gave the session with `/rename`, else Claude's own title for it, else your last prompt, else your first. A session where you only ran commands shows the command, like `/clear` or `! code .`.
- **Left out:** subagent and team sessions, `claude -p` and SDK runs, `/loop` sessions, sessions that were continued in a newer one, and sessions with no messages.
- **Fast:** like `/resume`, it reads only the first and last 64 KB of each session log. Claude deletes logs older than `cleanupPeriodDays` (30 days by default), so the list doesn't grow without end.

**Sessions whose directory isn't here** (deleted, or a devcontainer path) show the directory dimmed and marked "(missing)". Enter resumes them from `~`, after a second's note saying so.

**Other ways to resume:** paste a full session ID into the picker and press Enter, or run `claude-launcher --resume <id>` (or `-r <id>`) to skip the picker and resume it in its directory. Anything else after `--resume`, like a session name, is passed to `claude` unchanged once you pick a directory.

## After a restart or logout

When a restart or logging out closed Claude sessions, the picker shows which:

```
  Select a project directory for Claude                   enter open   tab sessions   shift-tab dir sessions   esc quit
  ↻ 3 sessions were open when your computer restarted (11:14 PM)   tab to see them
```

Press Tab and they're at the top of the Sessions view, marked ↻. Enter resumes one, and it leaves the group; Ctrl-X takes it out without resuming, and Alt-X twice takes out every ↻ session the view shows. A session stays in the group for 7 days, unless you resume or dismiss it first, or start it again from anywhere else.

**How it knows:** `claude-session-hook` keeps a small record for each interactive Claude in `~/.claude/state/claude-launcher/open/`. An exit you chose (`/exit`, `/clear`, switching with `/resume`) deletes the record straight away. Closing the tab, or the desktop closing every window, keeps it, with the time the session ended. When the picker opens, it checks those times against the systemd journal:

- **Restart:** from when the restart began (logind's "System is rebooting." message, or after a crash or power cut, the last thing logged) to the end of that boot.
- **Logout:** from when your desktop session ended (GNOME and KDE stop `graphical-session.target`, just before the windows close) to when the system was done with it. A Claude in an SSH session also counts when that SSH session ends. Other logins, like a script that connects over SSH on a schedule, don't count.

A session that ended in that span, or up to 30 seconds before it began (desktops close windows before the system hears about the restart or logout) or 10 seconds after it ended, was closed by it. So was one that never recorded an end at all.

Anything else, like a tab you closed yourself, isn't listed. The group is kept in `~/.claude/state/claude-launcher/restored.json`.

This works out of the box on Ubuntu, Debian and Fedora with GNOME or KDE. Logouts come from your own user journal, which you can always read. Restarts are most precise with the system journal, which needs your user in the `adm`, `wheel` or `systemd-journal` group, as the first user on those distros is. Without it (on Arch, say, until you add yourself to one), the last thing in your own journal, usually your desktop session ending, stands in for the restart. It needs systemd, and a journal kept across reboots, as these distros do by default.

Only interactive Claude sessions in a terminal are recorded: not `claude -p`, SDK runs or an editor's Claude panel.

## Updating

```bash
git -C ~/git/claude-launcher pull
```

Changes to the launcher, hook and theme apply right away. If the desktop launcher changed (`src/claude_launcher/install.py`), re-run `install.sh`.

## Uninstalling

```bash
~/git/claude-launcher/install.sh --uninstall
```

This removes the links, the desktop launcher and the session hook. It leaves the launcher's state in `~/.claude/state/claude-launcher` and says how to remove it.

## Development

See [CLAUDE.md](CLAUDE.md). In short: `uv run pytest` runs the tests, including end-to-end ones that drive the real launcher and fzf in a pseudo-terminal.

---

claude-launcher is an independent project. It isn't affiliated with or endorsed by Anthropic; Claude and Claude Code are Anthropic's trademarks.
