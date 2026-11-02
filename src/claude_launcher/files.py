"""Putting a finished file in place, for the installer."""

from __future__ import annotations

import os
import time

# Windows refuses to replace a file another process has open for a moment (a
# virus scanner, an indexer, Windows Terminal reading its fragment), and a
# moment later it can. The hook retries for 40 ms; nothing waits on an
# install, so this waits up to about half a second.
_TRIES, _PAUSE = 10, 0.05


def replace(src: os.PathLike | str, dst: os.PathLike | str) -> None:
    """os.replace, tried again for a moment if the file is in use."""
    for i in range(_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == _TRIES - 1:
                raise
            time.sleep(_PAUSE)
