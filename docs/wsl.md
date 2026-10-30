# claude-launcher in WSL

Claude Code inside a WSL distro, opened from Windows Terminal. For the picker itself, see the [README](../README.md); for Claude Code on Windows itself, [docs/windows.md](windows.md).

## Install

Inside your distro (WSL 2; tested with Ubuntu 26.04), with Python 3.10+ and [Claude Code](https://docs.claude.com/en/docs/claude-code) installed there:

```bash
git clone https://github.com/MasonFlint44/claude-launcher.git ~/git/claude-launcher
~/git/claude-launcher/install.sh
```

The installer does what it does on Linux (fzf, `~/.local/bin` links, the session hook in `~/.claude/settings.json`), and in place of a desktop launcher adds a **Claude Code (*distro*)** profile to Windows Terminal, which opens the launcher in this distro. It sits beside the Windows profile if you have both. Restart Windows Terminal to see it. It needs WSL's interop (on by default), to reach Windows Terminal's settings and Windows' event log.

Updating and uninstalling are as on Linux: `git pull`, and `install.sh --uninstall`.

## How it knows what closed a session

As on Linux, the session hook keeps a record for each interactive Claude. When the picker opens it checks them against:

- **Windows' restarts, shutdowns and logoffs**, from Windows' System event log (through interop), as on [Windows](windows.md#how-it-knows-what-a-restart-or-logoff-closed).
- **WSL stopping on its own**, as with `wsl --shutdown` or `wsl --update`. Claude was still running then, so its session is listed, as "open when WSL stopped". A tab you closed before that isn't. (WSL also stops when it's idle, but only once nothing is running in it.)
