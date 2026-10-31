"""The face server: serves face.html, the models and the animations, and relays events to it.

POST /event {"type": "state", "value": "thinking"} -> every open page.
GET /events is the Server-Sent Events stream the page listens to.
GET /actions lists every clip she can play, with its pool, what it looks like and how long it takes.
GET /model says which model (avatar) is showing: {"chosen": the one she last chose, remembered in
<home>/model so a restart does not put the folder's first name back, or null; "showing": the models
the open pages show}. A page loads the chosen one, and switches to it when the event arrives.
GET /<name>.json is that model's notes, if it has any: {"covers": ["chest", "hips", "thighs"]}
says what her outfit covers, so a close look there is not a shy one.
GET /snapshot is a PNG of the window as the user sees it now (an open page sends it back to
POST /snapshot?id=...); its X-Face header says what she is doing, as JSON.
GET /view is what the user last turned the camera to (a page's {"type": "view", "part": "chest",
"side": "front", "close": true, "shy": true, ...} event, kept, not relayed), with "n" counting them.
GET /music says whether she dances and whether a choice is holding that answer: {"playing": bool,
"choice": true, false or null}. A {"type": "music", "value": true|false} event is such a choice,
and "auto" hands the question back to what the system plays.
GET /autonomy is what she agreed to do on her own, one switch per thing she does without being
asked: {"dance": bool, "blush": bool, "emotion": bool, "greeting": bool}. A {"type": "autonomy",
"value": {"dance": false}} event sets any of them and is written to <home>/autonomy.json, so a
restart does not quietly undo it; a switch she has never touched sits at its default — `dance` off,
the rest on (see AUTONOMY_DEFAULTS).
A {"type": "blink", "eyes": "both"|"left"|"right", "seconds": 0.2} event is a blink she chose; it is
relayed like a pose and not remembered, because a blink that arrived while a page was reloading has
already happened.
Stdlib only; run with `python3 server.py` or `zrb-avatar serve`, then open http://localhost:7777.

Models and animations are looked up in folders, the first match winning:
  models:      $<PREFIX>_AVATAR_MODELS..., <home>/models
  animations:  $<PREFIX>_AVATAR_ANIMS...,  <home>/anim, the ones shipped in this package
The shipped clips are copied into <home>/anim on the first run, with a manifest beside them
(.template.json) recording what was copied: so the folder that plays is the one you can edit.
A copy follows the package while it is untouched, an edit is kept, and a deletion is recorded
and honoured by the pool listing -- see seed_anims, and `zrb-avatar anim reset` to undo.
<home> is $<PREFIX>_AVATAR_HOME, ~/.zrb/avatar by default. <PREFIX> is ZRB unless zrb is
white-labeled: it is read from the bare `_ZRB_ENV_PREFIX`, the same variable zrb takes its
own settings prefix from, so a distribution running under another label needs no code change
here. The variables take os.pathsep-separated lists. An animation is <folder>/<pool>/<name>.vrma:
the pool (idle, thinking, dance, actions, ...)
says when it plays. Pools nest (actions/greetings/hi.vrma is in the pool actions/greetings), and a
nested pool's clips play with its top pool's.

With ZRB_AVATAR_MUSIC=auto (the default) the server watches the system's media player status
(Windows' media controls, also from WSL; playerctl on Linux; on macOS `media-control` if you
`brew install media-control`, else Spotify or Music by AppleScript, which asks for permission
to control them the first time) and sends {"type": "music"} when something starts or stops
playing, so she dances. `media-control` is the better one to install: it also sees a browser
tab playing YouTube, which AppleScript cannot.
"""

import base64
import hashlib
import json
import os
import platform
import queue
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path

# zrb spells its own settings <ENV_PREFIX>_*, and reads that prefix from the bare
# `_ZRB_ENV_PREFIX` -- the bootstrap key in zrb's config, deliberately unprefixed so it
# cannot depend on itself. Reading the same variable is what lets a white-labeled zrb
# (one whose prefix is not ZRB) point this server at its own settings, and the server
# still never imports zrb.
PREFIX_VAR = "_ZRB_ENV_PREFIX"
DEFAULT_PREFIX = "ZRB"


def env_name(setting: str, prefix: str | None = None) -> str:
    """The variable that holds an avatar setting: `HOME` -> `ZRB_AVATAR_HOME`."""
    return f"{prefix or os.getenv(PREFIX_VAR) or DEFAULT_PREFIX}_AVATAR_{setting}"


def env_get(setting: str, default: str = "", prefix: str | None = None) -> str:
    """An avatar setting's value, spelled with the prefix zrb is running under."""
    return os.getenv(env_name(setting, prefix), default)


HERE = Path(__file__).parent
HOME = Path(os.path.expanduser(env_get("HOME", "~/.zrb/avatar")))


def _dirs(variable: str, sub: str, *bundled: Path) -> list[Path]:
    listed = [
        Path(os.path.expanduser(p))
        for p in os.getenv(variable, "").split(os.pathsep)
        if p
    ]
    return [*listed, HOME / sub, *bundled]


