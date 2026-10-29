# Windows

The native Windows platform, `src/claude_launcher/platforms/windows/`, and what was learned building it. (WSL is Linux, with Windows' restarts.)

## Layout

| Path | Role |
|---|---|
| `bin/claude-session-hook.py` | The session hook: the bash hook's record, in Python. settings.json runs it as a program and arguments (`"command": python.exe, "args": ["-I", hook, "start"]`), with no shell: 30 ms, where Git Bash takes about as long and PowerShell 530 ms, and Git is optional for Claude on Windows. Claude gives it its pid (`CLAUDE_PID`); the boot ID is the counter as 32 hex digits. |
| `platforms/windows/__init__.py` | Windows' `Platform`: fzf picks `cmd` itself (`SHELL=cmd`), the core's command line quoted for it (`cmd_quote`), fzf on a localhost port, files in `%LOCALAPPDATA%\claude-launcher`. |
| `platforms/windows/eventlog.py` | Restarts and logoffs from the System log through `wevtutil.exe` (any user can read it; the Security log needs an administrator), shared with WSL. |
| `platforms/windows/host.py` | The boot counter (registry `BootId`), processes (`NtQueryInformationProcess` for command lines), `msvcrt` locks, the console (`CONOUT$`, `CONIN$` with VT input). Standard library only. |

## Rules

- **fzf and cmd.** With `--with-shell`, fzf treats the shell as unknown and quotes `{q}` POSIX-style ('a'), and it doesn't wrap the command for `cmd /s`. Without it, fzf takes `$SHELL` (OpenSSH and Git Bash set it to PowerShell or bash), else `cmd`; for `cmd` it runs `cmd /s/c "COMMAND"` and quotes placeholders the way programs split their command lines, with `^` before `cmd`'s special characters. So the launcher runs fzf with `SHELL=cmd` and no `--with-shell`, and `cmd_quote` quotes the core's arguments the same way. The program itself is in plain quotes: `cmd` finds it by them, and `^"` isn't one.
- **No Unix sockets in Windows' Python**, so fzf listens on `localhost:0` and gives its commands the port in `FZF_PORT`. Every request carries the run's `FZF_API_KEY`.
- **No fork**: a first press's second half runs as a detached core process (`ui restore-header`).
- **Text is UTF-8 with `\n`, everywhere.** Windows' Python writes the code page (cp1252, no ↻) and `\r\n` by default, to files and to pipes; fzf would show the `\r`, and `subprocess` with `text=True` hung on a ↻ in fzf's input. Give every text read and write `encoding="utf-8"` (and `newline=""` for writes); the core and launcher reconfigure stdout and stderr (`text.utf8_out`).
- **fzf leaves the console's last column unused** (writing it wraps the line), so the picker lays out one column narrower (`columns_lost`).
- **`HOME` isn't home**: Git Bash sets it to `/c/Users/...`. Home is `USERPROFILE`.
- **Paths**: see `pathtext.PathText("nt")`. Claude names a log directory after the path with every non-alphanumeric character as `-` (`C:\Users\me` is `C--Users-me`), as on Linux.
- **Restart and logoff detection** (`eventlog.py`). Restart or power off: the boot's last User32 1074 ("initiated the restart") to Kernel-General 13 ("shutting down"); without one (a crash, a power cut), the boot's last logged event. Logoff: Winlogon 7002 with this user's SID (`whoami.exe /user`); Windows logs one at a restart too, and the restart wins. A boot runs from one Kernel-General 12 to the next; filter by provider as well as ID, since Wininit logs 12 and 13 too. Sort by time, not by the log's order: a clock that was set wrong and then right leaves "later" events earlier in the log. Measured in the VM: 1074 → 7002 (+0.8 s) → 13 (+4 s) → 12 at the next boot. Windows Update restarts on its own (TrustedInstaller, "Upgrade (Planned)"), with the same events. The boot counter (`BootId`) goes up on every boot; whether a Fast Startup boot raises it is unchecked (the VM can't hibernate), but its shutdown logs this user's 7002 either way.

- **What Claude gives a hook on Windows** (2.1.312): the same stdin as on Linux (`/exit` is `prompt_input_exit`, closing the terminal `other`), `CLAUDE_CODE_ENTRYPOINT=cli` (`sdk-cli` for `claude -p`), `CLAUDE_PID`. A shell-form hook runs as `bash -c "bash COMMAND"` under Git Bash, or PowerShell without it. `bash` on PATH is `System32\bash.exe`, WSL's.

## Testing in a VM

The suite runs on Windows (`python -m pytest --basetemp=C:\t`; a short temp directory keeps deep test paths under 260 characters). Tests that drive a pseudo-terminal (`test_e2e.py`, `test_theme.py`) are POSIX-only; on Windows the launcher is driven over SSH, whose sessions are real consoles (ConPTY), and its screen rendered with pyte.
