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
Audio for a page that is the only microphone and the only speaker: POST /audio takes its
microphone (16 kHz mono 16-bit, measured or chunked), and GET /audio/in is that same audio handed
to zrb's dictation; POST /say takes what zrb's speech player made, and GET /audio/out hands it to
the page. Both GETs are one long response of raw frames, never JSON: the headers name the format,
and a sentence made at a new sample rate ends the out stream so the page connects again and reads
that rate rather than playing it at the wrong pitch. Nothing here knows what speech is; audio.py
holds the two queues and the caps on them.
Typed lines take the same shape without the format: POST /text takes one line from the page's text
box, and GET /text/in hands the lines to zrb in the order they were typed, newline-framed UTF-8 with
a blank line every fifteen seconds so a proxy does not end a quiet route. One line is one message,
and typed.py holds the queue and the caps on it.

Stdlib only; run with `python3 server.py` or `zrb-avatar serve`, then open http://localhost:7777.

With ZRB_AVATAR_TOKEN set, every request has to carry that token: `Authorization: Bearer <token>`,
the cookie a first visit with `?token=` sets, or `?token=` itself. Unset -- the default -- nothing
is asked for, because the server only answers this machine. A bind that is not loopback is refused
without a token; auth.py holds the policy and the reasoning behind it.

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
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path

try:  # imported as `zrb_avatar.server`, which is how the tests and every tool see her
    from zrb_avatar import audio, auth, typed
except ImportError:  # run as a script beside the package, which is how `zrb-avatar` starts her
    import audio, auth, typed

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
# The one byte range a page asks for: the rest of a file, from the byte it got to.
RANGE = re.compile(r"bytes=(\d+)-(\d*)")