MODEL_DIRS = _dirs(env_name("MODELS"), "models")
ANIM_DIRS = _dirs(env_name("ANIMS"), "anim", HERE / "anim")
NAME = re.compile(r"[\w .()-]+")  # a file name without its extension; no slashes
POOL = re.compile(r"[\w-]+")
MAX_EVENT_BYTES = 65536
MAX_SNAPSHOT_BYTES = 16_000_000  # a PNG as a data: URL, plus what she is doing
SNAPSHOT_SECONDS = 6.0  # how long GET /snapshot waits for a page to answer
AUTONOMY = (
    "dance",
    "blush",
    "emotion",
    "greeting",
)  # what she may do without being asked
AUTONOMY_FILE = (
    HOME / "autonomy.json"
)  # where her answer lives, so a restart does not undo it
MODEL_FILE = (
    HOME / "model"
)  # the one she chose, so a restart does not put the first name back
# The shipped clips are copied here on the first run, so the folder you edit is the one that
# plays. The manifest beside them records what was copied and from which version, which is how
# a clip you have not touched can follow the package while one you edited stays yours.
ANIM_HOME = HOME / "anim"
TEMPLATE_FILE = ANIM_HOME / ".template.json"
SHIPPED_ANIMS = HERE / "anim"
# Each switch's starting value, and what a page assumes before the server says anything. Only
# `dance` starts off: the other three trigger on something the user did, while `dance` triggers on
# the media watcher, which cannot tell a song from a talk.
AUTONOMY_DEFAULTS = {"dance": False, "blush": True, "emotion": True, "greeting": True}


def _on(variable: str) -> bool:
    """Whether an env var says yes. Unset, empty, or a word that means no all say no."""
    return os.getenv(variable, "").strip().lower() not in ("", "0", "off", "no", "false")


# Her body answers, on its own, a look that has become one she has said no to. It rides the round
# trip the view event is already making, because a reply takes seconds and a boundary cannot wait
# for one. Off unless asked for: an install should not start refusing looks before whoever runs it
# has decided that is what they want.
REFLEX = _on(env_name("REFLEX"))
# And this is what she does about it: crossed forearms over chest and hips, her face turned away,
# shoulders in. A held pose rather than a clip, because nothing in the animation pool covers her.
COVER = {"leftHand": "chest", "rightHand": "hips", "head": "away", "body": "hunch"}

_clients: set[queue.Queue] = set()
_lock = threading.Lock()
# The media watcher runs in a thread of its own and `zrb-avatar music on|off|auto` arrives on a
# thread per request (the server is threaded), and both change the same two values below. So they
# change them under one lock — this one, then `_lock` for the broadcast, and never the other way
# round — because "the choice decides" is only true if no answer can land between the check and
# the write.
_music_lock = threading.Lock()
# One autonomy change at a time, across the state, the file and the rollback: two posts arriving
# together would otherwise share `autonomy.json.new`, and one would fail on the other's rename --
# reported as "could not remember that" for perfectly good input. Order: this one, then `_lock`.
_autonomy_lock = threading.Lock()
_music = {"type": "music", "value": False}  # the latest, for pages that connect later
_system = {
    "playing": False
}  # what the media watcher last saw, whatever a choice is doing
_music_choice: bool | None = None  # `zrb-avatar music on|off`: holds until `music auto`
# One model change at a time, across the state, the file and the rollback, for the reason the
# autonomy lock exists: two posts arriving together would otherwise share `model.new`. Order:
# this one, then `_lock`.
_model_lock = threading.Lock()
_model = {
    "type": "model",
    "value": None,
}  # the chosen one; load_model() fills it in from the last run
_showing: dict[queue.Queue, str] = (
    {}
)  # each open page's model, as it says when it connects
_view: dict = {"n": 0}  # the latest view event, for zrb to react to
# The page fires its view events without waiting for any of them, and this server answers each on
# its own thread, so two can be in flight and land in either order. The lock makes the comparison
# and the write one step, so "nothing older than what we have decides anything" is true rather
# than likely.
_view_lock = threading.Lock()
_pose: dict = (
    {}
)  # the pose she holds ({"hold": true, ...}), for pages that connect later
_autonomy: dict = dict(AUTONOMY_DEFAULTS)  # what she agreed to; see load_autonomy()
_snapshots: dict[str, dict] = (
    {}
)  # id -> {"done": Event, "image": bytes, "about": dict}, while asked for

# Prints "Playing" or "Paused" whenever any media session (a browser, Spotify, ...) changes.
_WINDOWS_MEDIA = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | select -First 1
$type = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$manager = $asTask.MakeGenericMethod($type).Invoke($null, @($type::RequestAsync())).Result
$last = ''
while ($true) {
  $now = if (@($manager.GetSessions() | ? { $_.GetPlaybackInfo().PlaybackStatus -eq 'Playing' }).Count) { 'Playing' } else { 'Paused' }
  if ($now -ne $last) { [Console]::Out.WriteLine($now); [Console]::Out.Flush(); $last = $now }
  Start-Sleep -Seconds 2
}
"""


_MACOS_MEDIA = """
set out to "Paused"
if application "Spotify" is running then
  try
    tell application "Spotify"
      if player state is playing then set out to "Playing"
    end tell
  end try
