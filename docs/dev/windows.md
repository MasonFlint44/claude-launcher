# Windows

The native Windows platform, `src/claude_launcher/platforms/windows/`, and what was learned building it. (WSL is Linux, with Windows' restarts.)

## Layout

| Path | Role |
|---|---|
| `platforms/windows/__init__.py` | Windows' `Platform`: fzf picks `cmd` itself (`SHELL=cmd`), the core's command line quoted for it (`cmd_quote`), fzf on a localhost port, files in `%LOCALAPPDATA%\claude-launcher`. |
| `platforms/windows/host.py` | The boot counter (registry `BootId`), processes (`NtQueryInformationProcess` for command lines), `msvcrt` locks, the console (`CONOUT$`, `CONIN$` with VT input). Standard library only. |

## Rules

- **fzf and cmd.** With `--with-shell`, fzf treats the shell as unknown and quotes `{q}` POSIX-style ('a'), and it doesn't wrap the command for `cmd /s`. Without it, fzf takes `$SHELL` (OpenSSH and Git Bash set it to PowerShell or bash), else `cmd`; for `cmd` it runs `cmd /s/c "COMMAND"` and quotes placeholders the way programs split their command lines, with `^` before `cmd`'s special characters. So the launcher runs fzf with `SHELL=cmd` and no `--with-shell`, and `cmd_quote` quotes the core's arguments the same way. The program itself is in plain quotes: `cmd` finds it by them, and `^"` isn't one.
- **No Unix sockets in Windows' Python**, so fzf listens on `localhost:0` and gives its commands the port in `FZF_PORT`. Every request carries the run's `FZF_API_KEY`.
- **No fork**: a first press's second half runs as a detached core process (`ui restore-header`).
- **Text is UTF-8 with `\n`, everywhere.** Windows' Python writes the code page (cp1252, no ↻) and `\r\n` by default, to files and to pipes; fzf would show the `\r`, and `subprocess` with `text=True` hung on a ↻ in fzf's input. Give every text read and write `encoding="utf-8"` (and `newline=""` for writes); the core and launcher reconfigure stdout and stderr (`text.utf8_out`).
- **fzf leaves the console's last column unused** (writing it wraps the line), so the picker lays out one column narrower (`columns_lost`).
- **`HOME` isn't home**: Git Bash sets it to `/c/Users/...`. Home is `USERPROFILE`.
- **Paths**: see `pathtext.PathText("nt")`. Claude names a log directory after the path with every non-alphanumeric character as `-` (`C:\Users\me` is `C--Users-me`), as on Linux.

## Testing in a VM

The suite runs on Windows (`python -m pytest --basetemp=C:\t`; a short temp directory keeps deep test paths under 260 characters). Tests that drive a pseudo-terminal (`test_e2e.py`, `test_theme.py`) are POSIX-only; on Windows the launcher is driven over SSH, whose sessions are real consoles (ConPTY), and its screen rendered with pyte.
