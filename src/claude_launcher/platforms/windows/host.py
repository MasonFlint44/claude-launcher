"""Windows' Host: the boot counter, processes, the console, and a lock, from
the standard library (winreg, msvcrt, ctypes) so nothing needs installing.

The Win32 functions are declared on this module's own WinDLL objects
(`_api`): ctypes.windll's are shared with any other code in the process,
which would see the types set here."""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import functools
import ntpath
import os
import re
import time
from collections.abc import Iterator, Sequence
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

# Windows counts its full boots here: a restart adds one. Starting after a
# Fast Startup shutdown, which is a hibernation, doesn't (measured in a VM).
# As 32 hex digits, it's a boot ID like Linux's. The hook has its own copy.
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
# FILETIME counts 100 ns from 1601; Unix time from 1970.
_FILETIME_1970 = 116444736000000000
# A process that began this long after its record's Claude started isn't that
# Claude, but a new process given the same pid (Windows reuses them soon).
_REUSED_AFTER = 10
_HANDLE = ctypes.c_void_p


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
    program = ntpath.basename(argv[0]).lower()
    if program in ("claude.exe", "claude"):
        return True
    if program in ("node.exe", "node"):
        return any(
            "@anthropic-ai" in a.replace("\\", "/") and a.lower().endswith(("cli.js", "cli.mjs")) for a in argv[1:]
        )
    return False


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


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


def _declare(dll, name: str, restype, *argtypes):
    f = getattr(dll, name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


@functools.cache
def _api() -> SimpleNamespace:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    dword_p, filetime_p = ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.FILETIME)
    return SimpleNamespace(
        OpenProcess=_declare(kernel32, "OpenProcess", _HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD),
        CloseHandle=_declare(kernel32, "CloseHandle", wintypes.BOOL, _HANDLE),
        GetExitCodeProcess=_declare(kernel32, "GetExitCodeProcess", wintypes.BOOL, _HANDLE, dword_p),
        GetProcessTimes=_declare(
            kernel32, "GetProcessTimes", wintypes.BOOL, _HANDLE, filetime_p, filetime_p, filetime_p, filetime_p
        ),
        LocalFree=_declare(kernel32, "LocalFree", ctypes.c_void_p, ctypes.c_void_p),
        GetTickCount64=_declare(kernel32, "GetTickCount64", ctypes.c_ulonglong),
        GetConsoleMode=_declare(kernel32, "GetConsoleMode", wintypes.BOOL, _HANDLE, dword_p),
        SetConsoleMode=_declare(kernel32, "SetConsoleMode", wintypes.BOOL, _HANDLE, wintypes.DWORD),
        WaitForSingleObject=_declare(kernel32, "WaitForSingleObject", wintypes.DWORD, _HANDLE, wintypes.DWORD),
        ReadConsoleInputW=_declare(
            kernel32, "ReadConsoleInputW", wintypes.BOOL, _HANDLE, ctypes.POINTER(_InputRecord), wintypes.DWORD, dword_p
        ),
        NtQueryInformationProcess=_declare(
            ntdll,
            "NtQueryInformationProcess",
            wintypes.LONG,
            _HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ),
        CommandLineToArgvW=_declare(
            shell32,
            "CommandLineToArgvW",
            ctypes.POINTER(wintypes.LPWSTR),
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ),
    )


@contextlib.contextmanager
def _process(pid: int) -> Iterator[int | None]:
    """A handle to a process that's still running, or None."""
    api = _api()
    handle = api.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        yield None
        return
    try:
        code = wintypes.DWORD()
        yield handle if api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == _STILL_ACTIVE else None
    finally:
        api.CloseHandle(handle)


