"""Start the face server with no terminal of its own, and find it again.

The trick behind "runs in the background": the command starts a second copy of the server
with no terminal attached, points that copy's output at the log, writes down its pid, and
exits. Nothing is left holding the child -- no shell to hang up on it, no console to be
closed under it.

This file is deliberately a small copy of the board's `board/daemon.py` rather than an
import of it: the two packages are installed separately and pin nothing of each other, and
forty lines shared by a dependency is a worse trade than forty lines written twice.

`start_new_session` is POSIX-only in CPython. Windows gets the same thing by another name:
`DETACHED_PROCESS` (no console) plus `CREATE_NEW_PROCESS_GROUP` (its own signals).
"""

from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def detach(command: list[str], log: Path, mode: str = "a") -> int:
    """Start *command* with no terminal of its own, and return its pid."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, mode, encoding="utf-8") as out:
        shared = {
            "stdin": subprocess.DEVNULL,
            "stdout": out,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            return subprocess.Popen(
                command,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
                **shared,
            ).pid
        return subprocess.Popen(command, start_new_session=True, **shared).pid


def running(pid: int) -> bool:
    """Whether *pid* names a live process.

    Signal 0 asks the system whether it could deliver, which is not the same question: an
    exited child whose parent has not reaped it answers yes, so Linux's `/proc` settles
    that. Windows must not be asked this way at all -- `os.kill` there terminates the
    process whatever the number, so "are you alive" would become "die".
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _running_on_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    fields = stat.rsplit(")", 1)[-1].split()
    return not fields or fields[0] != "Z"


def _running_on_windows(pid: int) -> bool:
    """Ask Windows whether a process is still running, without touching it.

    `OpenProcess` fails for a pid that is gone, and `GetExitCodeProcess` separates a live
    process from a dead one whose object another handle is still holding open.
    """
    ctypes, wintypes, kernel32 = _kernel32()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _kernel32():
    """Windows' kernel32, with the signatures this module relies on declared.

    ctypes assumes a function returns `c_int`, and a 64-bit Windows HANDLE does not fit in
    one: the call succeeds and everything after it works on a truncated handle, so a live
    process reads as dead. Declaring them is correctness, not tidiness.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    return ctypes, wintypes, kernel32


def read_pid(path: Path) -> int | None:
    """The pid in the first line of *path*, or None when missing or not a number."""
    try:
        return int(path.read_text(encoding="utf-8").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None


def read_identity(path: Path) -> str | None:
    """The start time written beside the pid, so a reused pid is not mistaken for it."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return lines[1].strip() if len(lines) > 1 and lines[1].strip() else None


def started_at(pid: int) -> str | None:
    """When the process began, as the system reports it, or None where it cannot be read.

    A pid is reused: the number in a file left behind by a crash can be someone else's by
    the time it is read, and signalling that would be worse than doing nothing. Linux gives
    this in `/proc`, macOS through `ps`, Windows through the process's creation time.
    """
    if os.name == "nt":
        return _windows_started_at(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    else:
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) > 19:
            return fields[19]
    try:
        answered = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
        ).stdout.strip()
    except OSError:
        return None
    return answered or None


def _windows_started_at(pid: int) -> str | None:
    """The process's creation time, so a reused pid can be told from the original."""
    ctypes, wintypes, kernel32 = _kernel32()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        asked = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not asked:
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def write_pid(path: Path, pid: int) -> None:
    """Write the pid, and what the system says its process began, for `stop` to check."""
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = started_at(pid) or ""
    path.write_text(f"{pid}\n{identity}\n", encoding="utf-8")


def answering(url: str, timeout: float = 1.0) -> bool:
    """Whether something at *url* answers at all -- the cheapest "is it up"."""
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
    except (urllib.error.URLError, OSError):
        return False
    return True
