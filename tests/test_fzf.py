import hashlib
import io
import tarfile

import pytest

from claude_launcher import fzf


def fake_fzf(directory, version):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fzf"
    path.write_text(f"#!/bin/sh\necho '{version} (debian)'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path):
    return {"PATH": str(tmp_path / "sysbin"), "HOME": str(tmp_path / "home")}


def test_version(tmp_path):
    assert fzf.version(str(fake_fzf(tmp_path, "0.44.1"))) == (0, 44, 1)
    assert fzf.version(str(fake_fzf(tmp_path / "x", "0.60"))) == (0, 60, 0)
    assert fzf.version(str(tmp_path / "missing")) is None


def test_system_fzf_new_enough(tmp_path, env):
    path = fake_fzf(tmp_path / "sysbin", "0.67.0")
    assert fzf.find(env) == (str(path), (0, 67, 0))


def test_system_fzf_too_old_uses_our_own(tmp_path, env):
    fake_fzf(tmp_path / "sysbin", "0.44.1")
    own = fake_fzf(fzf.private_dir(env), fzf.PINNED)
    assert fzf.find(env) == (str(own), (0, 74, 5))


def test_nothing_new_enough(tmp_path, env):
    fake_fzf(tmp_path / "sysbin", "0.60.3")
    assert fzf.find(env) == (None, (0, 60, 3))


def test_none_at_all(env):
    assert fzf.find(env) == (None, None)


def test_private_dir_follows_xdg(env):
    assert fzf.private_dir({**env, "XDG_DATA_HOME": "/data"}) == fzf.private_dir({"XDG_DATA_HOME": "/data"})
    assert str(fzf.private_dir(env)).endswith(".local/share/claude-launcher/bin")


def tarball(content=b"#!/bin/sh\necho '0.74.5 (pinned)'\n", name="fzf"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestFetch:
    NAME = f"fzf-{fzf.PINNED}-linux_amd64.tar.gz"

    def pin(self, monkeypatch, data):
        monkeypatch.setattr(fzf, "checksums", lambda path=None: {self.NAME: hashlib.sha256(data).hexdigest()})

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
        with pytest.raises(KeyError):
            fzf.fetch(tmp_path / "bin", machine="x86_64", download=lambda url: data)


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
