"""zrb-avatar: start the face, or tell it what to do.

zrb-avatar [model] [url]     (re)start the server and open a window showing <model>
zrb-avatar serve             run the server in the foreground
zrb-avatar action <name>     play anim/actions/<name> once; <pool>/<name> plays any clip once
zrb-avatar actions [pool]    list the animations by pool (or one pool), with what each looks like
zrb-avatar snapshot [file]   save the window as the user sees it (zrb-avatar-snapshot.png)
zrb-avatar music on|off|auto dance (anim/dance) while idle, or follow the system again
zrb-avatar models            list the models (avatars), marking the one showing (*)
zrb-avatar model <name>      switch every open window to <name>.vrm, and remember it for the next start
zrb-avatar dirs              where models and animations are looked up
zrb-avatar init-path         the file to add to ZRB_INIT_SCRIPTS
"""

import getpass
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from zrb_avatar import server

PORT = int(os.getenv("ZRB_AVATAR_PORT", "7777"))
LOG = Path(tempfile.gettempdir()) / "zrb-avatar.log"


def post(message: dict) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/event",
        json.dumps(message).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=2).close()
    except OSError as e:
        sys.exit(
            f"the face server isn't running on port {PORT} ({e}); start it with: zrb-avatar"
        )


def window() -> list[str] | None:
    """The window program: ZRB_AVATAR_APP, the Windows build from WSL, the macOS app, or
    Electron from a source checkout. None: open a browser instead."""
    if os.getenv("ZRB_AVATAR_APP"):
        return [os.environ["ZRB_AVATAR_APP"]]
    if "microsoft" in platform.uname().release.lower():
        exe = Path(
            f"/mnt/c/Users/{getpass.getuser()}/AppData/Local/zrb-avatar/zrb-avatar.exe"
        )
        return [str(exe)] if exe.exists() else None
    if sys.platform == "darwin":
        installed = any(
            Path(d, "zrb-avatar.app").exists()
            for d in ("/Applications", Path.home() / "Applications")
        )
        return ["open", "-n", "-a", "zrb-avatar", "--args"] if installed else None
    source = server.HERE.parent / "app"
    electron = source / "node_modules" / ".bin" / "electron"
    return [str(electron), str(source)] if electron.exists() else None


def start(args: list[str]) -> None:
    # Restart the server, so it runs the current code with the current settings.
    # Open windows reconnect and reload by themselves.
    try:
        old = subprocess.run(
            ["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError:
        old = ""  # ponytail: without lsof an old server keeps the port; the new one logs "address in use"
    for pid in old.split():
        os.kill(int(pid), signal.SIGTERM)
    if old:
        time.sleep(0.5)
    with open(LOG, "w") as log:
        subprocess.Popen(
            [sys.executable, str(server.HERE / "server.py")],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    program = window()
    if (
        program
    ):  # detached, or zsh/bash stop it for reading the terminal ("suspended (tty input)")
        subprocess.Popen(
            [*program, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        model = next((a for a in args if not a.startswith("http")), "")
        query = (
            f"?model={urllib.parse.quote(model.removesuffix('.vrm'))}" if model else ""
        )
        print(
            "no desktop app found; opening a browser (see the README to install the app)"
        )
        time.sleep(0.5)  # let the server bind
        webbrowser.open(f"http://localhost:{PORT}/{query}")
    print(f"server log: {LOG}")


def actions(pool: str = "") -> None:
    for clip in server.clips():
        if not pool or clip["pool"] == pool or clip["pool"].startswith(f"{pool}/"):
            name = f"{clip['pool']}/{clip['name']}"
            print(
                f"{name:26} {clip['seconds'] if clip['seconds'] is not None else '?':>5} s  {clip['description']}"
            )


def ask(path: str):
    """The running server's JSON answer to GET <path>."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/{path}", timeout=2
        ) as response:
            return json.loads(response.read())
    except OSError as e:
        sys.exit(
            f"the face server isn't running on port {PORT} ({e}); start it with: zrb-avatar"
        )


def models() -> None:
    names, now = ask("models"), ask("model")
    shown = now["showing"] or [
        now["chosen"] or (names or [""])[0]
    ]  # no window open: what the next one shows
    for name in names:
        print(f"{'*' if name in shown else ' '} {name}")
    if not names:
        print("no models; see: zrb-avatar dirs")


def model(name: str) -> None:
    name = name.removesuffix(".vrm")
    names = ask("models")
    if name not in names:
        sys.exit(f"no model {name!r}; models: {', '.join(names) or 'none'}")
    post({"type": "model", "value": name})


def snapshot(file: str) -> None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/snapshot", timeout=10
        ) as response:
            image, about = response.read(), response.headers.get("X-Face", "")
    except OSError as e:
        sys.exit(f"no snapshot: is the server running and a window open? ({e})")
    Path(file).write_bytes(image)
    print(f"saved {file}  {about}")


def dirs() -> None:
    for title, folders in (
        ("models", server.MODEL_DIRS),
        ("animations", server.ANIM_DIRS),
    ):
        print(f"{title} (first match wins):")
        for folder in folders:
            print(f"  {folder}{'' if folder.is_dir() else '  (missing)'}")
    print("found models:", ", ".join(sorted(server.models())) or "none")
    for pool, names in sorted(server.anims().items()):
        print(f"found {pool}:", ", ".join(sorted(names)))


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    if command in ("-h", "--help", "help"):
        print(__doc__)
    elif command == "serve":
        server.main()
    elif command == "action" and len(args) == 2:
        post({"type": "action", "value": args[1]})
    elif command == "music" and args[1:] in (["on"], ["off"], ["auto"]):
        post(
            {"type": "music", "value": "auto" if args[1] == "auto" else args[1] == "on"}
        )
    elif command == "models" and len(args) == 1:
        models()
    elif command == "model" and len(args) == 2:
        model(args[1])
    elif command == "actions" and len(args) <= 2:
        actions(args[1] if len(args) == 2 else "")
    elif command == "snapshot" and len(args) <= 2:
        snapshot(args[1] if len(args) == 2 else "zrb-avatar-snapshot.png")
    elif command == "dirs":
        dirs()
    elif command == "init-path":
        print(server.HERE / "zrb_init.py")
    elif command in ("action", "music", "model", "models"):
        sys.exit(__doc__)
    else:
        start(args)


if __name__ == "__main__":
    main()
