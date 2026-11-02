# Changelog

What changed in each release of claude-launcher. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). To update to the latest release, see Updating in [Linux](docs/linux.md#updating), [Windows](docs/windows.md#updating) or [WSL](docs/wsl.md#updating).

## [0.1.0] - 2026-11-02

The first release. It runs on Linux with systemd, Windows 10 and 11, and WSL 2, with Python 3.10 or newer.

### Added

- **The picker.** `claude-launcher` lists the directories you've used Claude in, most recent first. Enter shows a directory's sessions, to resume one or start a new one; Ctrl-S shows your sessions everywhere; typing a path (or Tab) opens any directory. When Claude exits, you're back at the list.
- **Resuming by ID.** `claude-launcher --resume <id>` finds the directory a session started in and resumes it there. Other options go to every Claude it starts; `--help` and `--version` show how to run it and its version.
- **Sessions a restart or logout closed** come back at the top, marked ↻, for a week: from the systemd journal on Linux, Windows' System event log on Windows, and in WSL both of those plus WSL stopping. Ctrl-X dismisses one, Alt-X twice every one shown.
- **Claude Dark.** The terminal tab switches to it while the launcher runs, Claude included, and back to your own colors when it exits. There's a Ghostty theme too.
- **A Claude Code app.** On Linux, a desktop entry and profile for Ghostty, kitty, WezTerm, Ptyxis, GNOME Terminal, Konsole, Guake, or the desktop's default terminal. On Windows and in WSL, a Windows Terminal profile.
- **Installers.** `install.sh` on Linux and in WSL and `install.ps1` on Windows, with `--terminal`, `--icon` and `--uninstall`. They fetch a checked fzf when the system's is older than 0.67.

[0.1.0]: https://github.com/MasonFlint44/claude-launcher/releases/tag/v0.1.0
