# claude-launcher

A project and session picker for Claude Code, plus a Claude Dark theme and a desktop launcher. README.md is the user-facing description; this file is how the code is put together and the rules for changing it. The code is split into what's the same everywhere and what differs by OS (`src/claude_launcher/platforms/`); Linux and Windows so far. The files imported at the end cover the split and each platform's own parts.

## Layout

| Path | Role |
|---|---|
| `bin/claude-launcher` | Python. Runs `launcher.main()`; nothing else. |
| `libexec/claude-launcher-core` | Entry script for the Python package. fzf's key bindings and install.sh call it; it isn't on PATH. |
| `src/claude_launcher/launcher.py` | The loop: sets the tab's colors and title, runs fzf with the options the picker gives it, starts `claude`, comes back. Signals: see its docstring. |
| `src/claude_launcher/cli.py` | Subcommands for the entry script. Arguments are parsed by hand, because they include fzf's query and claude's own options. |
| `src/claude_launcher/picker.py` | The picker: rows, headers, what each key does, and what Enter chose. Its state lives in a per-run directory (see the module docstring). |
| `src/claude_launcher/pathtext.py` | Paths as text: separators, `~`, what's inside what, path mode's splitting, by POSIX or Windows rules. The only place the picker knows what a separator is. |
| `src/claude_launcher/logs.py` | Reading Claude's session logs: the sessions list, titles, directories. |
| `src/claude_launcher/platforms/` | What differs by OS, behind the interfaces in `base.py`; `current()` picks the platform. See docs/dev/platforms.md, and docs/dev/linux.md for `linux/`. |
| `src/claude_launcher/restart.py` | The group of sessions a restart or logout closed: the rule, and the record format. The same on every platform. |
| `src/claude_launcher/theme.py` | Reads `themes/claude-dark`; switches a terminal's colors and puts them back. |
| `src/claude_launcher/title.py` | The tab title: "Claude Code" while the picker shows, and the old title put back on exit. |
| `src/claude_launcher/fzf.py`, `share/fzf/` | Which fzf to run, and fetching the pinned one when the system's is too old (checked against the release's checksums file). |
| `src/claude_launcher/settings.py` | For install.sh: adding the hook to Claude's settings.json, and taking it out. |
| `share/icons/`, `scripts/render-icons.sh` | The app icon. Edit the SVG, then run the script to re-render the PNGs. |
| `tests/` | pytest. `term.py` is a pseudo-terminal driver (pyte) for the end-to-end and hook tests. `contract/` checks any platform's parts; `linux/` is Linux's own tests. |

## Commands

```bash
uv run pytest                    # everything (about 20 s)
uv run pytest -m "not e2e"       # skip the pseudo-terminal tests
uvx ruff check src tests bin/claude-launcher bin/claude-session-hook.py libexec/claude-launcher-core
uvx ruff format src tests bin/claude-launcher bin/claude-session-hook.py libexec/claude-launcher-core
shellcheck bin/claude-session-hook install.sh tests/docker/run.sh tests/docker/*/*.sh
```

Run these before calling a change done, and the Docker layer a change touches (docs/dev/linux.md). CI runs everything. Tests need fzf 0.67+ on PATH. The Ghostty theme check is skipped if Ghostty isn't installed.

## Rules

