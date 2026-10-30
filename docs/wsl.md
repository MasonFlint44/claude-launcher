# claude-launcher in WSL

Claude Code inside a WSL distro, opened from Windows Terminal. For the picker itself, see the [README](../README.md); for Claude Code on Windows itself, [docs/windows.md](windows.md).

## Install

Inside your distro (WSL 2; tested with Ubuntu 26.04), with Python 3.10+ and [Claude Code](https://docs.claude.com/en/docs/claude-code) installed there:

```bash
git clone https://github.com/MasonFlint44/claude-launcher.git ~/git/claude-launcher
~/git/claude-launcher/install.sh
```

The installer does what it does on Linux (fzf, the session hook in `~/.claude/settings.json`, `~/.local/bin` links), and in place of a desktop launcher adds a **Claude Code (*distro*)** profile to Windows Terminal, which opens the launcher in this distro as you. It sits beside the Windows profile if you have both, and beside the profiles of other distros you install it in. Restart Windows Terminal to see it.

It uses WSL's interop (on by default) to reach Windows Terminal's settings and Windows' event log; it finds Windows' programs even if `appendWindowsPath` is off. With interop off, it skips the profile and says so, and `claude-launcher` still works in any terminal. Run it as yourself, not with `sudo`: the profile opens as the user who installed it.

| Option | Does |
|---|---|
| `--icon FILE.png` | Your own icon for the profile (Windows Terminal wants a PNG). A copy is kept, so the original can move, and it's used again when you re-run the installer. `--icon default` goes back to the one in `share/icons`. |
| `--uninstall` | Takes everything out again, including this distro's profile. |

## Updating

```bash
git -C ~/git/claude-launcher pull
```

Changes apply right away. If the profile changed (`src/claude_launcher/platforms/wsl/`, or `platforms/windows/terminal.py`, which builds it), re-run `install.sh` and restart Windows Terminal.

## How it knows what closed a session

As on Linux, the session hook keeps a record for each interactive Claude. When the picker opens it checks them against:

- **Windows' restarts, shutdowns and logoffs**, from Windows' System event log (through interop), as on [Windows](windows.md#how-it-knows-what-a-restart-or-logoff-closed).
- **WSL, or this distro, stopping on its own**, as with `wsl --shutdown`, `wsl --terminate <distro>` or `wsl --update`. Claude was still running then, so its session is listed, as "open when WSL stopped". A tab you closed before that isn't. (WSL also stops when it's idle, but only once nothing is running in it.) If Windows restarted or you logged out, that's what it says instead: it stops WSL too.