def asked_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The byte range a request asks for, as inclusive bounds, or None for the whole body.

    Only `bytes=N-` and `bytes=N-M` are read, which is what a page resuming a download sends. A
    header that cannot be served — malformed, a suffix range, a start at or past the end — is
    answered with the whole body, which is always a correct answer to it.
    """
    match = RANGE.fullmatch((header or "").strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)
MAX_EVENT_BYTES = 65536
MAX_SNAPSHOT_BYTES = 16_000_000  # a PNG as a data: URL, plus what she is doing
SNAPSHOT_SECONDS = 6.0  # how long GET /snapshot waits for a page to answer
# How long a snapshot keeps insisting on a page that shows the chosen model before it
# accepts one that does not. A reload drops the page for a second or two, and a model
# switch leaves the old model on screen until the new file has loaded: in both cases the
# window is there, and the picture that arrives is of the wrong thing.
SNAPSHOT_INSIST_SECONDS = 2.0
MAX_FRAME_BYTES = 24_000_000  # a phone's JPEG as a data: URL, which inflates it by a third
# How long GET /frame waits for a page to answer: longer than a snapshot, because a camera has to
# open before it can take anything, and a phone takes a moment to do that.
FRAME_SECONDS = 12.0
AUTONOMY = (
    "dance",
    "blush",
    "emotion",
    "greeting",
    "cover",
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
# `dance` starts off: the other four trigger on something the user did, while `dance` triggers on
# the media watcher, which cannot tell a song from a talk.
AUTONOMY_DEFAULTS = {
    "dance": False,
    "blush": True,
    "emotion": True,
    "greeting": True,
    "cover": True,
}
# Who may reach her. Empty -- the default -- is a server only this machine talks to, which is what
# she is until someone puts a phone or a proxy in front of her. Set, it guards every request;
# auth.py holds the shapes it is accepted in, and why a bind that is not loopback is refused
# without one.
TOKEN = env_get("TOKEN")


def _on(variable: str) -> bool:
    """Whether an env var says yes. Unset, empty, or a word that means no all say no."""
    return os.getenv(variable, "").strip().lower() not in ("", "0", "off", "no", "false")


def _peer_closed(connection: socket.socket) -> bool:
    """Whether the far end has closed its side of *connection*.

    A closed socket reads as ready with nothing in it. Asking is what makes a reader that has gone
    count as gone at the next keepalive: waiting for a write to fail took two of them, and until then
    the page was told its lines were being read by nobody.
    """
    try:
        readable, _, _ = select.select([connection], [], [], 0)
        return bool(readable) and not connection.recv(1, socket.MSG_PEEK)
    except (OSError, ValueError):  # closed under us, or no longer a socket
        return True


# Her body answers, on its own, a look that has become one she has said no to. It rides the round
# trip the view event is already making, because a reply takes seconds and a boundary cannot wait
# for one. Off unless asked for: an install should not start refusing looks before whoever runs it
# has decided that is what they want.
REFLEX = _on(env_name("REFLEX"))
# And this is what she does about it: a clip of her own, out of the folder made for what the look
# was at -- `cover/chest`, `cover/bottom`, or `cover/all` for a look from below, which sees both at
# once. Nothing ships in them, because no source here may be redistributed; a folder with nothing
# in it leaves her in the idle she was already in. The page plays one of its clips and holds its
# last frame until the look stops.
COVER_POOLS = {"chest": "cover/chest", "hips": "cover/bottom", "thighs": "cover/bottom"}
COVER_ALL = "cover/all"


def cover_for(view: dict) -> str:
    """Which cover folder answers this view: what it is on, or all of her when it is from below."""
    if view.get("side") == "below":
        return COVER_ALL
    return COVER_POOLS.get(view.get("part") or "", COVER_ALL)
# Pools that hold the clips for a moment rather than for a state: a greeting when a conversation
# starts, and whatever else a folder of yours is named later (a goodbye, thanks). Nothing plays in
# one of these by itself -- the moment asks for the pool by name, and the page plays one of its
# clips, chosen at random, so a folder of three greetings greets differently each time. The folder
# is created empty in the home animation folder, because a folder you cannot see is one you cannot
# put a clip in, and nothing here ships a greeting (see PROVENANCE: no source may be redistributed).
EVENT_POOLS = ("greeting", "cover/chest", "cover/bottom", "cover/all")

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
_frames: dict[str, dict] = (
    {}
)  # id -> {"done": Event, "image": bytes, "refusals": [str]}, while asked for
# The names each page gave its cameras (POST /frame/devices?page=<id>), by the id the page chose for
# itself. A page labels a camera only once it has been allowed one, so this is a report rather than a
# discovery, and each report replaces that page's last one. Kept per page because two pages are
# normal now -- a laptop and a phone -- and a name is only ever one page's: the page without the
# camera can only answer that it has none, and its refusal must not answer for everyone.
_cameras: dict[str, list[str]] = {}
_cameras_lock = threading.Lock()
# What zrb can do with the page it is looking at, as zrb says so itself. `remote` comes from
# zrb-avatar's zrb side ({"type": "voice", "remote": bool}): only zrb knows its own config, and `/set`
# can move its voice onto this server or off it while the chat runs. `listening` is this server's own
# knowledge -- a reader on the microphone route (`audio.is_microphone_being_read`) -- so it needs no
# word from zrb and cannot go stale. `said` is the last thing the pages were told, so each change
# reaches them once; the two values themselves are the answer a connecting page gets.
_voice: dict = {"remote": False, "said": None}
# Which page each event stream is, by the id that page gave at /events?page=<id>. Held with the pages
# themselves, because it is discarded with them.
_page_ids: dict[queue.Queue, str] = {}

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


# A switch she set with a lifetime: name -> (value, deadline on the monotonic clock). Kept here
# and not in the file, because an answer that ends by itself is not one a restart should inherit —
# the file is what a restart believes.
_autonomy_for: dict[str, tuple[bool, float]] = {}


def _agreed_now(what: str) -> bool:
    """Whether she is willing to do *what* at this moment. The caller holds `_lock`.

    A switch with a lifetime wins until it runs out; after that the file's answer is hers again,
    which is why the deadline is read here rather than expired by a timer: nothing has to be
    running for a temporary no to end.
    """
    held = _autonomy_for.get(what)
    if held is not None:
        value, deadline = held
        if time.monotonic() < deadline:
            return value
        del _autonomy_for[what]
    return _autonomy.get(what, AUTONOMY_DEFAULTS.get(what, True))


def agreed(what: str) -> bool:
    """Whether she is willing to do `what` now: `dance`, `blush`, `emotion`, `greeting`, `cover`."""
    with _lock:
        return _agreed_now(what)


def autonomy_state() -> dict:
    """What she agreed to do on her own. A switch she has not touched is still its default; see
    AUTONOMY_DEFAULTS for why `dance` is the one that starts off. A switch set with a lifetime
    reads as what it is right now, so a page that connects mid-way is told the truth."""
    with _lock:
        return {name: _agreed_now(name) for name in AUTONOMY}


def choose_autonomy(value, seconds: float | None = None) -> None:
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

    *seconds* makes it temporary: the switch holds for that long and then goes back to what the file
    says, and nothing is written down, so a restart mid-way comes back to her own answer rather than
    to a decision she made for a while.
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
    if seconds is not None and (
        not isinstance(seconds, (int, float))
        or isinstance(seconds, bool)
        or seconds <= 0
    ):
        raise ValueError("seconds is how long the switch holds: a number above zero")
    with (
        _autonomy_lock
    ):  # the state, the file and the rollback are one change, not three
        with _lock:
            before = dict(_autonomy)
            before_for = dict(_autonomy_for)
            if seconds is None:
                _autonomy.update(value)
                for name in value:
                    _autonomy_for.pop(name, None)  # a decision replaces a timeout, and vice versa
            else:
                deadline = time.monotonic() + seconds
                for name, one in value.items():
                    _autonomy_for[name] = (one, deadline)
            # Every switch goes to the pages, because they merge what they are told: a switch that
            # moved back to its default has to be named. The file gets only what she moved.
            whole = {name: _agreed_now(name) for name in AUTONOMY}
            saved = {
                name: one
                for name, one in _autonomy.items()
                if one != AUTONOMY_DEFAULTS[name]
            }
            # A no to covering while she is covering is worth acting on at once, not at the next
            # look: the pose is hers, so taking it back must not wait for the camera to move.
            if not _agreed_now("cover") and _pose.get("reflex"):
                _pose.clear()
                _send_all(json.dumps({"type": "cover", "pool": None}))
        try:
            if seconds is None:
                AUTONOMY_FILE.parent.mkdir(parents=True, exist_ok=True)
                # Written beside it and moved into place, so a reader never sees half a file: the
                # server is threaded, and a crash mid-write would leave one for the next start.
                half = AUTONOMY_FILE.with_name(AUTONOMY_FILE.name + ".new")
                half.write_text(json.dumps(saved), encoding="utf-8")
                half.replace(AUTONOMY_FILE)
        except OSError as e:
            with (
                _lock
            ):  # not remembered is not taken: the answer stays what the file says
                _autonomy.clear()
                _autonomy.update(before)
                _autonomy_for.clear()
                _autonomy_for.update(before_for)
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
    # An event pool is created even though the package ships nothing in it: the folder is where a
    # greeting of your own goes, and a folder that does not exist is not somewhere to put one.
    for pool in EVENT_POOLS:
        (ANIM_HOME / pool).mkdir(parents=True, exist_ok=True)
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
    """Ask the open pages for a picture of the window; the first to answer wins.

    A page is not the same as the window being there. A reload drops the page for a
    second or two, and a model switch leaves the old model on screen until the new one
    has loaded, so the pages are asked again for as long as the timeout lasts -- one
    that connects late still answers -- and while any page shows the chosen model only
    those are asked, so a window still changing clothes cannot answer for the one
    already dressed. None only when no page answers in time.
    """
    key = secrets.token_hex(8)
    waiting = _snapshots[key] = {"done": threading.Event(), "image": b"", "about": {}}
    message = json.dumps({"type": "snapshot", "id": key})
    chosen = chosen_model()
    now = time.monotonic()
    deadline = now + timeout
    insist = now + min(SNAPSHOT_INSIST_SECONDS, timeout)
    asked: set[queue.Queue] = set()  # each page is asked once: one answer ends this
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                return None
            with _lock:
                open_pages = list(_clients)
                dressed = [
                    page for page in open_pages if chosen and _showing.get(page) == chosen
                ]
                # Nobody dressed yet and time to spare: wait for the page that is
                # finishing its swap rather than take the old clothes for the answer.
                if not dressed and chosen and now < insist:
                    targets = []
                else:
                    targets = dressed or open_pages
                for page in targets:
                    if page not in asked:
                        asked.add(page)
                        page.put(message)
            if waiting["done"].wait(min(0.5, deadline - now)):
                return waiting["image"], waiting["about"]
    finally:
        _snapshots.pop(key, None)


def take_frame(
    device: str | None, timeout: float = FRAME_SECONDS
) -> tuple[bytes, str]:
    """Ask the pages for a photo from *device*; the first photo wins, and only a photo.

    Which page it is has to be settled before anything is asked, so that is first: `camera_owner`
    resolves the name to one page, or gives back the sentence saying why the name is nobody's -- a
    label two pages report, which asked for bare could only be a photo from whichever machine
    answered first. In that case the sentence is the whole answer and no page hears of it.

    Otherwise whom to ask is known: each page reports its own camera names, so the page that
    reported the name is the page that can take it, and the others are not asked at all. Asking
    everyone at once is how a refusal came to be the answer -- the laptop without the phone's back
    camera says so first, and the photo arriving a moment later is too late. A name no page has
    reported is asked of every page, since a page reports only after it has been allowed a camera
    and the report can be a moment behind.

    A refusal does not end this while a page that was asked has yet to answer: it is one page
    saying it could not, and the page holding the camera may still be taking the photo. When every
    page asked has answered and none of them had a photo, nobody is left to wait for.

    The ask is repeated for as long as the timeout lasts, so a page that connects late still
    answers. The second value is the last sentence a page gave, which is the sentence the user
    needs rather than a failure with no reason; an empty image means no photo at all.
    """
    owner = camera_owner(device or "")
    if isinstance(owner, str):
        return b"", owner  # two pages' camera: the ask never leaves, and the sentence says why
    page, label = owner if owner else ("", device or "")
    key = secrets.token_hex(8)
    waiting = _frames[key] = {
        "done": threading.Event(),
        "image": b"",
        "refusals": [],
    }
    message = json.dumps({"type": "frame", "id": key, "device": label})
    deadline = time.monotonic() + timeout
    asked: set[queue.Queue] = set()  # each page once: a second ask is a second photo
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                return b"", _last_refusal(waiting)
            for asking in _pages_for_frame(page):
                if asking not in asked:
                    asked.add(asking)
                    asking.put(message)
            if waiting["done"].wait(min(0.5, deadline - now)):
                return waiting["image"], ""
            if asked and len(waiting["refusals"]) >= len(asked):
                return b"", _last_refusal(waiting)  # every page asked said it could not
    finally:
        _frames.pop(key, None)


def _pages_for_frame(page: str) -> list[queue.Queue]:
    """The open pages a photo should be asked of.

    The page the name resolved to, and every page when no page reported the name at all -- or when
    the page that did has gone, since a page that connects late carries its own report with it.
    """
    with _lock:
        open_pages = list(_clients)
        if not page:
            return open_pages
        holding = [client for client in open_pages if _page_ids.get(client, "") == page]
    return holding or open_pages


def _last_refusal(waiting: dict) -> str:
    """The most recent sentence a page gave for having no photo.

    The last rather than the first: the pages that said so earliest are the ones least likely to
    have had the camera, so a camera in use is a more useful sentence than a name nobody has.
    """
    refusals = waiting["refusals"]
    return refusals[-1] if refusals else ""


def _qualified(label: str, page: str) -> str:
    """*label* as that one page's camera: the name a label two pages report is offered under."""
    return f"{label} ({page})"