def command_line(pid: int) -> list[str] | None:
    """Another process's command line, split as Windows programs split theirs."""
    api = _api()
    with _process(pid) as handle:
        if handle is None:
            return None
        size = wintypes.ULONG(0)
        api.NtQueryInformationProcess(handle, _COMMAND_LINE_INFO, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if api.NtQueryInformationProcess(handle, _COMMAND_LINE_INFO, buf, size, ctypes.byref(size)) != 0:
            return None
        found = _UnicodeString.from_buffer(buf)
        # Length is in bytes, and the text needn't end in a NUL.
        text = ctypes.wstring_at(found.Buffer, found.Length // 2) if found.Buffer and found.Length else ""
    if not text:
        return None
    count = ctypes.c_int()
    argv = api.CommandLineToArgvW(text, ctypes.byref(count))
    if not argv:
        return [text]
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        api.LocalFree(argv)


def process_started(pid: int) -> float | None:
    """When a running process began, as a Unix time."""
    api = _api()
    with _process(pid) as handle:
        if handle is None:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
    created = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    return (created - _FILETIME_1970) / 1e7


def is_same_process(created: float | None, started: float | None) -> bool:
    """Whether a process that began at `created` can be the Claude whose hook
    wrote `started`: Claude starts before its hook runs."""
    return created is None or started is None or created <= started + _REUSED_AFTER


def _restore_output_mode(mode: int) -> None:
    with contextlib.suppress(OSError):
        import msvcrt

        fd = os.open("CONOUT$", os.O_RDWR)
        try:
            _api().SetConsoleMode(msvcrt.get_osfhandle(fd), mode)
        finally:
            os.close(fd)


_KEY_EVENT = 0x0001


class WindowsHost:
    def boot_id(self) -> str:
        return boot_id()

    def __init__(self) -> None:
        self._output_mode: int | None = None  # the console's, before open_tty changed it

    def boot_start(self, now: float) -> float:
        return now - _api().GetTickCount64() / 1000

    def claude_running(self, pid: object, started: float | None = None) -> bool:
        """`started`, the record's, tells a reused pid from its Claude."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        argv = command_line(pid)
        if argv is None or not is_claude(argv):
            return False
        return started is None or is_same_process(process_started(pid), started)

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
        """The console's screen, for writing escapes, with VT processing on.
        The mode it had is put back when the launcher exits: the caller
        closes the file itself, and the mode belongs to the console, not the
        file, so it's the same on any handle to it."""
        import msvcrt

        try:
            fd = os.open("CONOUT$", os.O_RDWR)
        except OSError:
            return None
        api = _api()
        handle = msvcrt.get_osfhandle(fd)
        mode = wintypes.DWORD()
        if api.GetConsoleMode(handle, ctypes.byref(mode)):
            wanted = mode.value | _ENABLE_PROCESSED_OUTPUT | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if wanted != mode.value and api.SetConsoleMode(handle, wanted) and self._output_mode is None:
                self._output_mode = mode.value
                atexit.register(_restore_output_mode, mode.value)
        return fd

    def terminal_reply(self, fd: int, query: bytes, done: re.Pattern[bytes], timeout: float) -> bytes | None:
        """Write `query` and read the terminal's answer until `done` matches.
        The answer comes in through the console's input, as key presses once
        VT input is on; line input and echo are off while it waits, so it
        isn't shown."""
        import msvcrt

        api = _api()
        try:
            conin = os.open("CONIN$", os.O_RDWR)
        except OSError:
            return None
        handle = msvcrt.get_osfhandle(conin)
        old = wintypes.DWORD()
        if not api.GetConsoleMode(handle, ctypes.byref(old)):
            os.close(conin)
            return None
        quiet = _ENABLE_LINE_INPUT | _ENABLE_ECHO_INPUT | _ENABLE_PROCESSED_INPUT
        api.SetConsoleMode(handle, (old.value | _ENABLE_VIRTUAL_TERMINAL_INPUT) & ~quiet)
        try:
            os.write(fd, query)
            buf = ""
            end = time.monotonic() + timeout
            while not done.search(buf.encode("utf-8", "surrogatepass")):
                left = end - time.monotonic()
                if left <= 0 or api.WaitForSingleObject(handle, int(left * 1000) + 1) != _WAIT_OBJECT_0:
                    return None
                buf += _typed(api, handle)
            return buf.encode("utf-8", "surrogatepass")
        finally:
            api.SetConsoleMode(handle, old.value)
            os.close(conin)


def _typed(api, handle) -> str:
    """The characters waiting in the console's input; other events (focus,
    resizing, key releases) are taken and dropped."""
    records = (_InputRecord * 64)()
    n = wintypes.DWORD()
    if not api.ReadConsoleInputW(handle, records, 64, ctypes.byref(n)):
        return ""
    out = []
    for r in records[: n.value]:
        if r.EventType == _KEY_EVENT and r.Event.KeyEvent.bKeyDown and r.Event.KeyEvent.UnicodeChar != "\0":
            out.append(r.Event.KeyEvent.UnicodeChar * max(1, r.Event.KeyEvent.wRepeatCount))
    return "".join(out)