end if
if application "Music" is running then
  try
    tell application "Music"
      if player state is playing then set out to "Playing"
    end tell
  end try
end if
return out
"""  # `is running` never starts the app; `playing` needs the tell block to be in scope


def music_command() -> list[str] | None:
    """A command printing a line with "Playing" whenever media starts, and another line when it stops."""
    if sys.platform == "win32" or "microsoft" in platform.uname().release.lower():
        return ["powershell.exe", "-NoProfile", "-Command", _WINDOWS_MEDIA]
    if sys.platform.startswith("linux"):
        return ["playerctl", "--follow", "status"]
    return None  # macOS: watch_macos_music() asks osascript instead


def set_music(playing: bool) -> None:
    """Remember the music state, and tell every open page if it changed.

    A `zrb-avatar music on|off` holds that answer: the watcher keeps reporting what the
    system plays, and only `zrb-avatar music auto` lets it be believed again.
    """
    with _music_lock:
        _system["playing"] = playing
        if _music_choice is not None or playing == _music["value"]:
            return
        _music["value"] = playing
        broadcast(json.dumps(_music))


def choose_music(value) -> None:
    """Take `zrb-avatar music on|off|auto`, raising ValueError for anything else.

    `on` and `off` are choices, so they hold; `auto` clears the choice and answers
    with what the system last said, without waiting for the next poll.
    """
    global _music_choice
    if value != "auto" and not isinstance(value, bool):
        raise ValueError('music value must be true, false or "auto"')
    with _music_lock:  # with the watcher's, so neither can undo the other
        _music_choice = None if value == "auto" else value
        _music["value"] = _system["playing"] if value == "auto" else value
        # Never the event itself: "auto" is the CLI's word, and the page knows only true and false.
        broadcast(json.dumps(_music))


def music_state() -> dict:
    """What GET /music answers: whether she dances, and the choice holding that answer."""
    with (
        _music_lock
    ):  # both or neither: a reader never sees a choice beside the answer it overrides
        return {"playing": bool(_music["value"]), "choice": _music_choice}


def autonomy_state() -> dict:
    """What she agreed to do on her own. A switch she has not touched is still its default; see
    AUTONOMY_DEFAULTS for why `dance` is the one that starts off."""
    with _lock:
        return dict(_autonomy)


def choose_autonomy(value) -> None:
    """Take {"dance": false, ...} -- any of AUTONOMY, each true or false -- and remember it.

    A switch is hers to set, and only the switches that are not at their default are written to
    AUTONOMY_FILE. A file holding a snapshot of every switch would record a default as if it were a
    decision, and would make every later default change miss the switches a user never touched. A
    name that is not one of hers is refused rather than ignored: a typo that silently does nothing is
    worse than an error.

    And so is a switch that could not be written down: `ValueError` says why and nothing changes,
    because a state the file disagrees with is worse than no change at all -- the file is what a
    restart will believe. The home folder is made if it is not there yet, which is the usual case
    for a `ZRB_AVATAR_HOME` that only ever held models somewhere else.
    """
    if not isinstance(value, dict):
        raise ValueError(
            f"autonomy takes an object of {' or '.join(AUTONOMY)}: true or false"
        )
    unknown = sorted(set(value) - set(AUTONOMY))
    if unknown:
        raise ValueError(f"no such switch: {', '.join(unknown)}")
    if not all(isinstance(one, bool) for one in value.values()):
        raise ValueError("each autonomy switch is true or false")
    with (
        _autonomy_lock
    ):  # the state, the file and the rollback are one change, not three
        with _lock:
            before = dict(_autonomy)
            _autonomy.update(value)
            # Every switch goes to the pages, because they merge what they are told: a switch that
            # moved back to its default has to be named. The file gets only what she moved.
            whole = dict(_autonomy)
            saved = {
                name: one
                for name, one in whole.items()
                if one != AUTONOMY_DEFAULTS[name]
            }
        try:
            AUTONOMY_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Written beside it and moved into place, so a reader never sees half a file: the server
            # is threaded, and a crash mid-write would leave one for the next start to read.
            half = AUTONOMY_FILE.with_name(AUTONOMY_FILE.name + ".new")
            half.write_text(json.dumps(saved), encoding="utf-8")
            half.replace(AUTONOMY_FILE)
        except OSError as e:
            with (
                _lock
            ):  # not remembered is not taken: the answer stays what the file says
                _autonomy.clear()
                _autonomy.update(before)
            raise ValueError(f"could not remember that ({e}): nothing changed") from e
        broadcast(json.dumps({"type": "autonomy", "value": whole}))


def load_autonomy() -> None:
    """Read her switches at start. Missing, unreadable, or not what we wrote: the defaults."""
    try:
        saved = json.loads(AUTONOMY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    with _lock:
        for name in AUTONOMY:
            if isinstance(saved.get(name), bool):
                _autonomy[name] = saved[name]


def choose_model(value) -> None:
    """Take a model name she has, remember it, and tell every open page.

    The choice is written to MODEL_FILE for the reason the autonomy switches are: without it, a
    restart puts back the first name in the folder — alphabetical order, not anybody's decision.
    A name that is not in the folders is refused rather than ignored, and so is one that could not
    be written down: the file is what a restart will believe, so a state it disagrees with is worse
    than no change at all.
    """
    if not isinstance(value, str) or value not in models():
        raise ValueError("no such model")
    with _model_lock:  # the state, the file and the rollback are one change, not three
        with _lock:
            before = _model["value"]
            _model["value"] = value
        try:
            MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Written beside it and moved into place, so a reader never sees half a name.
            half = MODEL_FILE.with_name(MODEL_FILE.name + ".new")
            half.write_text(value, encoding="utf-8")
            half.replace(MODEL_FILE)
        except OSError as e:
            with (
                _lock
            ):  # not remembered is not taken: the answer stays what the file says
                _model["value"] = before
            raise ValueError(f"could not remember that ({e}): nothing changed") from e
        broadcast(json.dumps(_model))


def load_model() -> None:
    """Read the model she last chose at start.

    Missing, unreadable, or a name whose `.vrm` is gone: none, and a page falls back to the folder's
    first model exactly as it did before this existed. A file is a memory, not an instruction — a
    name she no longer has is not worth an error at startup.

    Only the line ending is trimmed. A model file may carry spaces around its name (`NAME` allows
    them), and stripping those would read back a name that no longer resolves.
    """
    try:
        saved = MODEL_FILE.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        # UnicodeError too: bytes that are not UTF-8 -- a half-written or synced-over file -- are
        # "unreadable" as much as a missing one, and must not stop the server from starting.
        return
    if saved in models():
        with _lock:
            _model["value"] = saved


def watch_music(command: list[str]) -> None:
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True
        )
    except OSError as e:
        print(f"not watching music: {e}")
        return
    for line in process.stdout:
        set_music("Playing" in line)


def _media_control_state(run, timeout: float) -> str | None:
    """Ask `media-control get --no-artwork` what macOS is playing: "Playing", "Paused", or None
    when it is not installed, exits badly, or answers something we can't read (so we fall back
    to AppleScript). A literal `null` is not a failure — it is the app saying nothing is playing.
    """
    path = shutil.which("media-control")
    if path is None:
        return None
    try:
        done = run(
            [path, "get", "--no-artwork"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    out = done.stdout.strip()
    if not out:
        return None
    try:
        payload = json.loads(out)
    except ValueError:
        return None
    if payload is None:
        return "Paused"
    if not isinstance(
        payload, dict
    ):  # valid JSON but not the object we know: fall back
        return None
    playing = payload.get(
        "playing"
    )  # what media-control prints: {"playing": true, "title": ...}
    if playing is None:
        playing = payload.get("is_playing")
    if playing is None:  # no flag at all: going by the rate, which is 0 while paused
        rate = payload.get("playbackRate")
        if not isinstance(rate, (int, float)):
            return None
        playing = rate > 0
    return "Playing" if playing else "Paused"


def macos_media(run=subprocess.run, timeout: float = 5) -> str | None:
    """What the Mac is playing, "Playing" or "Paused"; None while we can't tell (so the watcher
    says nothing this try). We prefer `media-control` (brew install media-control), which reads
    every player — Spotify, Music, and a browser tab — through one API; when it is missing or
    unhappy we fall back to AppleScript on Spotify/Music, which macOS asks permission to control
    the first time, so a slow answer must not hang us."""
    state = _media_control_state(run, timeout)
    if state is not None:
        return state
    try:
        done = run(
            ["osascript", "-e", _MACOS_MEDIA],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def watch_macos_music() -> None:
    """Ask osascript every couple of seconds; while it can't tell us, say nothing. The first
    look waits longer, for the permission macOS asks for the first time."""
    first = True
    while True:
        state = macos_media(timeout=60 if first else 5)
        if state is None and first:
            print(
                "not watching music: allow this app to control Spotify or Music "
                "(System Settings > Privacy & Security > Automation)"
            )
        first = False
        if state is not None:
            set_music(state == "Playing")
        time.sleep(2)


def models() -> dict[str, Path]:
    """Every <name>.vrm in MODEL_DIRS; an earlier folder wins."""
    found = {}
    for folder in reversed(MODEL_DIRS):
        found.update(
            {f.stem: f for f in folder.glob("*.vrm") if NAME.fullmatch(f.stem)}
        )
    return found


def model_notes() -> dict[str, Path]:
    """`<name>.json` for every model, looked for beside the `.vrm` that name resolves to.

    Beside *that* one, not beside any model of the same name: with two folders, the earlier `.vrm`
    wins and a `.json` in the later folder belongs to a model nobody is showing — a hoodie's
    `covers` would then hide a bikini's chest, or the reverse.
    """
    beside = {name: vrm.with_suffix(".json") for name, vrm in models().items()}
    return {name: note for name, note in beside.items() if note.is_file()}


def chosen_model() -> str | None:
    """The model a model event picked, while it is still in the folders."""
    return _model["value"] if _model["value"] in models() else None


def showing() -> dict:
    """What GET /model answers: the chosen model, and the models the open pages show."""
    with _lock:
        shown = sorted(set(_showing.values()))
    return {"chosen": chosen_model(), "showing": shown}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version() -> str:
    """The version the copies came from, so `zrb-avatar anim` can say what it holds.

    Read from pyproject.toml when it sits beside the package: an editable install keeps the
    version it was installed at, so its metadata goes stale the moment the version is bumped
    (and a checkout is exactly where this runs while the package is being worked on).
    """
    project = HERE.parent / "pyproject.toml"
    try:
        for line in project.read_text(encoding="utf-8").splitlines():
            if line.startswith("version = "):
                return line.split('"')[1]
    except (OSError, IndexError):
        pass
    try:
        return metadata.version("zrb-avatar")
    except metadata.PackageNotFoundError:
        return ""


def _template() -> dict:
    """What was copied into the home folder: {"version", "files": {path: sha}, "deleted": [...]}.

    A manifest that is missing or unreadable reads as empty, which makes every file in the
    folder look like the user's own -- the one failure that keeps a clip instead of replacing
    it, so nothing is ever overwritten on the strength of a file we could not read.
    """
    try:
        saved = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
        files = saved["files"]
        deleted = saved.get("deleted") or []
        return {
            "version": str(saved.get("version", "")),
            "files": {str(k): str(v) for k, v in files.items()},
            "deleted": {str(rel) for rel in deleted},
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"version": "", "files": {}, "deleted": set()}


def _write_template(files: dict[str, str], deleted: set[str]) -> None:
    TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    half = TEMPLATE_FILE.with_name(TEMPLATE_FILE.name + ".new")
    half.write_text(
        json.dumps(
            {
                "version": _package_version(),
                "files": files,
                "deleted": sorted(deleted),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    half.replace(TEMPLATE_FILE)


def _sync_one(rel: str, shipped: Path, known: dict[str, str], notes: list[str]) -> str | None:
    """Bring one shipped clip into the home folder; returns the digest to record for it.

    None means "record nothing", and that is how an edited clip is remembered: with no digest
    on file, no later run can mistake it for untouched and overwrite it.
    """
    target = ANIM_HOME / rel
    want = _digest(shipped)
    copied = known.get(rel)
    have = _digest(target) if target.is_file() else None
    if have is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shipped, target)
        notes.append(f"copied {rel}")
        return want
    if copied == have:  # still byte for byte what was copied
        if have != want:
            shutil.copy2(shipped, target)
            notes.append(f"updated {rel}")
        return want
    if copied is None and have == want:  # identical to the package: nothing to keep apart
        return want
    notes.append(f"kept {rel} (edited)")
    return None


def _retire(shipped: dict[str, Path], known: dict[str, str], restore: bool, notes: list[str]):
    """Clips the package no longer ships: gone when untouched and `restore`, else kept."""
    kept: dict[str, str] = {}
    for rel, copied in sorted(known.items()):
        if rel in shipped:
            continue
        target = ANIM_HOME / rel
        untouched = target.is_file() and _digest(target) == copied
        if untouched and restore:
            target.unlink()
            notes.append(f"removed {rel} (no longer shipped)")
        elif untouched:
            kept[rel] = copied
            notes.append(f"kept {rel} (no longer shipped; `zrb-avatar anim reset` removes it)")
        else:
            notes.append(f"kept {rel} (edited, no longer shipped)")
    return kept


def _skipped(
    rel: str, target: Path, known: dict, deleted: set[str], restore: bool, notes: list[str]
) -> bool:
    """True when a clip is left out of the copy pass, because the user deleted it.

    A clip we copied once and that is now gone was deleted on purpose, so it is recorded as
    deleted instead of being brought back; a clip the manifest has never seen is copied, which
    is how a release that adds an animation still delivers it. `restore` is the way back.
    """
    if target.is_file():  # back by hand: no longer deleted
        deleted.discard(rel)
        return False
    if restore:
        deleted.discard(rel)
        return False
    if rel in deleted or known.get(rel) is not None:
        deleted.add(rel)
        notes.append(f"kept {rel} deleted (bring it back with `zrb-avatar anim reset`)")
        return True
    return False


def seed_anims(restore: bool = False, source: Path | None = None) -> list[str]:
    """Copy the shipped clips into the home folder, and keep the copies level with the package.

    The folder is yours: delete a clip, or replace one with an animation of your own. So a copy
    is replaced only while it is still what was copied, one the manifest does not know is never
    touched -- a file that predates the manifest may be work of yours -- and one you delete
    stays deleted.

    `restore` -- `zrb-avatar anim reset` -- puts back the clips you deleted and removes ones
    that no longer ship, and still keeps every edit. Starting the server only adds and updates.
    Returns one line per change, for the caller to print.
    """
    source = source or SHIPPED_ANIMS
    # as_posix(): the manifest is keyed by "<pool>/<name>.vrma" on every platform, so a home
    # folder keeps working when it is looked at from another one (and Windows, which would
    # otherwise key it with backslashes).
    shipped = {p.relative_to(source).as_posix(): p for p in source.rglob("*.vrma")}
    if not shipped:
        return []
    template = _template()
    known, deleted = template["files"], set(template["deleted"])
    files: dict[str, str] = {}
    notes: list[str] = []
    for rel, path in sorted(shipped.items()):
        if _skipped(rel, ANIM_HOME / rel, known, deleted, restore, notes):
            continue
        digest = _sync_one(rel, path, known, notes)
        if digest is not None:
            files[rel] = digest
    files.update(_retire(shipped, known, restore, notes))
    changed = (
        files != known
        or deleted != template["deleted"]
        or template["version"] != _package_version()
    )
    if changed:
        _write_template(files, deleted)
    return notes


def anim_report() -> list[str]:
    """What the home folder holds and which clips are the user's own: `zrb-avatar anim`."""
    if not ANIM_HOME.is_dir():
        return [f"{ANIM_HOME} does not exist yet; every clip is read from {SHIPPED_ANIMS}"]
    known = _template()
    mine, copied = [], []
    for path in sorted(ANIM_HOME.rglob("*.vrma")):
        rel = path.relative_to(ANIM_HOME).as_posix()
        (copied if known["files"].get(rel) == _digest(path) else mine).append(rel)
    lines = [
        f"{ANIM_HOME} (copied from {known['version'] or 'an unknown version'})",
        f"  {len(copied)} as shipped, {len(mine)} your own, {len(known['deleted'])} deleted",
    ]
    return (
        lines
        + [f"  yours: {rel}" for rel in mine]
        + [f"  deleted: {rel}" for rel in sorted(known["deleted"])]
    )


