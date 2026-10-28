"""Seconds since the user last touched this machine, without opening the camera.

`ZRB_AVATAR_PRESENCE=size` looked by taking a photo, and taking a photo opens the camera.
The light belongs to the driver, not to ffmpeg, so no flag turns it off: the only way
not to flash is not to open the camera at all. Every desktop already records when the
keyboard or mouse last moved, and asking that number lights nothing.

One probe per platform, all answering the same thing:
  * Windows, and WSL through its Windows `powershell.exe` — GetLastInputInfo. A fresh
    PowerShell takes about a second to start, far more than the answer costs, so one
    process is kept running and prints a reading per interval.
  * macOS — `ioreg -c IOHIDSystem`, whose HIDIdleTime counts nanoseconds since the
    last HID event. A few tens of milliseconds per ask, and no camera.
  * Linux — `xprintidle`, when it is installed: milliseconds since the last X input.
Anything else answers None, and None means "cannot tell" — never "away".
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import platform
import shutil
import subprocess
import sys
import time

# Long enough for a slow machine, short enough that a wedged command is not a hang.
COMMAND_TIMEOUT_SECONDS = 10.0
# A probe that died is retried no sooner than this, so a broken one cannot spawn a
# PowerShell per poll.
RETRY_SECONDS = 60.0
# How long the first ask waits for a freshly started probe to print. PowerShell needs about a
# second to start and load its type; answering "unknown" instead would cost a whole interval.
START_SECONDS = 3.0
# How many polls an answer may be missing before it means "cannot tell" rather than
# "the last thing I heard still stands".
STALE_POLLS = 3

_WINDOWS_PROBE = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class ZrbIdle {
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")]
    static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    public static long Seconds() {
        LASTINPUTINFO info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(info);
        if (!GetLastInputInfo(ref info)) { return -1; }
        uint now = (uint)Environment.TickCount;
        return (long)(now - info.dwTime) / 1000;
    }
}
'@
while ($true) {
    [Console]::Out.WriteLine([ZrbIdle]::Seconds())
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds __INTERVAL__
}
"""


class LastInput:
    """Seconds since the user last touched this machine.

    Asked no oftener than *poll_seconds*, and answered from the last reading in between,
    so a caller in a one-second loop costs nothing. *ask* replaces the platform probe,
    which is how the tests exercise it without a desktop.

    None means the probe could not tell: no probe on this platform, no answer, or an
    answer older than STALE_POLLS polls. A caller must read it as unknown — a probe that
    fails says nothing about the desk, and silence is not absence.
    """

    def __init__(self, poll_seconds: float = 5.0, ask=None) -> None:
        self._poll_seconds = poll_seconds
        self._probe = ask or self._ask_platform
        self._value: float | None = None
        self._asked_at = 0.0  # monotonic, when a reading was last requested
        self._read_at = 0.0  # monotonic, when the value in hand was read
        self._windows: "_WindowsProbe | None" = None

    async def seconds(self) -> float | None:
        now = time.monotonic()
        if now - self._asked_at >= self._poll_seconds:
            self._asked_at = now
            reading = await self._probe()
            if reading is not None:
                self._value, self._read_at = reading, now
        if self._value is None or now - self._read_at > self._poll_seconds * STALE_POLLS:
            return None
        return self._value

    def close(self) -> None:
        """Stop the probe's process, if it keeps one. Safe to call twice."""
        if self._windows is not None:
            self._windows.close()
            self._windows = None

    async def _ask_platform(self) -> float | None:
        kind = probe_kind()
        if kind == "windows":
            if self._windows is None:
                self._windows = _WindowsProbe(self._poll_seconds)
            return await self._windows.reading()
        if kind == "ioreg":
            return parse_ioreg(await _run(("ioreg", "-c", "IOHIDSystem")))
        if kind == "xprintidle":
            return parse_xprintidle(await _run(("xprintidle",)))
        return None