- **The runtime is the standard library.** The launcher runs on the system `python3` (3.10+) with no installed packages. pytest and pyte are dev-only (`[dependency-groups] dev`).
- **Each key press runs the entry script once** (about 30 ms). Keep imports light and don't add work on the typing path: `on_change` reloads the list only while the query is a path. In path mode a key runs it twice (about 45 ms), quicker than anyone types, so there's no debounce. fzf takes a paste (terminals send it in bracketed-paste markers) as one edit: one run, not one per character. In tests, `Term.type` types at a fast typist's pace and `Term.paste` pastes; writing a whole string at once would be typing thousands of keys a second.
- **Path mode** (query starts with `/` or `~`, or on Windows a drive like `C:` or a share like `\\server`; every separator the picker relies on is in `pathtext.py`, and both sets of rules are tested on any OS): the title is "Directories in <typed>" and the rows are that directory, then its direct children (`Picker.directories`), one `scandir` per key press and no cap (14 ms for `/usr/bin`). Rows are `key<TAB>shown<TAB>note` and fzf matches only `shown` (`--with-nth=2.. --nth=1`, `--tabstop=1`), so notes never match a query. Sessions rows are `key<TAB>when<TAB>directory title`, matched on the last field (`NTH`, switched per view with `change-nth`), so ages and ↻ never match either. Say "directory", never "folder", in anything shown. Keep `MATCHING` and `NTH` the same in the picker and `_nth_match`. Tab and Esc only `change-query`; fzf's `change` event then reloads. Tab can reach fzf while it's still loading the rows (`{1}` is empty then), so Tab, like Enter, takes the row at `FZF_POS` from what the query matches.
- **Keys and views.** Key bindings run the core with the platform's shell and command line, saved in the picker's state at start so key presses never load the platform. Enter on a directory shows its sessions (`Picker.on_enter`); one with none opens straight away. Tab completes, from the plain list too. Ctrl-S toggles all sessions. Esc goes back. There's no Shift-Tab. A directory's view holds every session that ran in it or anywhere inside it (`is_inside`, from each log's working directory, since log directory names can't be turned back into paths), with ↻ first and then newest first: one order, no depth tiers, and no exception for `~` or `/`. Its first row is "+ New session" (key `NEW_SESSION` + the directory). Going back to the list restores the search and cursor (`State.back`). fzf moves the cursor to the top once a search's rows come in, so the cursor waits for the `result` event, which is bound only while one is pending: no extra run per key. Keys can arrive before a switch's rows do, but the picker's state is always current, so trust `state.view` over the row fzf reports (`on_enter`, `on_result`, `pick`).
- **fzf floor is 0.67** (`fzf.MIN_VERSION`): the first release whose `--listen` takes a Unix socket (0.66 and older reject it; before 0.45 there's no `transform`). Ubuntu 24.04 (0.44), Debian 12 (0.38) and Debian 13 (0.60) ship older ones, so install.sh fetches `fzf.PINNED` (checked against the release's own checksums file, kept as it is in `share/fzf/`) into the platform's data directory (`bin/`; on Linux `~/.local/share/claude-launcher/bin`), used only by the launcher. Requests to fzf's `--listen` (a socket, or a port on Windows) carry a key made fresh each run (`FZF_API_KEY`), so nothing but the key bindings can drive fzf. `fzf.find()` prefers the system's when it's new enough. To move the pin: add the new release's checksums file to `share/fzf/`, change `PINNED`, and run the tests with that build first on PATH (the e2e tests run whichever fzf is first). Test any new fzf feature against 0.67.
- **Session-log parsing matches what Claude Code's `/resume` picker shows** (as of 2.1.310), written from how Claude behaves and the logs it writes. Never copy text from Claude Code's own code into this repo; it isn't open source. The rules: the 64 KB head/tail reads, the title order, and which sessions are left out. When Claude's log format changes, update `logs.py` and add a test with the new shape to `test_logs.py`. Don't parse whole logs; they get large.
- **The session hook's record format is a contract.** Each platform's hook writes it and `restart.py` (the same everywhere) reads it; `tests/linux/test_hook.py` and `tests/test_windows_hook.py` check each hook against `restart.Record`. Change them together, with tests in `test_restart.py`.
- **`settings.json` may be a symlink** (often, into a dotfiles repo). Write through it to the real file, never replace the link. `settings.py` does; keep it that way.
- **`themes/claude-dark` is the one source of the colors.** The desktop launcher loads it, `theme.py` turns it into escapes, and the picker takes its accent and dim colors from it. Don't copy hex values into code.
- **This repo stays independent.** It doesn't know about, or integrate with, other session tools. It ships no Anthropic artwork (logos, Clawd): the default icon is original, and people who want something else use `install.sh --icon`.
- **Keep the user's terminal intact.** Colors: ask first (queries, then a device-attributes request as the end marker, never a guessed timeout), change only what the terminal reported, and on exit reset, ask again, and set back anything still off (Konsole has no reset for text and background colors). Write colors as `#rrggbb`; Konsole can't read `rgb:`. Don't detect terminals by environment variables: they're inherited (a terminal started from Ghostty has `TERM_PROGRAM=ghostty`). The one exception is GNU screen (`STY`), skipped on purpose: it forwards only the background, to the outer terminal for every window, with no reset. tmux is fine: per-pane colors, tested live with 3.6. Ctrl-C belongs to fzf and Claude: the launcher ignores it while either runs, with a handler rather than `SIG_IGN` (an ignored signal stays ignored in the child). A hang-up or TERM is acted on once fzf or Claude exits, as a shell does, so Claude is never left running on its own; after a hang-up nothing is put back, since the terminal is gone.

## Writing style

User-facing text (README, picker messages, comments) is plain and short: say what happens, from the user's side. "Not a directory: ~/x", not "Error: invalid path". In comments, explain why when it isn't obvious; don't narrate the code.

@docs/dev/platforms.md
@docs/dev/linux.md
@docs/dev/windows.md
