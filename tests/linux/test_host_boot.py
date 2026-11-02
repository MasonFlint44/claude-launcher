"""LinuxHost's boot: the kernel's, except under WSL's kernel, where every
distro shares one VM and a distro's own boot starts with its pid 1."""

import os

import pytest

from claude_launcher.platforms.linux.host import LinuxHost
from conftest import NOW

VM_BOOT = "5e1c2a7b-0d4f-4e8a-9b3c-6f2d1e0a9c84"
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


@pytest.mark.parametrize(
    "marker", ["run/WSL", "proc/sys/fs/binfmt_misc/WSLInterop", "proc/sys/fs/binfmt_misc/WSLInterop-late"]
)
def test_wsl_on_a_kernel_of_its_own_is_the_distros_boot(tmp_path, marker):
    # A custom kernel from .wslconfig needn't say "microsoft"; WSL's own
    # files (/run beside /proc) still do.
    host = fake_proc(tmp_path / "proc", release="6.12.1-custom", init_start=300 * HZ)
    (tmp_path / marker).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / marker).touch()
    assert host.boot_id() == "5e1c2a7b0d4f4e8a9b3c" + f"{300 * HZ:012x}"


def test_a_custom_kernel_without_wsls_files_is_plain_linux(tmp_path):
    host = fake_proc(tmp_path / "proc", release="6.12.1-custom")
    assert host.boot_id() == VM_BOOT


def test_a_distro_started_again_is_another_boot(tmp_path):
    first = fake_proc(tmp_path / "a", init_start=100).boot_id()
    assert fake_proc(tmp_path / "b", init_start=200).boot_id() != first


def test_without_pid_1s_stat_the_vms_boot(tmp_path):
    host = fake_proc(tmp_path, stat=False)
    assert host.boot_id() == VM_BOOT
    assert host.boot_start(NOW) == NOW - 5000


def a_claude(root, pid, start_ticks):
    (root / str(pid)).mkdir()
    (root / str(pid) / "cmdline").write_bytes(b"/home/me/.local/bin/claude\0")
    rest = " ".join(["S", "0", "1", "1"] + ["0"] * 15 + [str(start_ticks), "1000"])
    (root / str(pid) / "stat").write_text(f"{pid} (claude) {rest}\n")


def test_a_claude_that_got_the_pid_since_isnt_the_records(tmp_path, monkeypatch):
    import time

    host = fake_proc(tmp_path, release="6.8.0-31-generic")
    monkeypatch.setattr(time, "time", lambda: NOW)
    boot = NOW - 5000
    a_claude(tmp_path, 700, start_ticks=100 * HZ)  # started 100 s into the boot
    assert host.claude_running(700)
    assert host.claude_running(700, started=boot + 101)  # its own record, written once it was up
    assert not host.claude_running(700, started=boot + 50)  # a record from before it started


def test_a_sleep_in_wsl_doesnt_make_claude_another_process(tmp_path, monkeypatch):
    # WSL's VM stands still while Windows sleeps (its uptime too), then its
    # wall clock is stepped ahead: by the clocks, the Claude seems to have
    # started 20 minutes after its own record.
    import time

    host = fake_proc(tmp_path)
    boot = NOW - 5000
    a_claude(tmp_path, 700, start_ticks=100 * HZ)
    monkeypatch.setattr(time, "time", lambda: NOW + 1200)
    assert host.claude_running(700, started=boot + 101, start_ticks=100 * HZ)
    assert not host.claude_running(700, started=boot + 101)  # an older hook's record: the clocks are all it has


def test_start_ticks_tell_a_claude_that_got_the_pid_since(tmp_path):
    host = fake_proc(tmp_path, release="6.8.0-31-generic")
    a_claude(tmp_path, 700, start_ticks=100 * HZ)
    assert host.claude_running(700, started=NOW, start_ticks=100 * HZ)
    assert not host.claude_running(700, started=NOW, start_ticks=100 * HZ - 1)  # exact, no margin


def test_a_claude_whose_start_cant_be_read_is_taken_as_the_records(tmp_path, monkeypatch):
    # Taken as the record's, so the record waits instead of being sorted while its Claude may still run.
    import time

    host = fake_proc(tmp_path, release="6.8.0-31-generic")
    monkeypatch.setattr(time, "time", lambda: NOW)
    a_claude(tmp_path, 700, start_ticks=100 * HZ)
    (tmp_path / "700" / "stat").unlink()
    assert host.claude_running(700, started=NOW - 5000 + 50, start_ticks=100 * HZ)
    assert host.claude_running(700, started=NOW - 5000 + 50)  # an older hook's record


def test_without_the_uptime_an_older_records_claude_is_its_own(tmp_path):
    host = fake_proc(tmp_path, release="6.8.0-31-generic")
    a_claude(tmp_path, 700, start_ticks=100 * HZ)
    (tmp_path / "uptime").unlink()
    assert host.claude_running(700, started=NOW - 5000 + 50)
    assert host.claude_running(700, started=None)
