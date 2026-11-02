# claude-launcher on Windows

How to install claude-launcher on Windows, what the installer sets up, and how the picker tells which sessions a restart or logoff closed. For the picker itself, see the [README](../README.md). For Claude Code in WSL, see [docs/wsl.md](wsl.md).

## Install

You need Windows 10 or 11, [Claude Code](https://docs.claude.com/en/docs/claude-code) (its installer puts `claude.exe` in `%USERPROFILE%\.local\bin`; npm's `claude.cmd` works too), Git to clone the repo, and Python 3.10 or newer from python.org, the Microsoft Store or anywhere else. If there's no Python, the installer offers to install Python 3.13 for you with winget (from a desktop session; winget can't install it over SSH).

In PowerShell:

```powershell
git clone https://github.com/MasonFlint44/claude-launcher.git $HOME\git\claude-launcher
cd $HOME\git\claude-launcher
git checkout -q (git describe --tags --abbrev=0 origin/main)  # the latest release
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

(`-ExecutionPolicy Bypass` lets this one script run; Windows doesn't run downloaded PowerShell scripts otherwise.)

The installer:

1. Checks for fzf 0.67 or newer, and if yours is older than that, or missing, downloads fzf 0.74.5 from fzf's GitHub releases, checks it against the release's checksum, and keeps it in `%LOCALAPPDATA%\claude-launcher\bin` for the launcher alone.
2. Adds the session hook to Claude Code's user settings (`%USERPROFILE%\.claude\settings.json`, or `%CLAUDE_CONFIG_DIR%\settings.json`), as a SessionStart and SessionEnd hook. Your other settings and hooks are left as they are.
3. Writes `claude-launcher.cmd` to `%USERPROFILE%\.local\bin`, and the small files it and the hook run to `%LOCALAPPDATA%\claude-launcher`. `claude-launcher` then runs in any terminal if `%USERPROFILE%\.local\bin` is on your PATH. Claude Code's own installer puts it there; if you installed Claude Code with npm only, it may not be, and the installer warns you.
4. Adds a **Claude Code** profile to Windows Terminal, in Claude Dark, with the app icon. It's a [fragment](https://learn.microsoft.com/windows/terminal/json-fragment-extensions), so your Windows Terminal settings file isn't touched; restart Windows Terminal to see it.

Re-running it is safe; it updates what's already there.

| Option | Does |
|---|---|
| `-Icon FILE.png` | Your own icon for the profile. A copy is kept, so the original can move, and re-running the installer keeps it. `-Icon default` goes back to the one in `share\icons`. |
| `-Uninstall` | Takes everything out again. |
| `-Help` | Shows these. |

## The Claude Code profile

Open it from Windows Terminal's new-tab menu. Quitting the picker closes the tab; if the launcher fails, its message stays up. Your own changes to the profile (font, size and so on) are kept in Windows Terminal's settings and survive re-installing.

In any other terminal tab, `claude-launcher` switches that tab to Claude Dark while it runs and back when it exits, as on Linux. The old console window (conhost, Windows 10's default) runs the picker too, but keeps its own colors, since it doesn't say what they are, and its default font has no ↻.

## How it knows what a restart or logoff closed

`claude-session-hook.py` keeps a small record for each interactive Claude in `%USERPROFILE%\.claude\state\claude-launcher\open\`. An exit you chose (`/exit`, `/clear`, switching with `/resume`) deletes it; closing the tab, or Windows closing every window, keeps it with the time the session ended. When the picker opens, it checks those times against Windows' System event log, which any user can read:

- **Restart or shutdown** (including Windows Update's own restarts): from when it was started to when Windows began shutting down. After a crash or power cut, from the last thing logged. Shutting down with Fast Startup on (Windows' default) hibernates instead of shutting down; it closes Claude all the same, and counts as a shutdown.
- **Logoff**: when you signed out.

A session that ended in that span, or up to 30 seconds before or 10 seconds after it, was closed by it. So was one that never recorded an end, if a restart or logoff came after it started (one that crashed isn't). Anything else, like a tab you closed yourself, isn't listed.

Once the System log is full it drops its oldest events (it usually holds weeks of them), and clearing it drops them all. For a session that ended before the log's oldest event, the last session to end in that boot stands in for its restart: that one and any that ended up to 30 seconds before it are listed. If none was open at the restart, the last tab you closed yourself is listed, and Ctrl-X dismisses it. Shutting down with Fast Startup doesn't start a new boot, so a session the log no longer has an answer for waits until Windows next restarts fully (Restart, or an update's restart), and is worked out then the same way. One still waiting a week after it ended is dropped.

## Updating

```powershell
cd $HOME\git\claude-launcher
git fetch -q --tags
git checkout -q (git describe --tags --abbrev=0 origin/main)
```

That moves to the latest release ([CHANGELOG.md](../CHANGELOG.md) says what changed). Changes apply right away. If the profile changed (`src\claude_launcher\platforms\windows\`), re-run `install.ps1` and restart Windows Terminal. If you move the repo, run `install.ps1` again from its new place: the installed files name it.

The profile, `claude-launcher.cmd` and the session hook find Python each time they run (the first `python` on your PATH, else the `py` launcher, else the Microsoft Store's; it has to be 3.10 or newer), so upgrading or replacing Python needs nothing from you.

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File $HOME\git\claude-launcher\install.ps1 -Uninstall
```

This removes `claude-launcher.cmd`, the profile, the session hook and the launcher's own files in `%LOCALAPPDATA%\claude-launcher` (its fzf, your icon). It leaves the launcher's state in `%USERPROFILE%\.claude\state\claude-launcher` and says so.
