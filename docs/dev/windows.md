# Windows

The native Windows platform, `src/claude_launcher/platforms/windows/`, and what was learned building it. (WSL is Linux, with Windows' restarts.)

## Layout

| Path | Role |
|---|---|
| `bin/claude-session-hook.py` | The session hook: the bash hook's record, in Python. settings.json runs it as a program and arguments (`"command": python.exe, "args": ["-I", hook, "start"]`), with no shell: 30 ms, where Git Bash takes about as long and PowerShell 530 ms, and Git is optional for Claude on Windows. Claude gives it its pid (`CLAUDE_PID`); the boot ID is the counter as 32 hex digits. |
| `platforms/windows/__init__.py` | Windows' `Platform`: fzf picks `cmd` itself (`SHELL=cmd`), the core's command line quoted for it (`cmd_quote`), fzf on a localhost port, files in `%LOCALAPPDATA%\claude-launcher`. |
| `install.ps1` | Finds Python 3.10+ (skipping the Microsoft Store's `python.exe`, which only offers to install Python), offers to install it with winget if there's none, then runs `install.py` and shows its lines. ASCII only: Windows PowerShell 5.1 reads a script without a BOM in the code page. `$ErrorActionPreference` stays `Continue`: `Stop` makes a native program's stderr fatal in 5.1. |
| `platforms/windows/setup.py`, `terminal.py` | What the installer adds: `claude-launcher.cmd` in `%USERPROFILE%\.local\bin` (where Claude Code's installer puts `claude.exe`; marked as ours), and the "Claude Code" Windows Terminal profile as a fragment (`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\claude-launcher\`), with the Claude Dark scheme built from `themes/claude-dark`. Uninstalling deletes the fragment; Windows Terminal's own settings.json is never edited. The profile's GUID never changes: Windows Terminal keeps the user's changes to it under that. |
| `platforms/windows/eventlog.py` | Restarts and logoffs from the System log through `wevtutil.exe` (any user can read it; the Security log needs an administrator), shared with WSL. |
| `platforms/windows/host.py` | The boot counter (registry `BootId`), processes (`NtQueryInformationProcess` for command lines), `msvcrt` locks, the console (`CONOUT$`, `CONIN$` with VT input). Standard library only. |

## Rules

- **fzf and cmd.** With `--with-shell`, fzf treats the shell as unknown and quotes `{q}` POSIX-style ('a'), and it doesn't wrap the command for `cmd /s`. Without it, fzf takes `$SHELL` (OpenSSH and Git Bash set it to PowerShell or bash), else `cmd`; for `cmd` it runs `cmd /s/c "COMMAND"` and quotes placeholders the way programs split their command lines, with `^` before `cmd`'s special characters. So the launcher runs fzf with `SHELL=cmd` and no `--with-shell`, and `cmd_quote` quotes the core's arguments the same way. The program itself is in plain quotes: `cmd` finds it by them, and `^"` isn't one.
- **No Unix sockets in Windows' Python**, so fzf listens on `localhost:0` and gives its commands the port in `FZF_PORT`. Every request carries the run's `FZF_API_KEY`.
- **No fork**: a first press's second half runs as a detached core process (`ui restore-header`).
- **Text is UTF-8 with `\n`, everywhere.** Windows' Python writes the code page (cp1252, no ↻) and `\r\n` by default, to files and to pipes; fzf would show the `\r`, and `subprocess` with `text=True` hung on a ↻ in fzf's input. Give every text read and write `encoding="utf-8"` (and `newline=""` for writes); the core and launcher reconfigure stdout and stderr (`text.utf8_out`).
- **fzf leaves the console's last column unused** (writing it wraps the line), so the picker lays out one column narrower (`columns_lost`).
- **`TERM` is unset in Windows' consoles**, which take escapes anyway, so only `TERM=dumb` turns off colors and the title there (`title.dumb`). Windows Terminal (1.18 and later at least) answers the color queries and device attributes; the picker asks and switches as anywhere else.
- **conhost** (the old console window) runs the picker, the title and Ctrl-C as Windows Terminal does, but doesn't answer the color queries, so its colors are left alone; Consolas has no ↻.
- **Ctrl-C through `claude-launcher.cmd`**: fzf and Claude read the console raw, so Ctrl-C is a key to them and `cmd` never asks "Terminate batch job (Y/N)?" (checked: quitting the picker, and quitting Claude with two presses).
- **winget** installs Python from a desktop session only; over SSH it fails. `install.ps1`'s offer was checked with Python hidden: it installs Python 3.13 for the user and carries on. The hook, the profile and the `.cmd` name that `python.exe`, so a profile that's still pointed at a Python that's gone fails with Windows Terminal's "cannot find the file specified".
- **`HOME` isn't home**: Git Bash sets it to `/c/Users/...`. Home is `USERPROFILE`.
- **Paths**: see `pathtext.PathText("nt")`. Claude names a log directory after the path with every non-alphanumeric character as `-` (`C:\Users\me` is `C--Users-me`), as on Linux.
- **Restart and logoff detection** (`eventlog.py`). Restart or power off: the boot's last User32 1074 ("initiated the restart") to Kernel-General 13 ("shutting down"); without one (a crash, a power cut), the boot's last logged event. Logoff: Winlogon 7002 with this user's SID (`whoami.exe /user`); Windows logs one at a restart too, and the restart wins. A boot runs from one Kernel-General 12 to the next; filter by provider as well as ID, since Wininit logs 12 and 13 too. Sort by time, not by the log's order: a clock that was set wrong and then right leaves "later" events earlier in the log. Measured in the VM: 1074 → 7002 (+0.8 s) → 13 (+4 s) → 12 at the next boot. Windows Update restarts on its own (TrustedInstaller, "Upgrade (Planned)"), with the same events. The boot counter (`BootId`) goes up on every full boot. A Fast Startup shutdown (measured in the VM) logs 1074 ("power off", by Explorer) and this user's 7002, then hibernates: Kernel-Power 42, and 107 at power on, with no Kernel-General 13 or 12, Kernel-Boot 27 "boot type 0x1", and `BootId` unchanged. So the record's boot is still the current one. `_fast_startups` finds it as a 1074 followed by a 42 within `_FAST_STARTUP_GAP`, with no 13 between, and it counts as a restart from the 1074 to the 42 (hibernating on its own logs no 1074); its 7002 is a logoff, which the restart wins over. `shutdown /s /hybrid` from an SSH session shuts down fully instead; and with Credential Guard on (the VM's default), hibernating in the VM fails and reboots (Kernel-Power 41), which the picker treats as a crash.

- **What Claude gives a hook on Windows** (2.1.312): the same stdin as on Linux (`/exit` is `prompt_input_exit`, closing the terminal `other`), `CLAUDE_CODE_ENTRYPOINT=cli` (`sdk-cli` for `claude -p`), `CLAUDE_PID`. A shell-form hook runs as `bash -c "bash COMMAND"` under Git Bash, or PowerShell without it. `bash` on PATH is `System32\bash.exe`, WSL's.

## WSL

`platforms/wsl/` is Linux (its host, the bash hook, the `~/.local/bin` links) with two differences:

- **Restarts and logoffs** (`events.py`): Windows' own, from `eventlog.py` through interop, since a WSL record's boot is WSL's and ends with Windows'. WSL stopping by itself (`wsl --shutdown`, `wsl --update`) is its own reason, `wsl-stop` ("open when WSL stopped"), at the current WSL boot's start. It kills Claude, so the record has no end, and a record without one is closed by the first event after it started; a tab closed earlier has its own end and isn't counted. A Windows restart or logoff stops WSL too, and wins over it (it comes first, and `restart.REASONS` ranks it higher). Without interop, only that last one is known.
- **Setup** (`setup.py`): a "Claude Code (<distro>)" Windows Terminal profile, written through `/mnt/c` (`%LOCALAPPDATA%` from `cmd.exe`, paths through `wslpath`), with its own GUID per distro, so it sits beside native Windows' profile. It runs `wsl.exe -d <distro> --cd ~ -- bash -lc "~/.local/bin/claude-launcher || exec bash"`.

Checked in the VM (WSL 3.0.3, Ubuntu 26.04 with systemd): install.sh, the profile in Windows Terminal, a Claude session, `wsl --shutdown`, and the session offered back.

## Testing in a VM

The suite runs on Windows (`python -m pytest --basetemp=C:\t`; a short temp directory keeps deep test paths under 260 characters), in CI on `windows-latest` with Python 3.10, and in WSL, where a Linux desktop's own tests (`desktop_only`, such as install.sh's, which would write to the real Windows Terminal) are skipped. Tests that drive a pseudo-terminal (`test_e2e.py`, `test_theme.py`) are POSIX-only; on Windows the launcher is driven over SSH, whose sessions are real consoles (ConPTY), and its screen rendered with pyte.

A Windows 11 VM (QEMU, no root needed) was used for all of the above: an unattended install, SSH for commands, QMP for screenshots and keys on its desktop (Windows Terminal), and WSL inside it. Its clock must be local time (`-rtc base=localtime`), or restarts log events hours apart.
