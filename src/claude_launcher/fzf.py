"""Which fzf the picker runs, and fetching one when the system's is too old.

The picker needs fzf 0.67 or newer: older releases can't --listen on a Unix
socket, and before 0.45 there's no `transform` action at all. Ubuntu 24.04
(0.44), Debian 12 (0.38) and Debian 13 (0.60) ship older ones, so install.sh
fetches a pinned release, checksum-verified, into a directory only the
launcher uses. The system's own fzf, and everything else that uses it, is
left alone.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

from .paths import REPO_DIR

MIN_VERSION = (0, 67, 0)
PINNED = "0.74.5"
# The release's own checksums file, as it is. To move the pin, add the new
# release's file and change PINNED.
CHECKSUMS = REPO_DIR / "share" / "fzf" / f"fzf_{PINNED}_checksums.txt"
# fzf's names for processors, by what platform.machine() says on each OS.
ARCHES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "armv6l": "armv6",
    "ppc64le": "ppc64le",
    "riscv64": "riscv64",
    "s390x": "s390x",
    "loongarch64": "loong64",
}
_RELEASES = "https://github.com/junegunn/fzf/releases/download"


def private_dir(data_dir: Path) -> Path:
    """Where the fetched fzf goes, in the launcher's own data directory."""
    return data_dir / "bin"


def version(path: str) -> tuple[int, ...] | None:
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", done.stdout.strip())
    return tuple(int(g or 0) for g in m.groups()) if m else None


def show(v: tuple[int, ...] | None) -> str:
    return ".".join(map(str, v)) if v else "none"


def candidates(own_dir: Path, env: Mapping[str, str] = os.environ) -> list[str]:
    """The system's fzf first, then the launcher's own copy."""
    found = [p for p in (shutil.which("fzf", path=env.get("PATH")),) if p]
    found += [str(own) for own in (own_dir / "fzf", own_dir / "fzf.exe") if own.is_file() and os.access(own, os.X_OK)]
    return found


def find(own_dir: Path, env: Mapping[str, str] = os.environ) -> tuple[str | None, tuple[int, ...] | None]:
    """The fzf to use, and its version; or (None, the newest version seen)."""
    newest = None
    for path in candidates(own_dir, env):
        v = version(path)
        if v and v >= MIN_VERSION:
            return path, v
        newest = max(filter(None, (newest, v)), default=None)
    return None, newest


def checksums(path: Path | None = None) -> dict[str, str]:
    """{file name: sha256} from the pinned release's checksums file."""
    found = {}
    for line in (path or CHECKSUMS).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            found[parts[1]] = parts[0]
    return found


def build_name(system: str, machine: str) -> str | None:
    """The release's file for this OS (fzf's name for it, like "linux") and
    processor, or None if there's no build for it."""
    arch = ARCHES.get(machine.lower())
    kind = "zip" if system == "windows" else "tar.gz"
    name = f"fzf-{PINNED}-{system}_{arch}.{kind}"
    return name if arch and name in checksums() else None


def fetch(
    dest: Path,
    system: str = "linux",
    machine: str | None = None,
    download: Callable[[str], bytes] | None = None,
) -> Path:
    """Download the pinned fzf for this machine into `dest`, checking it
    against the release's checksum before it's used. Returns its path."""
    machine = machine or platform.machine()
    name = build_name(system, machine)
    if name is None:
        raise RuntimeError(f"no fzf {PINNED} build for {system} on {machine}; install fzf 0.67 or newer yourself")
    data = (download or _download)(f"{_RELEASES}/v{PINNED}/{name}")
    if hashlib.sha256(data).hexdigest() != checksums()[name]:
        raise RuntimeError(f"{name} didn't match its checksum; not using it")
    program = "fzf.exe" if name.endswith(".zip") else "fzf"
    binary = _unpack(name, data, program)
    dest.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=".fzf.")
    with os.fdopen(fd, "wb") as f:
        f.write(binary)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest / program)
    return dest / program


def _unpack(name: str, data: bytes, program: str) -> bytes:
    """The fzf program from a release archive: a .tar.gz, or Windows' .zip."""
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return z.read(program)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = tar.getmember(program)
            if member.isfile():
                return tar.extractfile(member).read()  # type: ignore[union-attr]
    except KeyError:
        pass
    raise RuntimeError(f"{name} has no {program} program in it")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()