def anims() -> dict[str, dict[str, Path]]:
    """pool -> name -> file, for every <pool>/<name>.vrma in ANIM_DIRS, the pool being the folders
    on the way ("idle", "actions/greetings"); an earlier folder wins, file by file.

    A clip the manifest records as deleted is left out wherever it is found: the package still
    ships the copy you deleted, and quietly serving that one would make deleting a clip from
    the home folder mean nothing."""
    deleted = _template()["deleted"]
    found: dict[str, dict[str, Path]] = {}
    for folder in reversed(ANIM_DIRS):
        for f in folder.rglob("*.vrma"):
            pool = f.parent.relative_to(folder).parts
            if (
                pool
                and all(POOL.fullmatch(part) for part in pool)
                and NAME.fullmatch(f.stem)
                and "/".join((*pool, f.name)) not in deleted
            ):
                found.setdefault("/".join(pool), {})[f.stem] = f
    return found


def clip_info(file: Path) -> dict:
    """What a .vrma says about itself: its description (the generators write one into the
    animation's glTF extras; a file of your own may have none) and its length in seconds. Each
    is "" or None when the file doesn't say, or says something of the wrong shape: a file of
    your own must not break the listing of the rest."""
    info = {"description": "", "seconds": None}
    try:
        with open(file, "rb") as f:
            head = f.read(20)
            size = struct.unpack_from("<I", head, 12)[0]
            gltf = json.loads(f.read(size))
        animation = gltf["animations"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
        return info
    if not isinstance(animation, dict):
        return info
    extras = animation.get("extras")
    if isinstance(extras, dict) and isinstance(extras.get("description"), str):
        info["description"] = extras["description"]
    try:
        seconds = gltf["accessors"][animation["samplers"][0]["input"]]["max"][0]
    except (KeyError, IndexError, TypeError):
        return info
    if (
        isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and 0 <= seconds < float("inf")
    ):
        info["seconds"] = round(float(seconds), 1)
    return info


def clips() -> list[dict]:
    """Every clip, as {"pool", "name", "description", "seconds"}, read from the folders now:
    the actions first (what an agent plays most), then the other pools."""
    return [
        {"pool": pool, "name": name, **clip_info(file)}
        for pool, names in sorted(
            anims().items(),
            key=lambda item: (item[0].split("/")[0] != "actions", item[0]),
        )
        for name, file in sorted(names.items())
    ]


def playable() -> dict[str, dict]:
    """Every name a clip can be asked for by (`zrb-avatar action`, zrb's AvatarAction) -> the clip:
    its full "pool/name", and for an action (pool actions, or nested under it) also the name
    without "actions/" ("wave", "greetings/hi") -- but only where no full name is the same, so
    actions/thinking/idea is "actions/thinking/idea", never the loop clip "thinking/idea".
    """
    found = clips()
    names = {f"{clip['pool']}/{clip['name']}": clip for clip in found}
    for clip in found:
        if clip["pool"] == "actions" or clip["pool"].startswith("actions/"):
            names.setdefault(
                f"{clip['pool']}/{clip['name']}".removeprefix("actions/"), clip
            )
    return names


def short_name(clip: dict, names: dict[str, dict]) -> str:
    """The name to show for *clip*: an action's short name where it means this clip, else the
    full pool/name."""
    full = f"{clip['pool']}/{clip['name']}"
    short = full.removeprefix("actions/")
    return short if short != full and names.get(short) is clip else full


def take_snapshot(timeout: float = SNAPSHOT_SECONDS) -> tuple[bytes, dict] | None:
    """Ask the open pages for a picture of the window; the first to answer wins. None when no
    page is open or none answers in time."""
    with _lock:
        if not _clients:
            return None
    key = secrets.token_hex(8)
    waiting = _snapshots[key] = {"done": threading.Event(), "image": b"", "about": {}}
    try:
        broadcast(json.dumps({"type": "snapshot", "id": key}))
        if not waiting["done"].wait(timeout):
            return None
        return waiting["image"], waiting["about"]
    finally:
        _snapshots.pop(key, None)


def _send_all(message: str) -> None:
    """Put one line in every open page's queue. The caller holds `_lock`."""
    for client in _clients:
        client.put(message)


def broadcast(message: str) -> None:
    with _lock:
        _send_all(message)


def _accepts_view(event: dict) -> bool:
    """Whether this view is newer than the one applied last, so a late older one decides nothing.

    The page sends each settled view without awaiting the last, and this server answers every
    request on a thread of its own: a refused close-up followed quickly by an ordinary view can
    arrive the other way round, and the older event would then be the one that decides her body —
    covered in front of a view she never refused. `run` is made once when the page loads and `seq`
    counts within it, so a reloaded page outranks anything the previous one said, and a page that
    says neither (an older page, a reload still in flight) is taken as it comes, which is what
    every page did before this. The caller holds `_view_lock`.
    """
    run, seq = event.get("run"), event.get("seq")
    if not isinstance(run, str) or not isinstance(seq, int):
        return True
    seen_run, seen_seq = _view.get("run"), _view.get("seq")
    if run != seen_run or not isinstance(seen_seq, int):
        return True
    return seq > seen_seq


def _answer_a_refused_look(shy: bool) -> None:
    """Cover her, or let her go, as the look on her turns into one she has said no to.

    The page has already sent the view, so the answer rides that same round trip. The point of a
    reflex is that it holds when no reply can — zrb busy, out of tokens, or not running at all.

    It is a refusal, not a lock: the user can zoom straight back in, and she covers again. What
    it costs: a reflex wins over a pose zrb was holding, and her own pose is not put back
    afterwards, which is what being quicker than a thought means.
    """
    if not REFLEX:
        return
    with _lock:
        held = bool(_pose.get("reflex"))
        if shy == held:  # the same answer as last time: one pose, not one per event
            return
        _pose.clear()
        if shy:
            _pose.update({**COVER, "type": "pose", "hold": True, "reflex": True})
            _send_all(json.dumps(_pose))
        else:
            _send_all(json.dumps({"type": "pose", "release": True}))


def _replay(client: queue.Queue, chosen: str | None) -> None:
    """What a page that just connected has missed: the music, the model, a pose she is holding.

    The caller holds `_lock`, together with adding `client` to the pages that hear what comes
    next, because a pose (or a release) arriving between those two would be in neither — the page
    would come up free while she holds, or holding what she has already let go of.
    """
    for message in (
        _music if _music["value"] else None,
        _model if chosen else None,
        _pose or None,
    ):
        if message:
            client.put(json.dumps(message))
    # Only when she has moved a switch away from its default: a page assumes the defaults, so when
    # that is the truth there is nothing to tell it. The caller holds the lock, so this is the
    # dict, not a fresh read.
    if _autonomy != AUTONOMY_DEFAULTS:
        client.put(json.dumps({"type": "autonomy", "value": dict(_autonomy)}))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.unquote(self.path.split("?")[0])
        if path == "/events":
            return self._stream()
        if path == "/models":  # the model names, for the page's default
            return self._json(sorted(models()))
        if path == "/model":
            return self._json(showing())
        if path == "/anims":
            return self._json({pool: sorted(names) for pool, names in anims().items()})
        if path == "/actions":
            return self._json(clips())
        if path == "/view":
            return self._json(_view)
        if path == "/music":
            return self._json(music_state())
        if path == "/autonomy":
            return self._json(autonomy_state())
        if path == "/snapshot":
            shot = take_snapshot()
            if shot is None:
                return self.send_error(503, "no window open, or it did not answer")
            image, about = shot
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.send_header("X-Face", json.dumps(about, ensure_ascii=True))
            self.end_headers()
            self.wfile.write(image)
            return
        if path == "/":
            return self._send(
                (HERE / "face.html").read_bytes(), "text/html; charset=utf-8"
            )
        # Files are only ever looked up by name in the listings above, never joined from the URL.
        parts = path[1:].split("/")
        if (
            len(parts) == 2
            and parts[0] == "js"
            and parts[1].endswith(".js")
            and NAME.fullmatch(parts[1][:-3])
        ):
            file = HERE / "js" / parts[1]  # the page's modules
            if file.is_file():
                return self._send(file.read_bytes(), "text/javascript; charset=utf-8")
            file = None
        elif len(parts) == 1 and path.endswith(".vrm"):
            file = models().get(parts[0][: -len(".vrm")])
        elif len(parts) == 1 and path.endswith(".json"):
            file = model_notes().get(parts[0][: -len(".json")])
        elif len(parts) >= 3 and parts[0] == "anim" and path.endswith(".vrma"):
            file = (
                anims().get("/".join(parts[1:-1]), {}).get(parts[-1][: -len(".vrma")])
            )
        else:
            file = None
        if file is None:
            return self.send_error(404)
        self._send(file.read_bytes(), "application/octet-stream")

    def _length(self, limit: int) -> int | None:
        """The request body's length, or None after answering 400 (not a number, negative) or
        413 (over *limit*): never read(-1), which would wait for the client to hang up.
        """
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0:
            self.send_error(400, "bad Content-Length")
            return None
        if size > limit:
            self.send_error(413)
            return None
        return size

    def _json(self, value) -> None:
        self._send(json.dumps(value).encode(), "application/json")

    def _send(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.startswith("/snapshot?"):
            return self._snapshot()
        if self.path != "/event":
            return self.send_error(404)
        size = self._length(MAX_EVENT_BYTES)
        if size is None:
            return
        try:
            event = json.loads(self.rfile.read(size))
        except ValueError:
            return self.send_error(400)
        if (
            isinstance(event, dict) and event.get("type") == "view"
        ):  # for zrb, not the other pages
            with _view_lock:
                newer = _accepts_view(event)
                if newer:
                    _view.update(event, n=_view["n"] + 1)
            if newer:  # a view that arrives after a newer one was seen says nothing at all
                _answer_a_refused_look(bool(event.get("shy")))
            self.send_response(204)
            self.end_headers()
            return
        if (
            isinstance(event, dict) and event.get("type") == "music"
        ):  # `zrb-avatar music on|off|auto`
            try:
                choose_music(
                    event.get("value")
                )  # tells the pages itself, under the lock
            except ValueError as e:
                return self.send_error(400, str(e))
            self.send_response(204)
            self.end_headers()
            return
        if (
            isinstance(event, dict) and event.get("type") == "autonomy"
        ):  # hers: what she agrees to
            try:
                choose_autonomy(
                    event.get("value")
                )  # tells the pages itself, and writes the file
            except ValueError as e:
                return self.send_error(400, str(e))
            self.send_response(204)
            self.end_headers()
            return
        if (
            isinstance(event, dict) and event.get("type") == "model"
        ):  # remembered; only a model we have
            try:
                choose_model(
                    event.get("value")
                )  # tells the pages itself, and writes the file
            except ValueError as e:
                return self.send_error(400, str(e))
            self.send_response(204)
            self.end_headers()
            return
        message = json.dumps(
            event
        )  # re-dumped so it is one line, as an SSE data field must be
        with _lock:
            if (
                isinstance(event, dict) and event.get("type") == "pose"
            ):  # a held pose outlives a reload
                _pose.clear()  # a timed pose, like a release, replaces whatever was being held
                if event.get("hold"):
                    _pose.update(event)
            _send_all(message)
        self.send_response(204)
        self.end_headers()

    def _snapshot(self):
        """A page's answer to a snapshot request: {"image": "data:image/png;base64,...",
        "about": {...}}. Only for an id that is being waited for."""
        key = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "id", [""]
        )[0]
        waiting = _snapshots.get(key)
        if waiting is None:
            return self.send_error(404)
        size = self._length(MAX_SNAPSHOT_BYTES)
        if size is None:
            return
        try:
            answer = json.loads(self.rfile.read(size))
            header, data = answer["image"].split(",", 1)
            if header != "data:image/png;base64":
                raise ValueError(header)
            image = base64.b64decode(data, validate=True)
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.send_error(400)
        if not waiting["done"].is_set():
            about = answer.get("about")
            waiting["image"], waiting["about"] = image, (
                about if isinstance(about, dict) else {}
            )
            waiting["done"].set()
        self.send_response(204)
        self.end_headers()

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        client: queue.Queue = queue.Queue()
        # The page says which model it shows (/events?model=<name>), for GET /model. A page that
        # named one asked for it -- `zrb-avatar <model>` opens the window with it -- so the remembered
        # model is replayed only to a page that did not ask. Otherwise the page loads what it was
        # told and is switched a moment later to what was remembered, and the argument is lost.
        shows = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "model", [""]
        )[0]
        chosen = None if shows else chosen_model()
        # What it missed, and joining the pages that hear what comes next, are one step: a held pose
        # (or a release) arriving between them would be in neither, and a reload would stand her up
        # in the middle of a thought — or bring back a posture she had already let go of.
        with _lock:
            _clients.add(client)
            if shows:
                _showing[client] = shows
            _replay(client, chosen)
        try:
            while True:
                try:
                    line = f"data: {client.get(timeout=15)}\n\n"
                except queue.Empty:
                    line = ": ping\n\n"  # notices a closed page
                self.wfile.write(line.encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with _lock:
                _clients.discard(client)
                _showing.pop(client, None)

    def log_message(self, format, *args):
        pass


def serve(host: str, port: int) -> ThreadingHTTPServer:
    load_autonomy()  # what she agreed to, from the last run
    load_model()  # the model she chose last, from the same run
    try:
        notes = seed_anims()  # the shipped clips, into the folder you edit
    except OSError as e:  # a home that cannot be written must not stop her from starting
        print(f"animations: left where they were ({e})")
    else:
        for note in notes:
            print(f"animations: {note}")
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    host = env_get("HOST", "127.0.0.1")
    port = int(env_get("PORT", "7777"))
    print(f"face on http://localhost:{port}")
    if env_get("MUSIC", "auto") == "auto":
        if command := music_command():
            threading.Thread(target=watch_music, args=(command,), daemon=True).start()
        elif sys.platform == "darwin":
            threading.Thread(target=watch_macos_music, daemon=True).start()
    serve(host, port).serve_forever()


if __name__ == "__main__":
    main()
