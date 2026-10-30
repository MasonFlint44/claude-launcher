# Platforms

How the code is split by OS, and how to add one. Imported into CLAUDE.md.

## What's shared, and what isn't

Most of the launcher is the same everywhere: the picker (`picker.py`), the session logs (`logs.py`), the loop (`launcher.py`), the colors and title (`theme.py`, `title.py`), the restart rule and its record format (`restart.py`), the installer's steps (`install.py`), the settings.json hook merge (`settings.py`) and fzf (`fzf.py`). What differs by OS is in `src/claude_launcher/platforms/<name>/`, behind the interfaces in `platforms/base.py`:

| Part | What it does | Linux's |
|---|---|---|
| `Host` | Boot ID and start, whether a process is the record's Claude and still running (`claude_running(pid, started)`), the user, a lock between processes, the terminal (opening it, and reading its answers to queries unechoed) | `/proc`, `flock`, `/dev/tty`, `termios` |
| `EventSource` | The restarts and logouts in a record's boot that could have closed its Claude. Only events: the margins and which one wins are `restart.py`'s | the systemd journal |
| `Setup` | What the installer (`install.py`, run by install.sh or install.ps1) checks first (`check`), sets up after fzf and the hook, and undoes; what settings.json runs for the hook; any core commands of the platform's own | links in `~/.local/bin`, Ghostty theme link, icon, desktop entries, terminal profiles; `terminal list`, `terminal open guake` |
| `Platform` fields | fzf's name for the OS (for the fetched build), the shell fzf runs key bindings with, the core's command line quoted for it, more environment for fzf, columns fzf leaves unused, how the picker reaches fzf while it runs (a Unix socket, or a localhost port where Python has no Unix sockets), where the launcher keeps its own files, and any reasons a session was closed beyond restart and logout (`reasons`: `restart.Reason`, with its key, its wording and its rank) | `linux`, `bash -c`, `shlex`, none, 0, socket, `~/.local/share/claude-launcher`, none |
| session hook | Writes the record `restart.Record` reads, for each interactive Claude | `bin/claude-session-hook` (bash; Windows: `bin/claude-session-hook.py`, run through a .cmd that finds Python, then an entry script) |
| path text | Separators, `~`, what's inside what, path mode's splitting | `pathtext.py`: POSIX rules, or Windows' (drives, shares, either slash, case ignored), by `os.name` |

`Host` and `Setup` are made when first used and `EventSource` only when the picker sorts the restart group, so key presses, which run the core once each, never load platform code (`tests/test_imports.py` checks). What a key press needs from the platform (the shell, the core's command line, the reasons' wording) is saved in the picker's state when it starts. Dismissing a ↻ session (Ctrl-X, Alt-X) is the exception: it takes the platform's lock.

A reason that's in a record's group but that no platform knows (from an older or newer launcher) is kept, ranked last, and shown as "Claude was stopped".

## Picking one

`platforms.current()` picks the platform once per run: `platforms/fork.py` if it exists, else Windows on `win32`, and on Linux WSL when the kernel says "microsoft" and WSL's markers are there (`wsl.is_wsl`), else Linux. On any other OS, the launcher and the installer say "claude-launcher doesn't run on <os> yet" and exit 1. This repo never creates `fork.py`.

## Adding a platform here

1. Add `platforms/<name>/` with a `platform()` function returning a `Platform`, and pick it in `platforms._detect()`.
2. Give it a session hook that writes the same record as `bin/claude-session-hook` (see the `restart.py` docstring), and a test like `test_records_are_what_the_launcher_reads` in `tests/linux/test_hook.py`.
3. Pass `tests/contract/` on it: they run against whatever platform the tests run on (`Host`, `EventSource`, `Setup` and the `Platform` fields).
4. Put the tests that only run on it in `tests/<name>/`, with a `RUNS_ON` file holding the `sys.platform` prefix they run on (`linux`, `win32`), and its user docs in `docs/<name>.md` with a pointer from the README. Tests that run anywhere (pure logic, faked OS) can go at the top level, like `tests/test_windows_*.py`.
5. Anything the picker does with paths that looks different there goes in `pathtext.py`, with cases in `tests/test_pathtext.py` (or a file of its own, like `test_pathtext_windows.py`).
6. A reason of its own that sessions get closed (like WSL's `wsl-stop`) goes in its `Platform.reasons`, not in `restart.py` or the picker.

Windows (`platforms/windows/`) is the second platform, and WSL (`platforms/wsl/`) Linux's with Windows' restarts and logoffs, Windows Terminal profiles and its own boot rule; see docs/dev/windows.md.

## Adding a platform in a fork

A fork that keeps its platform to itself (a Mac fork, say) can merge from this repo without conflicts if it only adds files:

- its platform in `platforms/<name>/`, and `platforms/fork.py` with a `platform()` function that returns it (it can choose among several);
- its hook, its own close reasons (in its `Platform`), docs, `tests/<name>/` with a `RUNS_ON` file, and CI workflow, as new files.

It shouldn't edit shared files, and it can leave the Linux ones in place: deleting them conflicts with every upstream change to them. Run `tests/contract/` on the fork's platform; when an interface here changes, those are what fail first. install.sh's help lists terminals from the platform (`terminal list`), so a fork's needs no edit. The one shared file a fork will want to change is the README, to say where it runs.