def _claimed_cameras() -> dict[str, list[str]]:
    """Every camera label the pages reported, and the ids of the pages that reported it.

    Built from the reports rather than kept beside them: the order is the order they were reported
    in, and a label two pages answer to is the one case a name alone cannot settle.
    """
    claimed: dict[str, list[str]] = {}
    with _cameras_lock:
        for page, names in _cameras.items():
            for name in names:
                pages = claimed.setdefault(name, [])
                if page not in pages:
                    pages.append(page)
    return claimed


def _ambiguous(label: str, pages: list[str]) -> str:
    """The refusal for a label more than one page reports: which pages, and what to ask instead."""
    return (
        f"{label} is claimed by pages {', '.join(pages)}; "
        f"ask for {' or '.join(_qualified(label, page) for page in pages)}"
    )


def camera_owner(device: str) -> tuple[str, str] | str | None:
    """Which page *device* is, and the label to ask that page for; or why it is nobody's alone.

    A name resolves three ways. One page reporting it makes it that page's camera, by its own
    label, which is how every name resolved before two pages were normal. Two pages reporting it
    makes it neither's: asking both would take whichever answered first, and a photo from the
    other machine is the wrong photo rather than a slow one -- so such a label is offered
    qualified, `back camera (<page>)`, and asked for bare it is refused with a string, the
    sentence for the 503, saying which pages claim it and which names do one page. An empty answer
    is a name no page has reported yet, which is asked of every page: a page reports only once it
    has been allowed a camera, and that report can be a moment behind.

    `page_cameras` offers exactly the names this resolves to one page, so a name in zrb's camera
    list is a name `/photo` can take a photo with.
    """
    wanted = device.strip()
    if not wanted:
        return None
    claimed = _claimed_cameras()
    if wanted in claimed:  # a page's own label, asked for as it was reported
        pages = claimed[wanted]
        if len(pages) == 1:
            return pages[0], wanted
        return _ambiguous(wanted, pages)  # nobody's alone, so nothing is asked
    for label, pages in claimed.items():  # a label qualified by the page that has it
        if len(pages) < 2:
            continue  # a name only one page reports is offered, and answered, bare
        for page in pages:
            if wanted == _qualified(label, page):
                return page, label
    return None


