# Platforms

How the code is split by OS, and how to add one. Imported into CLAUDE.md.

## What's shared, and what isn't

Most of the launcher is the same everywhere: the picker (`picker.py`), the session logs (`logs.py`), the loop (`launcher.py`), the colors and title (`theme.py`, `title.py`), the restart rule and its record format (`restart.py`), the settings.json hook merge (`settings.py`) and fzf (`fzf.py`). What differs by OS is in `src/claude_launcher/platforms/<name>/`, behind the interfaces in `platforms/base.py`:

| Part | What it does | Linux's |
|---|---|---|
| `Host` | Boot ID and start, whether a process is a running Claude, the user, a lock between processes, the terminal (opening it, and reading its answers to queries unechoed) | `/proc`, `flock`, `/dev/tty`, `termios` |
| `EventSource` | The restarts and logouts in a record's boot that could have closed its Claude. Only events: the margins and which one wins are `restart.py`'s | the systemd journal |
| `Setup` | What install.sh sets up after the shared steps, and undoing it; any core commands of the platform's own | Ghostty theme link, icon, desktop entries, terminal profiles; `terminal open guake` |
| `Platform` fields | fzf's name for the OS (for the fetched build), the shell fzf runs key bindings with, and the core's command line quoted for it | `linux`, `bash -c`, `shlex` |
| session hook | Writes the record `restart.Record` reads, for each interactive Claude | `bin/claude-session-hook` (bash) |
| path text | Separators, `~`, what's inside what, path mode's splitting | `pathtext.py` (POSIX for now) |

`Host` and `Setup` are made when first used and `EventSource` only when the picker sorts the restart group, so key presses, which run the core once each, never load platform code (`tests/test_imports.py` checks).

## Picking one

`platforms.current()` picks the platform once per run: `platforms/fork.py` if it exists, else by `sys.platform`. This repo never creates `fork.py`.

## Adding a platform here

1. Add `platforms/<name>/` with a `platform()` function returning a `Platform`, and pick it in `platforms._detect()`.
2. Give it a session hook that writes the same record as `bin/claude-session-hook` (see the `restart.py` docstring), and a test like `test_records_are_what_the_launcher_reads` in `tests/linux/test_hook.py`.
3. Pass `tests/contract/` on it: they run against whatever platform the tests run on.
4. Put its own tests in `tests/<name>/`, skipped elsewhere, and its user docs in `docs/<name>.md` with a pointer from the README.
5. Anything the picker does with paths that looks different there goes in `pathtext.py`, with cases in `tests/test_pathtext.py`.

Notes for Windows and WSL. On Windows, paths aren't only on `C:`: any drive letter, and UNC paths (`\\server\share`, `\\wsl.localhost\<distro>\...`). WSL is Linux with Windows' restarts: it can reuse `linux/host.py` and the bash hook, with an `EventSource` that asks Windows (the original Windows version read event 1074 with `wevtutil.exe`).

## Adding a platform in a fork

A fork that keeps its platform to itself (a Mac fork, say) can merge from this repo without conflicts if it only adds files:

- its platform in `platforms/<name>/`, and `platforms/fork.py` with a `platform()` function that returns it (it can choose among several);
- its hook, docs, tests and CI workflow as new files.

It shouldn't edit shared files, and it can leave the Linux ones in place: deleting them conflicts with every upstream change to them. Run `tests/contract/` on the fork's platform; when an interface here changes, those are what fail first.
