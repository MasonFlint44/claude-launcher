"""LinuxHost's boot: the kernel's, except under WSL's kernel, where every
distro shares one VM and a distro's own boot starts with its pid 1."""

import os

import pytest

from claude_launcher.platforms.linux.host import LinuxHost

VM_BOOT = "5e1c2a7b-0d4f-4e8a-9b3c-6f2d1e0a9c84"
NOW = 1_800_000_000.0
HZ = os.sysconf("SC_CLK_TCK")


def fake_proc(root, release="6.6.87.2-microsoft-standard-WSL2", init_start=1234, stat=True):
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(VM_BOOT + "\n")
    (root / "sys" / "kernel" / "osrelease").write_text(release + "\n")
    (root / "uptime").write_text("5000.00 9000.00\n")
    if stat:
        (root / "1").mkdir()
        rest = " ".join(["S", "0", "1", "1"] + ["0"] * 15 + [str(init_start), "1000"])
        (root / "1" / "stat").write_text(f"1 (systemd) {rest}\n")
    return LinuxHost(str(root))


def test_native_linux_is_unchanged(tmp_path):
    host = fake_proc(tmp_path, release="6.8.0-31-generic")
    assert host.boot_id() == VM_BOOT
    assert host.boot_start(NOW) == NOW - 5000


def test_wsl_boot_is_the_distros(tmp_path):
    host = fake_proc(tmp_path, init_start=300 * HZ)
    assert host.boot_id() == "5e1c2a7b0d4f4e8a9b3c" + f"{300 * HZ:012x}"
    assert len(host.boot_id()) == 32
    assert host.boot_start(NOW) == pytest.approx(NOW - 5000 + 300)


def test_a_distro_started_again_is_another_boot(tmp_path):
    first = fake_proc(tmp_path / "a", init_start=100).boot_id()
    assert fake_proc(tmp_path / "b", init_start=200).boot_id() != first


def test_without_pid_1s_stat_the_vms_boot(tmp_path):
    host = fake_proc(tmp_path, stat=False)
    assert host.boot_id() == VM_BOOT
    assert host.boot_start(NOW) == NOW - 5000