def page_cameras() -> list[str]:
    """The camera names the pages reported, which are labels rather than ids.

    Every page's, in the order they were reported and once each: zrb offers them beside this
    machine's own cameras, and what it asks for afterwards is a name rather than a page. A browser
    hides a camera's label until that page has been allowed one, so this is empty until the first
    grant, and filling in is the report that it happened.

    A label only one page reports is offered as it is, because a name in a completion is one
    somebody has already learned and typed. One that two pages report is offered once per page,
    qualified with the page, since a bare name for it is a name that cannot pick a camera.
    """
    names: list[str] = []
    for label, pages in _claimed_cameras().items():
        if len(pages) == 1:
            names.append(label)
        else:
            names.extend(_qualified(label, page) for page in pages)
    return names


def set_page_cameras(page: str, names: object) -> None:
    """Replace what *page* reported; a page reports what it can see now, not an addition."""
    cleaned = [
        name.strip() for name in names if isinstance(name, str) and name.strip()
    ] if isinstance(names, list) else []
    with _cameras_lock:
        if cleaned:
            _cameras[page] = cleaned
        else:
            _cameras.pop(page, None)  # a page allowed nothing knows nothing


def forget_page_cameras(page: str) -> None:
    """Drop what *page* reported: its cameras went with the page that named them."""
    with _cameras_lock:
        _cameras.pop(page, None)


