"""The shell the desktop launcher and WSL's profile start claude-launcher in.

It's your own login shell, started as an interactive login shell, so Claude
gets the PATH a terminal tab would give it. Tools like nvm add themselves to
~/.bashrc or ~/.zshrc, which a shell that isn't interactive skips: Ubuntu's
~/.bashrc stops at its first lines then. Claude runs its hooks with its own
PATH, so a hook that runs `node` found Windows' node in WSL, or none at all.
"""

from __future__ import annotations

import os
import pwd
import re
import shlex

# Shells that take -lic and `a || b`. Any other login shell (tcsh, dash, a
# shell we don't know) gets bash.
SHELLS = ("bash", "zsh", "fish")
FALLBACK = "bash"


def login_shell(user: str | None = None) -> str:
    """`user`'s login shell (this user's, by default) if it's one of SHELLS;
    else bash. Read when the installer runs: re-run it after `chsh`."""
    try:
        shell = (pwd.getpwnam(user) if user else pwd.getpwuid(os.getuid())).pw_shell
    except (KeyError, OSError):
        return FALLBACK
    # A plain path: WSL's profile puts it in Windows Terminal's command line unquoted.
    if not re.fullmatch(r"/[\w./+-]+", shell or "") or os.path.basename(shell) not in SHELLS:
        return FALLBACK
    return shell if os.access(shell, os.X_OK) else FALLBACK


def script(shell: str, launcher: str) -> str:
    """What the shell runs: the launcher, or a shell to see why if it fails.
    `launcher` is already quoted for the shell (WSL's is a bare ~/...)."""
    return f"{launcher} || exec {shlex.quote(shell)}"


def argv(shell: str, launcher: str) -> list[str]:
    return [shell, "-lic", script(shell, launcher)]