class _WindowsProbe:
    """One PowerShell process, kept running, printing the idle time per interval.

    Starting PowerShell costs about a second — more than the probe itself, and a burst
    of it on every poll is the kind of hitch this replaces. So it is started once, its
    lines are drained by a task of their own, and a death is retried after RETRY_SECONDS
    rather than on every ask.
    """

    def __init__(self, poll_seconds: float) -> None:
        self._poll_seconds = poll_seconds
        self._reading: float | None = None
        self._process: "asyncio.subprocess.Process | None" = None
        self._pump: "asyncio.Task | None" = None
        self._first = asyncio.Event()  # set when a started probe says anything at all
        self._started_at = 0.0
        # A chat that exits without closing still takes its probe with it: a PowerShell
        # left behind is 77 MB of RAM and a mystery in Task Manager.
        atexit.register(self._terminate)

    async def reading(self) -> float | None:
        if self._pump is None or self._pump.done():
            if not await self._start():
                return self._reading
            try:
                await asyncio.wait_for(self._first.wait(), START_SECONDS)
            except asyncio.TimeoutError:
                pass
        return self._reading

    async def _start(self) -> bool:
        now = time.monotonic()
        if now < self._started_at + RETRY_SECONDS:
            return False
        self._started_at = now
        self._first = asyncio.Event()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *powershell_command(self._poll_seconds),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:  # no PowerShell here: unknown, and left to the retry
            self._process = None
            return False
        self._pump = asyncio.create_task(self._drain(self._process))
        return True

    async def _drain(self, process: "asyncio.subprocess.Process") -> None:
        # Held by the instance, and read weakly by nothing: a collected task would leave
        # the process alive with its pipe unread.
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:  # the probe exited; the next ask starts another
                    return
                reading = parse_windows(line.decode(errors="ignore"))
                if reading is not None:
                    self._reading = reading
        finally:
            self._first.set()  # whatever happened, a waiter must not sit out START_SECONDS

    def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            self._pump = None
        self._terminate()
        self._process = None

    def _terminate(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except OSError:
                pass


def probe_kind() -> str:
    """Which probe this machine has, by name, so the choice can be asserted.

    A Windows `powershell.exe` on WSL's PATH is the whole point of that branch: the
    camera on this kind of machine is Windows', and so is the input it records.
    """
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "ioreg"
    if _is_wsl() and shutil.which("powershell.exe"):
        return "windows"
    if shutil.which("xprintidle"):
        return "xprintidle"
    return ""


def powershell_command(poll_seconds: float) -> list[str]:
    """PowerShell with the whole probe as one base64 argument.

    Encoded rather than quoted: the script holds a C# block, braces and quotes, and
    every one of them is something a command line would have to get right. Base64 has
    no arguments to lose.
    """
    interval = max(1, int(poll_seconds * 1000))
    script = _WINDOWS_PROBE.replace("__INTERVAL__", str(interval))
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
    ]


def parse_windows(line: str) -> float | None:
    """A line of the Windows probe: whole seconds, or -1 when the call itself failed."""
    try:
        seconds = float(line.strip())
    except ValueError:
        return None
    return None if seconds < 0 else seconds


def parse_ioreg(text: str) -> float | None:
    """ioreg's HIDIdleTime, in nanoseconds: `    | |   "HIDIdleTime" = 2763347000`."""
    for line in reversed(text.splitlines()):
        name, separator, value = line.partition("HIDIdleTime")
        if not separator:
            continue
        _, _, number = value.partition("=")
        try:
            return int(number.strip()) / 1e9
        except ValueError:
            return None
    return None


def parse_xprintidle(text: str) -> float | None:
    """xprintidle prints milliseconds as a bare integer."""
    try:
        return int(text.strip()) / 1000
    except ValueError:
        return None


async def _run(command: tuple[str, ...]) -> str:
    """One short command's output, or "" when it cannot run or does not answer."""
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            list(command),
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode(errors="ignore")


def _is_wsl() -> bool:
    return "microsoft" in platform.release().lower()
