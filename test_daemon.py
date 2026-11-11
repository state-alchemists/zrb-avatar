"""The face server's detach, its pid file, and saying whether it is up.

Run with `python test_daemon.py`, like `test_face.py`: this repository's tests are scripts
whose last word is `ok`, not pytest modules.

The detach cases spawn real processes -- a mock would prove `Popen` was called and nothing
about whether the child outlives the caller, which is the only thing being claimed.
"""

import contextlib
import io
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from zrb_avatar import __main__ as cli
from zrb_avatar import daemon


def gone(pid: int, seconds: float = 5.0) -> bool:
    """Whether *pid* goes away within *seconds*, collecting it first where that is our job.

    A child that has exited but not been collected still answers signal 0. Linux's
    `running()` reads `/proc` and sees the `Z`; macOS has no `/proc`, so this process --
    which is the parent, and therefore the one that should reap -- collects it here.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.name != "nt":
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    return True
            except ChildProcessError:
                return True
        if not daemon.running(pid):
            return True
        time.sleep(0.05)
    return False


def check_detach_gives_the_child_no_terminal() -> None:
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "server.log"
        pid = daemon.detach([sys.executable, "-c", "import time; time.sleep(30)"], log)
        try:
            assert daemon.running(pid), "the detached child is not running"
            assert log.is_file(), "no log file was made"
        finally:
            os.kill(pid, signal.SIGTERM)
        assert gone(pid), "the child outlived the SIGTERM"


def check_the_childs_output_lands_in_the_log() -> None:
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "server.log"
        pid = daemon.detach([sys.executable, "-c", "print('face on 7777')"], log)
        assert gone(pid), "a one-line child is still running"
        assert "face on 7777" in log.read_text(encoding="utf-8")


def check_a_dead_pid_is_not_called_running() -> None:
    with tempfile.TemporaryDirectory() as folder:
        pid = daemon.detach([sys.executable, "-c", "pass"], Path(folder) / "log")
        assert gone(pid)
        assert not daemon.running(pid)
    assert not daemon.running(0)
    assert not daemon.running(-1)


def check_the_pid_file_round_trips() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.pid"
        assert daemon.read_pid(path) is None
        daemon.write_pid(path, 4242)
        assert daemon.read_pid(path) == 4242
        path.write_text("not a number", encoding="utf-8")
        assert daemon.read_pid(path) is None


def check_the_pid_file_is_asked_before_lsof() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.pid"
        daemon.write_pid(path, 4242)
        asked = []

        def lsof(*args, **kwargs):
            asked.append(args)
            return mock.Mock(stdout="111\n")

        with mock.patch.object(cli, "PID", path):
            with mock.patch.object(daemon, "running", lambda pid: True):
                with mock.patch.object(cli.subprocess, "run", lsof):
                    assert cli.old_pids() == [4242]
        assert asked == [], "lsof was asked although the pid file answered"


def check_lsof_is_the_fallback() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.pid"  # never written
        with mock.patch.object(cli, "PID", path):
            with mock.patch.object(
                cli.subprocess, "run", lambda *a, **k: mock.Mock(stdout="111\n222\n")
            ):
                assert cli.old_pids() == [111, 222]


def check_stop_says_when_nothing_runs() -> None:
    with mock.patch.object(cli, "old_pids", lambda: []):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            cli.stop()
    assert "nothing to stop" in printed.getvalue()


def check_status_names_the_pid_and_whether_it_answers() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.pid"
        daemon.write_pid(path, 4242)
        with mock.patch.object(cli, "PID", path):
            with mock.patch.object(daemon, "running", lambda pid: True):
                with mock.patch.object(daemon, "answering", lambda url, timeout=1.0: True):
                    printed = io.StringIO()
                    with contextlib.redirect_stdout(printed):
                        cli.status()
        said = printed.getvalue()
    assert "running as pid 4242" in said, said
    assert "answering" in said, said


def check_status_says_a_stale_pid_file_is_stale() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.pid"
        daemon.write_pid(path, 4242)
        with mock.patch.object(cli, "PID", path):
            with mock.patch.object(daemon, "running", lambda pid: False):
                with mock.patch.object(daemon, "answering", lambda url, timeout=1.0: False):
                    printed = io.StringIO()
                    with contextlib.redirect_stdout(printed):
                        cli.status()
        said = printed.getvalue()
    assert "is gone" in said, said
    assert "not answering" in said, said


for name, check in sorted(globals().items()):
    if name.startswith("check_"):
        check()
print("ok")
