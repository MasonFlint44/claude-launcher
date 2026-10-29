# claude-launcher

A project and session picker for Claude Code, with a "Claude Code" app for your desktop and a Claude Dark look for your terminal. It runs on Linux, Windows and WSL.

Run `claude-launcher` (or open **Claude Code** from your apps) and you get a list of the directories you've used Claude in, most recent first. Pick one to see its sessions and resume one, or start a new session there; Ctrl-S shows your recent sessions everywhere. When Claude exits, you're back at the list.

```
  Select a project directory for Claude                          enter open   tab complete   ctrl-s sessions   esc quit

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
| `src/claude_launcher/` | The picker's logic, in Python (standard library only). What differs by OS is in `platforms/`. |
| `docs/` | Installing and using it on each OS ([Linux](docs/linux.md), [Windows](docs/windows.md), [WSL](docs/wsl.md)), and [adding a platform](docs/dev/platforms.md). |

## Install

On Linux (with systemd, Python 3.10+ and [Claude Code](https://docs.claude.com/en/docs/claude-code)):

```bash
git clone https://github.com/MasonFlint44/claude-launcher.git ~/git/claude-launcher
~/git/claude-launcher/install.sh
```

[docs/linux.md](docs/linux.md) has what the installer sets up, its options, the **Claude Code** app for each terminal, updating and uninstalling.

On Windows (with [Claude Code](https://docs.claude.com/en/docs/claude-code) and Git; the installer offers to install Python if you don't have it), in PowerShell:

```powershell
git clone https://github.com/MasonFlint44/claude-launcher.git $HOME\git\claude-launcher
powershell -ExecutionPolicy Bypass -File $HOME\git\claude-launcher\install.ps1
```

Then open a **Claude Code** tab in Windows Terminal. [docs/windows.md](docs/windows.md) has the details. In WSL, clone and run `install.sh` inside your distro, as on Linux: [docs/wsl.md](docs/wsl.md).

## Colors

Run `claude-launcher` in a terminal tab and it switches to Claude Dark while the launcher runs, Claude included, then back to your exact colors when it exits. It asks the terminal for its colors first and only changes the ones it reports, so a terminal that can't do this is left alone. GNU screen is left alone too, since it would change the colors of every window at once. Set `CLAUDE_LAUNCHER_COLORS=0` to always keep your own colors, or `=1` to switch them inside screen anyway. Which terminals are tested, and the Ghostty theme on its own: [docs/linux.md](docs/linux.md#colors-in-linux-terminals).

## Using the picker

| Key | Does |
|---|---|
| Type | Filter the list |
| Enter | Show the highlighted directory's sessions, or start Claude there straight away if it has none. In a Sessions view, resume the highlighted session, or start a new one on "+ New session" |
| Tab | Fill in the highlighted directory's path, and list what's in it (see "Opening a directory by path" below) |
| Ctrl-S | Show your sessions in all directories, most recent first; from there, back to the directory list |
| Ctrl-X | In a Sessions view, take the highlighted ↻ session out of the group a restart or logout closed |
| Alt-X, Alt-X | In a Sessions view, do that for every ↻ session it shows |
| Esc, Esc | Clear the search; on an empty search, quit. While typing a path, Esc backs up a directory instead, and in a Sessions view it goes back to the directory list |
| Ctrl-C | Clear the search; on an empty search, press twice to quit |
| Ctrl-D, Ctrl-D | On an empty search, quit |
| Ctrl-U | Delete back to the start of the search, a path included |

Like in Claude, Esc, Ctrl-D and Ctrl-C on an empty search take two presses within a second. The first press shows what the second will do ("esc again to quit") where the key hints are. In a narrower window, hints that don't fit beside the title go on a line under them.

Your home directory is the top row, so Enter on an empty search opens it, through its sessions if it has any.

**Opening a directory by path:** type a path starting with `/` or `~` (e.g. `~/git/new-repo`), or press Tab on a directory in the list, and the list becomes the directories in the one typed so far, like a shell's Tab completion:

```
  Directories in ~/git/                           enter open   tab complete   ctrl-s sessions   esc back   ctrl-u clear

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
- Typing filters by name as usual. Tab fills in the highlighted directory and lists what's in it; Enter opens it (through its sessions, if it has any).
- Esc backs up a directory at a time (`~/git/cla` → `~/git/` → `~/` → the start screen), so it never quits from a path. Ctrl-U clears the whole path.
- Only real directories are listed. A path that matches none shows "Not a directory" on Enter, and one that doesn't exist says "No such directory: ~/x" in place of the title. Plain words (`launcher`) still search every directory Claude ran in, however deep.
- Relative paths (`./`, `../`) aren't supported.

Text that matches nothing shows "Nothing matches"; either way the list comes back.

