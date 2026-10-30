# claude-launcher on Windows

How to install claude-launcher on Windows, what the installer sets up, and how the picker tells which sessions a restart or logoff closed. For the picker itself, see the [README](../README.md). For Claude Code in WSL, see [docs/wsl.md](wsl.md).

## Install

You need Windows 10 or 11, [Claude Code](https://docs.claude.com/en/docs/claude-code) (its installer puts `claude.exe` in `%USERPROFILE%\.local\bin`; npm's `claude.cmd` works too), Git to clone the repo, and Python 3.10 or newer from python.org, the Microsoft Store or anywhere else. If there's no Python, the installer offers to install Python 3.13 for you with winget.

In PowerShell:

```powershell
git clone https://github.com/MasonFlint44/claude-launcher.git $HOME\git\claude-launcher
powershell -ExecutionPolicy Bypass -File $HOME\git\claude-launcher\install.ps1
```

(`-ExecutionPolicy Bypass` lets this one script run; Windows doesn't run downloaded PowerShell scripts otherwise.)

The installer:

1. Checks for fzf 0.67 or newer, and if there isn't one, downloads fzf 0.74.5 from fzf's GitHub releases, checks it against the release's checksum, and keeps it in `%LOCALAPPDATA%\claude-launcher\bin` for the launcher alone.
2. Adds the session hook to Claude Code's user settings (`%USERPROFILE%\.claude\settings.json`, or `%CLAUDE_CONFIG_DIR%\settings.json`), as a SessionStart and SessionEnd hook. Your other settings and hooks are left as they are.
3. Writes `claude-launcher.cmd` to `%USERPROFILE%\.local\bin`, so `claude-launcher` runs in any terminal, and the small files it and the hook run to `%LOCALAPPDATA%\claude-launcher`.
4. Adds a **Claude Code** profile to Windows Terminal, in Claude Dark, with the app icon. It's a [fragment](https://learn.microsoft.com/windows/terminal/json-fragment-extensions), so your Windows Terminal settings file isn't touched; restart Windows Terminal to see it.

Re-running it is safe; it updates what's already there.

| Option | Does |
|---|---|
| `-Icon FILE.png` | Your own icon for the profile. A copy is kept, so the original can move. `-Icon default` goes back to the one in `share\icons`. |
| `-Uninstall` | Takes everything out again. |
| `-Help` | Shows these. |

## The Claude Code profile

Open it from Windows Terminal's new-tab menu. Quitting the picker closes the tab; if the launcher fails, its message stays up. Your own changes to the profile (font, size and so on) are kept in Windows Terminal's settings and survive re-installing.

In any other terminal tab, `claude-launcher` switches that tab to Claude Dark while it runs and back when it exits, as on Linux. The old console window (conhost, Windows 10's default) runs the picker too, but keeps its own colors, since it doesn't say what they are, and its default font has no ↻.

## How it knows what a restart or logoff closed

`claude-session-hook.py` keeps a small record for each interactive Claude in `%USERPROFILE%\.claude\state\claude-launcher\open\`. An exit you chose (`/exit`, `/clear`, switching with `/resume`) deletes it; closing the tab, or Windows closing every window, keeps it with the time the session ended. When the picker opens, it checks those times against Windows' System event log, which any user can read:

- **Restart or shutdown** (including Windows Update's own restarts): from when it was started to when Windows began shutting down. After a crash or power cut, from the last thing logged. Shutting down with Fast Startup on (Windows' default) hibernates instead of shutting down; it closes Claude all the same, and counts as a shutdown.
- **Logoff**: when you signed out.

A session that ended in that span, or up to 30 seconds before or 10 seconds after it, was closed by it. So was one that never recorded an end. Anything else, like a tab you closed yourself, isn't listed.

## Updating

```powershell
git -C $HOME\git\claude-launcher pull
```

Changes apply right away. If the profile changed (`src\claude_launcher\platforms\windows\`), re-run `install.ps1` and restart Windows Terminal.

The profile, `claude-launcher.cmd` and the session hook find Python each time they run (the first `python` on your PATH, else the `py` launcher, else the Microsoft Store's; it has to be 3.10 or newer), so upgrading or replacing Python needs nothing from you.

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File $HOME\git\claude-launcher\install.ps1 -Uninstall
```

This removes `claude-launcher.cmd`, the profile and the session hook. It leaves the launcher's state in `%USERPROFILE%\.claude\state\claude-launcher` and says so.
