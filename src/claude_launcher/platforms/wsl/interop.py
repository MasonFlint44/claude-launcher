"""Running Windows programs from WSL, by their full path in System32. Their
bare names work only while WSL adds Windows' PATH to the distro's
(`[interop] appendWindowsPath`, often turned off), and Windows' drives
aren't always under /mnt (`[automount] root`), so System32 is found on PATH
if it's there, else on whichever Windows drive has it mounted."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# The file systems WSL mounts Windows' drives with: drvfs (WSL 1), 9p
# (WSL 2), virtiofs (WSL 2's newer option).
_DRIVE_TYPES = ("drvfs", "9p", "virtiofs")


def run(argv: Sequence[str], cwd: Path | None = None) -> bytes:
    """A program's output; raises if it can't run or fails."""
    return subprocess.run(list(argv), capture_output=True, timeout=20, cwd=cwd, check=True).stdout


def _unescape(field: str) -> str:
    """/proc/mounts writes a space in a path as \\040, and so on."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


class Interop:
    def __init__(
        self,
        env: Mapping[str, str] = os.environ,
        mounts: str = "/proc/mounts",
        run: Callable[..., bytes] = run,
    ):
        self.env = env
        self.mounts = mounts
        self._run = run
        self._system32: Path | None = None

    def system32(self) -> Path | None:
        if self._system32 is None:
            self._system32 = self._find_system32()
        return self._system32

    def _find_system32(self) -> Path | None:
        on_path = shutil.which("cmd.exe", path=self.env.get("PATH", ""))
        if on_path:
            return Path(on_path).parent
        try:
            with open(self.mounts, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return None
        for line in lines:
            fields = line.split()
            if len(fields) < 3 or fields[2] not in _DRIVE_TYPES:
                continue
            # Windows' drives are case-insensitive: this finds C:\WINDOWS\system32 too.
            system32 = Path(_unescape(fields[1])) / "Windows" / "System32"
            if (system32 / "cmd.exe").is_file():
                return system32
        return None

    def run(self, program: str, args: Sequence[str]) -> bytes:
        """Run a program in System32, from there: cmd.exe complains when it
        starts in a WSL directory, which Windows sees as a network path."""
        system32 = self.system32()
        if system32 is None:
            raise RuntimeError("can't find Windows' System32 directory from WSL")
        try:
            return self._run([str(system32 / program), *args], cwd=system32)
        except subprocess.CalledProcessError as e:
            said = (e.stderr or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(f"{program} failed" + (f": {said[-1]}" if said else "")) from e
        except FileNotFoundError as e:
            raise RuntimeError(f"can't find {system32 / program}") from e
        except OSError as e:
            # Exec format error: Windows programs don't run here.
            raise RuntimeError(f"can't run Windows programs from WSL (is interop off?): {e.strerror}") from e

    def wevtutil(self, args: Sequence[str]) -> str | None:
        """For EventLog: wevtutil's output, or None if it couldn't say."""
        try:
            return self.run("wevtutil.exe", args).decode("utf-8", errors="replace")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return None

    def user_sid(self) -> str | None:
        """For WindowsEvents: the Windows user's SID, which logoffs are logged under."""
        try:
            out = self.run("whoami.exe", ["/user", "/fo", "csv", "/nh"])
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return None
        m = re.search(rb'"(S-1-[0-9-]+)"', out)
        return m.group(1).decode() if m else None