**Where the list comes from:** each directory under `~/.claude/projects/` (or `$CLAUDE_CONFIG_DIR/projects/`) holds session logs for one project. The picker reads the working directory from the newest log in each, sorts by that log's date, and skips directories that no longer exist. Your home directory is always first. A directory you've never used Claude in won't be listed until you open it once by typing its path.

**Options for Claude:** `claude-launcher --model opus` passes `--model opus` to every Claude it starts.

## Resuming a session

Claude keeps sessions per directory, so `claude --resume <id>` only finds a session when it's run from the directory the session started in. The launcher finds that directory for you.

**A directory's sessions:** Enter on a directory shows the sessions that ran in it or anywhere inside it, most recently active first, under a "+ New session" row. The cursor starts on "+ New session", so Enter, Enter starts a new session, and Down, Enter resumes the latest one. A directory with no sessions opens straight away.

```
  Claude sessions in ~/git/my-app                                         enter resume   ctrl-s all sessions   esc back

session>                                                                                                            4/4
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    Last active Directory                Session
▌   + New session
▌   5m ago      my-app                   Playback scrubber fix
▌   1d ago      my-app/tests             Flaky upload test
▌   2w ago      my-app                   Queue configuration
```

The Directory column says where each session ran, so a session in a subdirectory resumes there. For `~` (and `/`), that's everything inside it, so its view is nearly all your sessions, with the ones you ran in `~` itself among them.

**All sessions:** Ctrl-S shows your sessions in every directory, most recently active first.

```
    Last active Directory                Session
▌ ↻ 5m ago      my-app                   Playback scrubber fix
▌   2h ago      ~                        Dimming lights at sunset
▌   2w ago      workspace (missing)      Queue configuration
```

In either view, type to filter (it matches the directory and the title), and press Enter to resume the highlighted session in its directory. Esc goes back to the directory list, with the search and the cursor where you left them.

The list follows Claude Code's own `/resume` picker:

- **Title:** the name you gave the session with `/rename`, else Claude's own title for it, else your last prompt, else your first. A session where you only ran commands shows the command, like `/clear` or `! code .`.
- **Left out:** subagent and team sessions, `claude -p` and SDK runs, `/loop` sessions, sessions that were continued in a newer one, and sessions with no messages.
- **Fast:** like `/resume`, it reads only the first and last 64 KB of each session log. Claude deletes logs older than `cleanupPeriodDays` (30 days by default), so the list doesn't grow without end.

**Sessions whose directory isn't here** (deleted, or a devcontainer path) show the directory dimmed and marked "(missing)". Enter resumes them from `~`, after a second's note saying so.

**Other ways to resume:** paste a full session ID into the picker and press Enter, or run `claude-launcher --resume <id>` (or `-r <id>`) to skip the picker and resume it in its directory. Anything else after `--resume`, like a session name, is passed to `claude` unchanged once you pick a directory.

## After a restart or logout

When a restart or logging out closed Claude sessions, the picker shows which:

```
  Select a project directory for Claude                          enter open   tab complete   ctrl-s sessions   esc quit
  ↻ 3 sessions were open when your computer restarted (11:14 PM)   ctrl-s to see them
```

Press Ctrl-S and they're at the top of the Sessions view, marked ↻ (and at the top of their directory's sessions too). Enter resumes one, and it leaves the group; Ctrl-X takes it out without resuming, and Alt-X twice takes out every ↻ session the view shows. A session stays in the group for 7 days, unless you resume or dismiss it first, or start it again from anywhere else.

**How it knows:** `claude-session-hook` keeps a small record for each interactive Claude. An exit you chose (`/exit`, `/clear`, switching with `/resume`) deletes it; closing the tab, or the desktop closing every window, keeps it with the time the session ended. When the picker opens, it checks those times against the system's own record of restarts and logouts. A session that ended during one, or just before or after it, was closed by it; anything else, like a tab you closed yourself, isn't listed. On Linux that record is the systemd journal ([docs/linux.md](docs/linux.md#how-it-knows-what-a-restart-or-logout-closed) has the details, and what it needs); on Windows and in WSL, Windows' System event log ([docs/windows.md](docs/windows.md#how-it-knows-what-a-restart-or-logoff-closed)).

Only interactive Claude sessions in a terminal are recorded: not `claude -p`, SDK runs or an editor's Claude panel.

## Development

See [CLAUDE.md](CLAUDE.md). In short: `uv run pytest` runs the tests, including end-to-end ones that drive the real launcher and fzf in a pseudo-terminal. [docs/dev/platforms.md](docs/dev/platforms.md) is how the code is split by OS, and how to add one.

---

claude-launcher is an independent project. It isn't affiliated with or endorsed by Anthropic; Claude and Claude Code are Anthropic's trademarks.