def _status_reason(sentence: str) -> str:
    """*sentence* as a status line's reason: one line, ASCII, and short enough to be one.

    `send_error` puts it in the reason phrase, which is the one place a page's own words reach
    zrb — the client repeats that instead of guessing — so it leaves here as a phrase a header
    can carry rather than as text that would end the line early.
    """
    line = " ".join(sentence.split()).encode("ascii", "replace").decode("ascii")[:200]
    return line or "the page could not take the photo"


def _send_all(message: str) -> None:
    """Put one line in every open page's queue. The caller holds `_lock`."""
    for client in _clients:
        client.put(message)


def broadcast(message: str) -> None:
    with _lock:
        _send_all(message)


def voice_state() -> dict:
    """What zrb can do with a page: speak through it, and hear it.

    `remote` is read without a lock because it is a bool and `_replay` is called with `_lock` held:
    a page that connects while zrb's voice is elsewhere should say so in the same breath as its
    model, not a second later.
    """
    return {
        "type": "voice",
        "remote": bool(_voice["remote"]),
        "listening": audio.is_microphone_being_read(),
    }


def _tell_pages_voice() -> None:
    """Tell every page what zrb can do with it, when that has changed.

    Called on both moments it can change: zrb saying its voice moved onto this server or off it, and
    zrb's reader arriving on the microphone route or leaving it again. A page that is told twice the
    same thing would be a page that redraws its buttons for nothing, so only a change is sent.

    The state is read inside the lock the send is made under, so a reader ending and its replacement
    starting cannot publish in the wrong order: whichever call gets the lock last reads the count as
    it is then, and the page is left holding the newer answer rather than the older one.
    """
    with _lock:
        said = voice_state()
        if said == _voice["said"]:
            return
        _voice["said"] = said
        _send_all(json.dumps(said))


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

    What she covers with is a folder named for what the look is at, so a clip of her own answers
    the look it was made for; which clip in it, and when to let go, is the page's. It is a refusal,
    not a lock: the user can zoom straight back in, and she covers again. What it costs: a reflex
    wins over whatever her body was doing, and that is not put back afterwards, which is what
    being quicker than a thought means.
    """
    if not REFLEX or not agreed("cover"):
        # Not the machine's setting but her own: `cover` is a switch like the others, so a no to
        # covering holds until she says otherwise (or until the lifetime she gave it runs out), and
        # a cover already up comes down rather than staying up on a consent she has withdrawn.
        with _lock:
            if _pose.get("reflex"):
                _pose.clear()
                _send_all(json.dumps({"type": "cover", "pool": None}))
        return
    pool = cover_for(_view)
    with _lock:
        held = _pose.get("pool") if _pose.get("reflex") else None
        if (pool if shy else None) == held:  # the same answer as last time: one cover per look
            return
        _pose.clear()
        if shy:
            _pose.update({"type": "cover", "pool": pool, "reflex": True})
            _send_all(json.dumps(_pose))
        else:
            _send_all(json.dumps({"type": "cover", "pool": None}))


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
    # Only when there is something to reach: a page assumes nothing can, so with zrb's voice
    # elsewhere there is nothing to say — the same rule as the switches above. A page that connects
    # after it moved here is told, so its first frame is not three buttons that do nothing.
    voice = voice_state()
    if voice["remote"] or voice["listening"]:
        client.put(json.dumps(voice))
    # Only when she has moved a switch away from its default: a page assumes the defaults, so when
    # that is the truth there is nothing to tell it. The caller holds the lock, so this is the
    # dict, not a fresh read.
    if _autonomy != AUTONOMY_DEFAULTS:
        client.put(json.dumps({"type": "autonomy", "value": dict(_autonomy)}))


class Handler(BaseHTTPRequestHandler):
    def _allowed(self) -> bool:
        """Whether this request carries the token, when the server asks for one."""
        return auth.presented(
            self.headers, urllib.parse.urlparse(self.path).query, TOKEN
        )

    def _hand_over_the_cookie(self):
        """Trade a `?token=` for the cookie, and go where the link meant to go.

        The one thing a phone needs to do by hand: the address is typed once, with the token in
        it, and every request after that carries the cookie by itself -- the page fetches and its
        EventSource included, without a line of JavaScript asking.
        """
        target = auth.url_without_token(self.path)
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Set-Cookie", auth.cookie_header(TOKEN))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self.send_error(401, "this server wants its token")
        if auth.wants_a_cookie(self.path, TOKEN):
            return self._hand_over_the_cookie()
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
        if path == "/audio/in":  # the page's microphone, on its way to zrb's dictation
            return self._relay_audio_to_zrb()
        if path == "/audio/out":  # what zrb said, on its way to the page
            return self._relay_speech_to_page()
        if path == "/text/in":  # what was typed on the page, on its way to zrb
            return self._relay_typed_to_zrb()
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
        if path == "/frame":  # a camera this machine does not have: the page's own
            return self._page_frame()
        if path == "/frame/devices":  # the names the page last gave its cameras
            return self._json({"devices": page_cameras()})
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
        if path.endswith(".vrm"):  # the one file big enough that a resume is worth asking for
            return self._send_model(file.read_bytes())
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

    def _send(self, body: bytes, content_type: str, extra: dict[str, str] | None = None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_model(self, body: bytes):
        """Her model, or the rest of it when the page asks for a byte range.

        A phone whose screen went off drops the connection at whatever it had got — six of eighteen
        megabytes, measured 2026-11-21 — and the page comes back asking for the bytes after those
        rather than for the whole file again. The range is answered `206` with the size it belongs
        to, and the full answer says `Accept-Ranges` so a page knows the question can be asked.
        """
        asked = asked_range(self.headers.get("Range"), len(body))
        if asked is None:
            return self._send(body, "application/octet-stream", {"Accept-Ranges": "bytes"})
        start, end = asked
        part = body[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(part)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(part)

    def _body(self):
        """The request body as it arrives, however the caller measured it.

        A page streaming its microphone sends a chunked body and never says how long it is; a
        fallback that posts a frame at a time measures each one. Both arrive here, and neither is
        read with `read(-1)`, which would wait for the client to hang up before the first byte
        reached a reader.
        """
        if (self.headers.get("Transfer-Encoding") or "").strip().lower() == "chunked":
            yield from self._chunks()
            return
        size = self._length(audio.MAX_BODY_BYTES)
        if size is None:
            return
        while size > 0:
            block = self.rfile.read(min(size, 65536))
            if not block:
                return
            size -= len(block)
            yield block

    def _chunks(self):
        """The blocks of a chunked body, up to the one that ends it.

        `fetch(url, {body, duplex: "half"})` is what sends this: the page has no length to give
        while the microphone is open, and no way to end the request except by ending the body. A
        body that stops in the middle of a chunk is a page that went away, and ends the same way an
        ending does -- what arrived is kept, and nothing is waited for.
        """
        while True:
            line = self.rfile.readline(64)
            try:
                size = int(line.split(b";")[0].strip(), 16)
            except ValueError:
                return
            if size <= 0:
                return  # the last chunk, or a page that stopped saying anything at all
            block = self.rfile.read(size)
            if len(block) < size:
                return
            self.rfile.read(2)  # the CRLF a chunk ends with, which is not audio
            yield block

    def _write_frames(self, channel, rate: int, follows_rate: bool = False):
        """Write frames as they arrive, and a frame of silence when none does.

        Raw PCM has no comment syntax, so the answer to a quiet queue is silence rather than
        nothing: one frame of it, inaudible, which is also the only write that notices a page whose
        tab has gone. *follows_rate* ends the response when a sentence is made at a rate this
        stream is not at, because the headers are the only place a rate can say what it is -- and a
        channel that has been replaced ends it at once, since the page asks again as soon as this
        response ends, and waiting for that is waiting for the next sentence.
        """
        silence = audio.silence(rate)
        try:
            while not channel.ended:
                frame = channel.take(audio.PING_SECONDS)
                if frame is None:
                    if channel.ended or (
                        follows_rate and audio.outbound_rate() not in (None, rate)
                    ):
                        return
                    frame = silence
                self.wfile.write(frame)
                self.wfile.flush()
        except OSError:  # the far end closed: nothing left to say, and nobody to say it to
            pass

    def do_POST(self):
        if not self._allowed():
            return self.send_error(401, "this server wants its token")
        if self.path == "/audio":  # the page's microphone, until it stops talking
            return self._take_audio()
        if self.path == "/say":  # what zrb's speech player made
            return self._take_speech()
        if self.path == "/text":  # one line typed on the page
            return self._take_text()
        if self.path.startswith("/snapshot?"):
            return self._snapshot()
        # The page's cameras, as the page names them: the page itself travels in the query.
        if self.path == "/frame/devices" or self.path.startswith("/frame/devices?"):
            return self._take_camera_names()
        if self.path.startswith("/frame?"):
            return self._frame()
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
                    event.get("value"), event.get("seconds")
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
        if (
            isinstance(event, dict) and event.get("type") == "voice"
        ):  # zrb: whether its voice is this server's
            _voice["remote"] = bool(event.get("remote"))
            _tell_pages_voice()
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
            waiting["image"], waiting["about"] = image, {
                **(about if isinstance(about, dict) else {}),
                # Which avatar is *meant* to be showing, so zrb can tell a picture of the
                # one that is still loading from the one it asked for.
                "chosen": chosen_model(),
            }
            waiting["done"].set()
        self.send_response(204)
        self.end_headers()

    def _page_frame(self):
        """One photo from the page's camera, as the page itself sees it.

        A refusal is a status rather than an empty body: the page said a sentence -- it was not
        allowed the camera, the camera is held by another app -- and the reason phrase is where
        that sentence reaches zrb, which repeats it. An empty photo with no sentence would be a
        failure the user cannot act on.
        """
        device = (
            urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
                "device", [""]
            )[0]
            or ""
        ).strip()
        image, refusal = take_frame(device or None, FRAME_SECONDS)
        if image:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return
        if refusal:
            return self.send_error(503, _status_reason(refusal))
        return self.send_error(
            504, _status_reason(f"no page took a photo from {device or 'its camera'}")
        )

    def _frame(self):
        """A page's answer to a frame request: {"image": "data:image/jpeg;base64,..."} or
        {"refusal": "the browser would not give me the camera"}. Only for an id asked for."""
        key = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "id", [""]
        )[0]
        waiting = _frames.get(key)
        if waiting is None:
            return self.send_error(404)
        size = self._length(MAX_FRAME_BYTES)
        if size is None:
            return
        try:
            answer = json.loads(self.rfile.read(size))
        except ValueError:
            return self.send_error(400)
        if not isinstance(answer, dict):
            return self.send_error(400)
        image = b""
        if "image" in answer:
            try:
                header, data = answer["image"].split(",", 1)
                if header != "data:image/jpeg;base64":
                    raise ValueError(header)
                image = base64.b64decode(data, validate=True)
            except (ValueError, KeyError, TypeError, AttributeError):
                return self.send_error(400)
        refusal = "" if image else str(answer.get("refusal") or "").strip()
        if image:
            if not waiting["done"].is_set():
                waiting["image"] = image
                waiting["done"].set()
        else:  # one sentence per page: a refusal is that page's answer, not the conversation's
            waiting["refusals"].append(
                refusal or "the page sent no photo and no reason"
            )
        self.send_response(204)
        self.end_headers()

    def _take_camera_names(self):
        """The names a page gives its cameras: labels, by the id the page goes by.

        A page labels a camera only once it has been allowed one, so this is a report and not a
        discovery, and the last report from that page is the whole truth about it: a page allowed
        nothing knows nothing. Kept per page because a name belongs to the page that gave it --
        `take_frame` asks that page, and only that page, when zrb names one.
        """
        size = self._length(MAX_EVENT_BYTES)
        if size is None:
            return
        try:
            payload = json.loads(self.rfile.read(size))
        except ValueError:
            return self.send_error(400)
        names = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(names, list):
            return self.send_error(400, 'a camera report is {"devices": [name, ...]}')
        page = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "page", [""]
        )[0]
        set_page_cameras(page, names)
        self.send_response(204)
        self.end_headers()

    def _take_audio(self):
        """The page's microphone: one long request, its blocks handed to zrb as they arrive.

        The format is refused rather than accepted and mistranscribed: audio at another rate is
        noise to every speech-to-text backend, and a request that says so is a page to fix, not a
        transcript to read.
        """
        rate = (self.headers.get("X-Sample-Rate") or "").strip()
        channels = (self.headers.get("X-Channels") or "").strip()
        wanted, mono = str(audio.INBOUND_RATE), str(audio.CHANNELS)
        if (rate or wanted) != wanted or (channels or mono) != mono:
            return self.send_error(
                415,
                f"audio in is {wanted} Hz mono; got {rate or '?'} Hz, "
                f"{channels or '?'} channel(s)",
            )
        for block in self._body():
            audio.put_inbound(block)
        self.send_response(204)
        self.end_headers()

    def _take_speech(self):
        """What zrb's speech player made, with the rate it was made at in the header."""
        try:
            rate = int((self.headers.get("X-Sample-Rate") or "").strip())
        except ValueError:
            rate = 0
        if not audio.is_supported_rate(rate):
            return self.send_error(
                415, "X-Sample-Rate names no rate speech can have"
            )
        for block in self._body():
            audio.put_outbound(block, rate)
        self.send_response(204)
        self.end_headers()

    def _read_exactly(self, size: int) -> bytes | None:
        """Exactly *size* bytes, or None when the body stopped short of what it declared.

        A request that says one length and sends less is a page that went away in the middle of a
        line. Half a message is not a message: putting the part that arrived to zrb would hand the
        chat a truncated sentence as though it had been typed whole.
        """
        body = b""
        while len(body) < size:
            block = self.rfile.read(size - len(body))
            if not block:
                return None
            body += block
        return body

    def _take_text(self):
        """One line typed on the page, for zrb's next turn.

        A typed line is measured, not streamed: the page knows what it sent, and a body that says
        otherwise is refused rather than read until the client hangs up. UTF-8 only — a line that is
        not is a page to fix, not a message to guess at.

        Refused with a 503 when nobody holds ``/text/in``: a page told its line arrived is worse off
        than one that keeps it, because the words are still in the box to send again.
        """
        size = self._length(typed.MAX_BODY_BYTES)
        if size is None:
            return
        body = self._read_exactly(size)
        if body is None:
            return self.send_error(400, "the typed line stopped short")
        try:
            line = body.decode("utf-8")
        except UnicodeDecodeError:
            return self.send_error(400, "a typed line is UTF-8")
        if not typed.reader_attached():
            # Nobody holds /text/in: zrb without the reader in it, or a chat that is not running. Said
            # rather than queued, because a page told its line arrived is worse off than one that keeps
            # it -- the box puts the words back and Enter sends them again once something is reading.
            return self.send_error(503, "nothing is reading this page's text box")
        try:
            accepted = typed.put_line(line)
        except typed.ChannelFull:
            return self.send_error(503, "the chat is not taking typed lines")
        if not accepted:
            return self.send_error(400, "nothing was typed")
        self.send_response(204)
        self.end_headers()

    def _relay_audio_to_zrb(self):
        """The page's microphone, as one long response of raw frames.

        One reader at a time is the point: two would halve the audio between them and neither
        would transcribe a sentence. Taking the channel first is what makes this reader the one —
        a reader that has gone is replaced here rather than left holding the next block.

        Arriving and leaving are also what tells the pages whether a press on their microphone would
        be heard: this is the moment zrb starts reading, and the end of the route is the moment it
        stops. A page offers the button on that, and no word from zrb has to say it.
        """
        channel = audio.start_reading("in")
        _tell_pages_voice()  # zrb is reading: a page's microphone now has somewhere to go
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Sample-Rate", str(audio.INBOUND_RATE))
            self.send_header("X-Sample-Width", str(audio.SAMPLE_WIDTH))
            self.send_header("X-Channels", str(audio.CHANNELS))
            self.end_headers()
            self._write_frames(channel, audio.INBOUND_RATE)
        finally:
            audio.end_reading("in")
            _tell_pages_voice()  # whatever ended the route, nothing is reading now

    def _relay_speech_to_page(self):
        """What zrb said, as one long response of raw frames, at the rate the headers name.

        The channel and the rate are taken in one step (`audio.start_reading_outbound`), because the
        page configures itself from that header: a sentence made at another rate rotates the channel,
        and a rate read separately from the channel it belongs to is a sentence played wrong.
        """
        channel, rate = audio.start_reading_outbound()
        self.send_response(200)
        self.send_header("Content-Type", "audio/L16")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Sample-Rate", str(rate))
        self.send_header("X-Sample-Width", str(audio.SAMPLE_WIDTH))
        self.send_header("X-Channels", str(audio.CHANNELS))
        self.end_headers()
        self._write_frames(channel, rate, follows_rate=True)

    def _relay_typed_to_zrb(self):
        """What was typed on the page, as one long response of newline-framed UTF-8 lines.

        One reader at a time, as the microphone has one: two readers would take different lines of
        one message and neither would read it whole. A quiet route writes a blank line rather than
        nothing, which is the write that notices a reader that has gone, and a blank line is not a
        line: the reader on the other side skips it.
        """
        channel = typed.start_reading()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        line = ""
        try:
            while not channel.ended:
                line = channel.take(typed.KEEPALIVE_SECONDS)
                if channel.ended or _peer_closed(self.connection):
                    # A newer reader has it, or this one has gone. Either way a line already taken
                    # goes back rather than out with the response: a reconnect is not a reason to
                    # lose what was said into it.
                    typed.unsend(line)
                    return
                self.wfile.write(line.encode() + b"\n")
                self.wfile.flush()
        except OSError:  # the far end closed: nothing left to say, and nobody to say it to
            typed.unsend(line)  # the write that failed carried a line with it
        finally:
            typed.stop_reading(channel)  # zrb has stopped listening, and the box is told so

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
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        shows = query.get("model", [""])[0]
        # And which page it is (/events?page=<id>, the same id its camera report carries), so a photo
        # can be asked of the page that named the camera rather than of every page in the room.
        page = query.get("page", [""])[0]
        chosen = None if shows else chosen_model()
        # What it missed, and joining the pages that hear what comes next, are one step: a held pose
        # (or a release) arriving between them would be in neither, and a reload would stand her up
        # in the middle of a thought — or bring back a posture she had already let go of.
        with _lock:
            _clients.add(client)
            if page:
                _page_ids[client] = page
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
                page = _page_ids.pop(client, None)
                # A stream that reconnects carries the same id, so a page is only gone when no
                # stream of its own is still open.
                gone = page is not None and page not in _page_ids.values()
            if page is not None and gone:
                forget_page_cameras(page)  # its cameras went with it

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
    # Before anything is served: a bind the tailnet can reach is refused unless a token guards
    # it. There is no flag that talks this out of it -- see auth.refuse_insecure_bind.
    auth.refuse_insecure_bind(host, TOKEN, env_name("TOKEN"))
    # Quad digits, easy to say and to type; nothing is registered to it. It only has to
    # stay clear of zrb's web port (21213) and the board's 8765 -- and it is a default,
    # not a decision: ZRB_AVATAR_PORT moves it, with ZRB_AVATAR_URL to match.
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
