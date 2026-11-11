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
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_pid(path: Path) -> int | None:
    """The pid in *path*, or None when it is missing or is not a number."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def answering(url: str, timeout: float = 1.0) -> bool:
    """Whether something at *url* answers at all -- the cheapest "is it up"."""
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
    except (urllib.error.URLError, OSError):
        return False
    return True
