"""Windows' Host: the boot counter, processes, the console, and a lock, from
the standard library (winreg, msvcrt, ctypes) so nothing needs installing."""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import time
from collections.abc import Iterator, Sequence
from ctypes import wintypes
from pathlib import Path

# Windows counts its boots here; a restart, and a start after Fast Startup's
# shutdown, both add one. As 32 hex digits, it's a boot ID like Linux's.
_BOOT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION: allowed for any process of the same user
_STILL_ACTIVE = 259
_COMMAND_LINE_INFO = 60  # ProcessCommandLineInformation, Windows 8.1+

# Console modes.
_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_ENABLE_PROCESSED_OUTPUT = 0x0001
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_WAIT_OBJECT_0 = 0


def boot_counter() -> int:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BOOT_KEY) as key:
        return int(winreg.QueryValueEx(key, "BootId")[0])


def boot_id() -> str:
    return format(boot_counter(), "032x")


def is_claude(argv: Sequence[str]) -> bool:
    """The native claude.exe, or npm's package run by node."""
    if not argv:
        return False
    program = os.path.basename(argv[0].replace("/", "\\")).lower()
    if program in ("claude.exe", "claude"):
        return True
    if program in ("node.exe", "node"):
        return any(
            "@anthropic-ai" in a.replace("\\", "/") and a.lower().endswith(("cli.js", "cli.mjs")) for a in argv[1:]
        )
    return False


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]


def command_line(pid: int) -> list[str] | None:
    """Another process's command line, split as Windows programs split theirs."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    shell32 = ctypes.WinDLL("shell32")
    handle = kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        size = wintypes.ULONG(0)
        ntdll.NtQueryInformationProcess(handle, _COMMAND_LINE_INFO, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if ntdll.NtQueryInformationProcess(handle, _COMMAND_LINE_INFO, buf, size, ctypes.byref(size)) != 0:
            return None
        text = ctypes.cast(buf, ctypes.POINTER(_UnicodeString)).contents.Buffer or ""
    finally:
        kernel32.CloseHandle(handle)
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(text, ctypes.byref(count))
    if not argv:
        return [text]
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        kernel32.LocalFree(argv)


class _KeyEvent(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("UnicodeChar", wintypes.WCHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class _InputRecord(ctypes.Structure):
    class _Event(ctypes.Union):
        _fields_ = [("KeyEvent", _KeyEvent), ("_pad", ctypes.c_byte * 16)]

    _fields_ = [("EventType", wintypes.WORD), ("Event", _Event)]


_KEY_EVENT = 0x0001


class WindowsHost:
    def boot_id(self) -> str:
        return boot_id()

    def boot_start(self, now: float) -> float:
        ticks = ctypes.windll.kernel32.GetTickCount64  # type: ignore[attr-defined]
        ticks.restype = ctypes.c_ulonglong
        return now - ticks() / 1000

    def claude_running(self, pid: object) -> bool:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        argv = command_line(pid)
        return argv is not None and is_claude(argv)

    def user(self) -> str:
        return os.environ.get("USERNAME") or os.getlogin()

    @contextlib.contextmanager
    def lock(self, path: Path) -> Iterator[None]:
        import msvcrt

        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:  # LK_LOCK gives up after 10 s; wait as long as flock would
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    def open_tty(self) -> int | None:
        """The console's screen, for writing escapes, with VT processing on."""
        import msvcrt

        try:
            fd = os.open("CONOUT$", os.O_RDWR)
        except OSError:
            return None
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = msvcrt.get_osfhandle(fd)
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | _ENABLE_PROCESSED_OUTPUT | _ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        return fd

    def terminal_reply(self, fd: int, query: bytes, done: re.Pattern[bytes], timeout: float) -> bytes | None:
        """Write `query` and read the terminal's answer until `done` matches.
        The answer comes in through the console's input, as key presses once
        VT input is on; line input and echo are off while it waits, so it
        isn't shown."""
        import msvcrt

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        try:
            conin = os.open("CONIN$", os.O_RDWR)
        except OSError:
            return None
        handle = msvcrt.get_osfhandle(conin)
        old = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(old)):
            os.close(conin)
            return None
        quiet = _ENABLE_LINE_INPUT | _ENABLE_ECHO_INPUT | _ENABLE_PROCESSED_INPUT
        kernel32.SetConsoleMode(handle, (old.value | _ENABLE_VIRTUAL_TERMINAL_INPUT) & ~quiet)
        try:
            os.write(fd, query)
            buf = ""
            end = time.monotonic() + timeout
            while not done.search(buf.encode("utf-8", "surrogatepass")):
                left = end - time.monotonic()
                if left <= 0 or kernel32.WaitForSingleObject(handle, int(left * 1000) + 1) != _WAIT_OBJECT_0:
                    return None
                buf += _typed(kernel32, handle)
            return buf.encode("utf-8", "surrogatepass")
        finally:
            kernel32.SetConsoleMode(handle, old.value)
            os.close(conin)


def _typed(kernel32, handle) -> str:
    """The characters waiting in the console's input; other events (focus,
    resizing, key releases) are taken and dropped."""
    records = (_InputRecord * 64)()
    n = wintypes.DWORD()
    if not kernel32.ReadConsoleInputW(handle, records, 64, ctypes.byref(n)):
        return ""
    out = []
    for r in records[: n.value]:
        if r.EventType == _KEY_EVENT and r.Event.KeyEvent.bKeyDown and r.Event.KeyEvent.UnicodeChar != "\0":
            out.append(r.Event.KeyEvent.UnicodeChar * max(1, r.Event.KeyEvent.wRepeatCount))
    return "".join(out)
