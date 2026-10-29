import hashlib
import io
import tarfile
import zipfile

import pytest

from claude_launcher import fzf
from conftest import posix_only


def fake_fzf(directory, version):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fzf"
    path.write_text(f"#!/bin/sh\necho '{version} (debian)'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path):
    return {"PATH": str(tmp_path / "sysbin"), "HOME": str(tmp_path / "home")}


@pytest.fixture
def own(tmp_path):
    return tmp_path / "data" / "bin"


@posix_only  # the fake fzf is a shell script
def test_version(tmp_path):
    assert fzf.version(str(fake_fzf(tmp_path, "0.44.1"))) == (0, 44, 1)
    assert fzf.version(str(fake_fzf(tmp_path / "x", "0.60"))) == (0, 60, 0)
    assert fzf.version(str(tmp_path / "missing")) is None


@posix_only  # the fake fzf is a shell script
def test_system_fzf_new_enough(tmp_path, env, own):
    path = fake_fzf(tmp_path / "sysbin", "0.67.0")
    assert fzf.find(own, env) == (str(path), (0, 67, 0))


@posix_only  # the fake fzf is a shell script
def test_system_fzf_too_old_uses_our_own(tmp_path, env, own):
    fake_fzf(tmp_path / "sysbin", "0.44.1")
    ours = fake_fzf(own, fzf.PINNED)
    assert fzf.find(own, env) == (str(ours), (0, 74, 5))


@posix_only  # the fake fzf is a shell script
def test_nothing_new_enough(tmp_path, env, own):
    fake_fzf(tmp_path / "sysbin", "0.60.3")
    assert fzf.find(own, env) == (None, (0, 60, 3))


def test_none_at_all(env, own):
    assert fzf.find(own, env) == (None, None)


def test_private_dir_is_bin_in_the_data_dir(tmp_path):
    assert fzf.private_dir(tmp_path) == tmp_path / "bin"


def tarball(content=b"#!/bin/sh\necho '0.74.5 (pinned)'\n", name="fzf"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def zipped(content=b"MZ fzf", name="fzf.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, content)
    return buf.getvalue()


class TestFetch:
    NAME = f"fzf-{fzf.PINNED}-linux_amd64.tar.gz"

    def pin(self, monkeypatch, data, name=NAME):
        monkeypatch.setattr(fzf, "checksums", lambda path=None: {name: hashlib.sha256(data).hexdigest()})

    @posix_only  # the fake fzf is a shell script
    def test_verified_and_installed(self, tmp_path, monkeypatch):
        data = tarball()
        self.pin(monkeypatch, data)
        urls = []
        path = fzf.fetch(tmp_path / "bin", machine="x86_64", download=lambda url: urls.append(url) or data)
        assert urls == [f"https://github.com/junegunn/fzf/releases/download/v{fzf.PINNED}/{self.NAME}"]
        assert fzf.version(str(path)) == (0, 74, 5)
        assert [p.name for p in (tmp_path / "bin").iterdir()] == ["fzf"]

    def test_checksum_mismatch(self, tmp_path):
        with pytest.raises(RuntimeError, match="checksum"):
            fzf.fetch(tmp_path / "bin", machine="x86_64", download=lambda url: tarball())
        assert not (tmp_path / "bin" / "fzf").exists()

    @pytest.mark.parametrize(("system", "machine"), [("linux", "sparc64"), ("plan9", "x86_64")])
    def test_no_build_for_this_machine(self, tmp_path, system, machine):
        with pytest.raises(RuntimeError, match="no fzf"):
            fzf.fetch(tmp_path / "bin", system=system, machine=machine, download=lambda url: b"")

    def test_tarball_without_fzf(self, tmp_path, monkeypatch):
        data = tarball(name="README")
        self.pin(monkeypatch, data)
        with pytest.raises(RuntimeError, match="no fzf program"):
            fzf.fetch(tmp_path / "bin", machine="x86_64", download=lambda url: data)

    def test_windows_zip(self, tmp_path, monkeypatch):
        name = f"fzf-{fzf.PINNED}-windows_amd64.zip"
        data = zipped()
        self.pin(monkeypatch, data, name)
        path = fzf.fetch(tmp_path / "bin", system="windows", machine="AMD64", download=lambda url: data)
        assert path == tmp_path / "bin" / "fzf.exe" and path.read_bytes() == b"MZ fzf"

    def test_zip_without_fzf(self, tmp_path, monkeypatch):
        name = f"fzf-{fzf.PINNED}-windows_amd64.zip"
        data = zipped(name="README")
        self.pin(monkeypatch, data, name)
        with pytest.raises(RuntimeError, match="no fzf.exe program"):
            fzf.fetch(tmp_path / "bin", system="windows", machine="AMD64", download=lambda url: data)


class TestPinnedRelease:
    def test_the_checksums_file_is_the_pinned_release(self):
        sums = fzf.checksums()
        assert sums and all(fzf.PINNED in name and len(sha) == 64 for name, sha in sums.items())

    @pytest.mark.parametrize(
        ("machine", "build"),
        [
            ("x86_64", "linux_amd64"),
            ("aarch64", "linux_arm64"),
            ("armv7l", "linux_armv7"),
            ("riscv64", "linux_riscv64"),
        ],
    )
    def test_linux_builds(self, machine, build):
        assert fzf.build_name("linux", machine) == f"fzf-{fzf.PINNED}-{build}.tar.gz"

    @pytest.mark.parametrize(("machine", "build"), [("AMD64", "windows_amd64"), ("ARM64", "windows_arm64")])
    def test_windows_builds(self, machine, build):
        # platform.machine() on Windows says AMD64 or ARM64.
        assert fzf.build_name("windows", machine) == f"fzf-{fzf.PINNED}-{build}.zip"
