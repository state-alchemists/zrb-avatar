import ast
import asyncio
import http.client
import importlib
import json
import os
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

from zrb_avatar import client as face_client
from zrb_avatar import server
from zrb_avatar import typed
from zrb_avatar.client import MOUTH_STEP, Mouth, loudness

# The client compares `context.event == HookEvent.SESSION_START`, and HookEvent is not a string
# enum: a test context carrying the string "SESSION_START" is not a session start.
try:  # zrb 4.0 keeps the hook unit under capabilities/
    from zrb.llm.capabilities.hook import HookEvent
except ImportError:
    from zrb.llm.hook import HookEvent

# Loudness: silence closes the mouth, full scale opens it, one value per step.
assert loudness(bytes(3840), 24000) == [0.0, 0.0]  # 1920 samples = 2 x 40 ms
assert loudness(b"\xff\x7f" * 960, 24000) == [1.0]

# A chunk rarely ends on a step, and the samples that don't fill one wait for the next chunk
# (a value must be one step, or the mouth outlives the voice: piper's 2048-sample chunks at
# 22 kHz carry 2.32 steps, so counting the short last window as a whole one sends 3 values,
# 120 ms, for 93 ms of audio).
half = Mouth(22050)  # 22050 * 0.04 = 882 samples per step
assert half.feed(b"\xff\x7f" * 441) == []  # half a step, nothing to play yet
assert half.feed(b"\xff\x7f" * 441) == [1.0]  # the two halves make one step
assert half.flush() == []  # and nothing is left over
assert Mouth(22050).flush() == []  # nothing fed: nothing to flush

# One value per step of audio, chunk boundary or not: the mouth lasts as long as the voice.
mouth, chunks, rate = Mouth(22050), 586, 22050
voiced = sum(len(mouth.feed(b"\xff\x7f" * 2048)) for _ in range(chunks)) + len(
    mouth.flush()
)
assert abs(voiced * MOUTH_STEP - chunks * 2048 / rate) <= MOUTH_STEP, (
    voiced * MOUTH_STEP
)


# The wiring: whatever a backend streams, the page hears one value per step of audio, and
# the chunks still reach zrb's player untouched.
class _Streamed:
    sample_rate, chunks = 22050, [b"\xff\x7f" * 2048] * 10


sent, face_send = [], face_client.send
face_client.send = sent.append
try:
    streamed = face_client.with_mouth(_Streamed())
    assert len(list(streamed.chunks)) == 10
finally:
    face_client.send = face_send
assert (
    len(sent) == 11 and sent[-1]["type"] == "mouth"
)  # every chunk, then the last short step
assert (
    abs(sum(len(m["values"]) for m in sent) * MOUTH_STEP - 10 * 2048 / rate)
    <= MOUTH_STEP
)

# The caption: the sentence goes out as the voice starts and is cleared once the last chunk has
# been read. The page holds a line for a minimum time, because this clear runs ahead of the ear.
spoken, face_send = [], face_client.send
face_client.send = spoken.append
try:
    captioned = face_client.with_mouth(_Streamed(), "Good morning, Go.")
    assert len(list(captioned.chunks)) == 10
finally:
    face_client.send = face_send
assert spoken[0] == {"type": "subtitle", "text": "Good morning, Go."}
assert spoken[-1] == {"type": "subtitle", "clear": True}
assert sum(m["type"] == "mouth" for m in spoken) == 11  # the mouth is unchanged by the caption
# ZRB_AVATAR_SUBTITLE=off: the same audio, no caption at all.
quiet, face_send, subtitle_on = [], face_client.send, face_client.SUBTITLE
face_client.send, face_client.SUBTITLE = quiet.append, False
try:
    uncaptioned = face_client.with_mouth(_Streamed(), "not captioned")
    assert len(list(uncaptioned.chunks)) == 10
finally:
    face_client.send, face_client.SUBTITLE = face_send, subtitle_on
assert not any(m["type"] == "subtitle" for m in quiet)

# The state is a caption too: while she is not speaking, the line says what she is doing, and
# idle clears it. It rides the same switch, so ZRB_AVATAR_SUBTITLE=off silences both halves.
class _Hooks:
    def __init__(self):
        self.registered = []

    def add_hook(self, hook, events):
        self.registered.append((hook, events))


class _Context:
    event = "not a session start"  # so the greeting branch is not taken
    event_data = None


def _hook_for(hooks, name):
    for hook, events in hooks.registered:
        if any(getattr(event, "name", "") == name for event in events):
            return hook
    raise AssertionError(f"nothing registered for {name}")


lines, face_send = [], face_client.send
face_client.send = lines.append
try:
    registry = _Hooks()
    face_client.face_hooks(registry)
    for event in ("PRE_TOOL_USE", "PERMISSION_REQUEST", "STOP"):
        asyncio.run(_hook_for(registry, event)(_Context()))
finally:
    face_client.send = face_send
assert lines[:2] == [
    {"type": "state", "value": "tool"},
    {"type": "status", "text": "(working…)"},
], lines
assert {"type": "status", "text": "(waiting for your answer…)"} in lines
assert lines[-2:] == [
    {"type": "state", "value": "idle"},
    {"type": "status", "text": ""},
], lines  # idle has nothing to say, so the line goes rather than saying "(idle…)"
muted, face_send, subtitle_on = [], face_client.send, face_client.SUBTITLE
face_client.send, face_client.SUBTITLE = muted.append, False
try:
    quiet_registry = _Hooks()
    face_client.face_hooks(quiet_registry)
    asyncio.run(_hook_for(quiet_registry, "PRE_TOOL_USE")(_Context()))
finally:
    face_client.send, face_client.SUBTITLE = face_send, subtitle_on
assert muted == [{"type": "state", "value": "tool"}], muted

# A run a hook starts — the memory reviewer, a sub-agent, a skill — is not the conversation, and
# zrb marks it with `nested_run` on every hook payload. Painting its prompt as "thinking" leaves
# the caption saying the user's turn is still running, because nothing tells the window idle when
# that run ends: it says so until the user speaks again. Everything outward is skipped for such a
# run, the approval's answer and emotion included, and the same event from the user's turn paints.
class _Nested(_Context):
    event_data = {"nested_run": True}


class _Answered(_Context):
    event_data = {"approved": True}


class _Denied(_Context):
    event_data = {"approved": False}


class _NestedAnswered(_Answered):
    event_data = {"approved": True, "nested_run": True}


class _Emotion(_Context):
    emotion, emotion_source = "happy", "prompt"


class _NestedEmotion(_Emotion):
    event_data = {"nested_run": True}


def _painted(context, name):
    paints, face_send = [], face_client.send
    registry = _Hooks()
    face_client.face_hooks(registry)
    face_client.send = paints.append
    try:
        asyncio.run(_hook_for(registry, name)(context))
    finally:
        face_client.send = face_send
    return paints


for nested_event in ("USER_PROMPT_SUBMIT", "PRE_TOOL_USE", "PERMISSION_REQUEST", "STOP"):
    assert _painted(_Nested(), nested_event) == [], nested_event
assert _painted(_Context(), "PRE_TOOL_USE") == [
    {"type": "state", "value": "tool"},
    {"type": "status", "text": "(working…)"},
]  # the same event from the user's turn: the guard is not a mute button
if getattr(HookEvent, "PERMISSION_RESOLVED", None) is not None:
    assert _painted(_NestedAnswered(), "PERMISSION_RESOLVED") == []
    # The answer ends the caption as well as the posture. The page keeps the last status line up
    # until another one arrives, so a bare state left "(waiting for your answer…)" over a tool
    # that had been running for a minute — the approval's line outliving the approval.
    assert _painted(_Answered(), "PERMISSION_RESOLVED") == [
        {"type": "state", "value": "tool"},
        {"type": "status", "text": "(working…)"},
    ]
    assert _painted(_Denied(), "PERMISSION_RESOLVED") == [
        {"type": "state", "value": "thinking"},
        {"type": "status", "text": "(thinking…)"},
    ]
    # And the caption rides ZRB_AVATAR_SUBTITLE like every other one.
    painted_subtitle, face_client.SUBTITLE = face_client.SUBTITLE, False
    try:
        muted_answer = _painted(_Answered(), "PERMISSION_RESOLVED")
    finally:
        face_client.SUBTITLE = painted_subtitle
    assert muted_answer == [{"type": "state", "value": "tool"}], muted_answer
if getattr(HookEvent, "EMOTION", None) is not None:
    assert _painted(_NestedEmotion(), "EMOTION") == []
    assert _painted(_Emotion(), "EMOTION") == [
        {"type": "emotion", "value": "happy", "source": "prompt"}
    ]

# The greeting is the pool, not a file name. `greeting/` is a folder of your own clips and the page
# plays one of them at random; the greeting is asked for by the folder, so a greeting you drop in
# works without anything knowing what you called it, and `actions/Greeting.vrma` still plays as the
# fallback the README used to ask for. A run that carries earlier turns is not a greeting, however
# often SessionStart fires.
class _Fresh(_Context):
    event = HookEvent.SESSION_START


class _Resumed(_Fresh):
    event_data = {"history": ["an earlier turn"]}


saved_url, face_send = face_client.AVATAR_URL, face_client.send
# No server answers: no answer means she consents, as documented.
face_client.AVATAR_URL = "http://127.0.0.1:9/event"
greeted = []
face_client.send = greeted.append
try:
    greeting_registry = _Hooks()
    face_client.face_hooks(greeting_registry)
    asyncio.run(_hook_for(greeting_registry, "SESSION_START")(_Fresh()))
finally:
    face_client.AVATAR_URL, face_client.send = saved_url, face_send
assert {"type": "action", "value": "greeting"} in greeted, greeted
resumed = []
face_client.send = resumed.append
face_client.AVATAR_URL = "http://127.0.0.1:9/event"
try:
    resumed_registry = _Hooks()
    face_client.face_hooks(resumed_registry)
    asyncio.run(_hook_for(resumed_registry, "SESSION_START")(_Resumed()))
finally:
    face_client.AVATAR_URL, face_client.send = saved_url, face_send
assert not any(message.get("type") == "action" for message in resumed), resumed

# Search paths: a user folder adds models and animations, and overrides shipped ones by name.
user = Path(tempfile.mkdtemp())
(user / "models").mkdir()
(user / "models" / "mine.vrm").write_bytes(b"mine")
(user / "anim" / "idle").mkdir(parents=True)
(user / "anim" / "idle" / "rest.vrma").write_bytes(b"override")
(user / "anim" / "actions").mkdir()
(user / "anim" / "actions" / "wave.vrma").write_bytes(b"wave")
(user / "anim" / "actions" / "greetings").mkdir()  # pools nest
(user / "anim" / "actions" / "greetings" / "hi.vrma").write_bytes(b"hi")
(user / "anim" / "bad pool").mkdir()  # not a pool name: ignored
(user / "anim" / "bad pool" / "x.vrma").write_bytes(b"x")
server.MODEL_DIRS = [user / "models"]
server.AUTONOMY_FILE = (
    user / "autonomy.json"
)  # hers, but never the developer's own file
server.MODEL_FILE = (
    user / "model"
)  # the same: a test must not leave a choice in the real closet
server.ANIM_HOME = (
    user / "anim-home"
)  # where the shipped clips are copied: never the developer's own folder
server.TEMPLATE_FILE = server.ANIM_HOME / ".template.json"
server.ANIM_DIRS = [user / "anim", server.HERE / "anim"]
assert server.anims()["idle"]["rest"].read_bytes() == b"override"
assert "Idle" in server.anims()["idle"]  # the shipped ones are still there
assert server.anims()["actions/greetings"]["hi"].read_bytes() == b"hi"
assert not any(" " in pool for pool in server.anims())

# The shipped clips are copied into the home folder, so the folder you edit is the one that
# plays. After that a copy follows the package only while it is still what was copied: an
# edited clip is the user's, and a manifest that is missing or unreadable reads as "all mine".
pkg = user / "pkg"  # a stand-in for the package's own anim folder
(pkg / "idle").mkdir(parents=True)
shipped = {"Idle": b"idle-v1", "Gone": b"gone-v1", "Stale": b"stale-v1", "Untouched": b"u-v1"}
for name, body in shipped.items():
    (pkg / "idle" / f"{name}.vrma").write_bytes(body)
server.ANIM_HOME = user / "seed-test"
server.TEMPLATE_FILE = server.ANIM_HOME / ".template.json"
assert server.seed_anims(source=pkg) == [
    f"copied idle/{name}.vrma" for name in sorted(shipped)
]
assert (server.ANIM_HOME / "idle" / "Idle.vrma").read_bytes() == b"idle-v1"
# A greeting is a folder the package ships nothing in, and the folder has to exist or there is
# nowhere to put one: seeding creates it, and it is not in the report because nothing was copied.
for pool in server.EVENT_POOLS:
    assert (server.ANIM_HOME / pool).is_dir(), pool
assert server.seed_anims(source=pkg) == []  # a second start has nothing left to do

(server.ANIM_HOME / "idle" / "Gone.vrma").write_bytes(b"mine")  # the user edits one
(pkg / "idle" / "Idle.vrma").write_bytes(b"idle-v2")  # the package improves another
assert server.seed_anims(source=pkg) == [
    "kept idle/Gone.vrma (edited)",
    "updated idle/Idle.vrma",
]
assert (server.ANIM_HOME / "idle" / "Gone.vrma").read_bytes() == b"mine"  # the edit survives
assert (server.ANIM_HOME / "idle" / "Idle.vrma").read_bytes() == b"idle-v2"  # the copy follows

# The package drops three names, and how each copy is treated depends on the manifest: an
# edited copy the manifest still knows is reported and kept, an untouched one is removed, and
# one whose digest the manifest dropped when the user edited it is simply not ours to touch.
(server.ANIM_HOME / "idle" / "Stale.vrma").write_bytes(b"mine-too")  # edited, still recorded
for name in ("Gone", "Stale", "Untouched"):
    (pkg / "idle" / f"{name}.vrma").unlink()
assert server.seed_anims(source=pkg, restore=True) == [
    "kept idle/Stale.vrma (edited, no longer shipped)",
    "removed idle/Untouched.vrma (no longer shipped)",
]
assert (server.ANIM_HOME / "idle" / "Gone.vrma").read_bytes() == b"mine"
assert (server.ANIM_HOME / "idle" / "Stale.vrma").read_bytes() == b"mine-too"
assert not (server.ANIM_HOME / "idle" / "Untouched.vrma").exists()

server.TEMPLATE_FILE.unlink()  # a lost manifest must not overwrite anything
(pkg / "idle" / "Gone.vrma").write_bytes(b"gone-v2")  # the name comes back, different
assert server.seed_anims(source=pkg) == ["kept idle/Gone.vrma (edited)"]
assert (server.ANIM_HOME / "idle" / "Gone.vrma").read_bytes() == b"mine"

# A clip you delete stays deleted -- that is how you get rid of one -- and `anim reset` is the
# way back, without touching the clips you edited.
(server.ANIM_HOME / "idle" / "Idle.vrma").unlink()
deleted = "kept idle/Idle.vrma deleted (bring it back with `zrb-avatar anim reset`)"
assert server.seed_anims(source=pkg) == ["kept idle/Gone.vrma (edited)", deleted]
assert not (server.ANIM_HOME / "idle" / "Idle.vrma").exists()
assert server.seed_anims(source=pkg) == ["kept idle/Gone.vrma (edited)", deleted]
# Deleted is deleted, not "fall back to the copy the package still ships": with the package
# folder and the home folder both in play, the name is gone from the pools.
saved_dirs = server.ANIM_DIRS
server.ANIM_DIRS = [pkg, server.ANIM_HOME]
assert "Idle" not in server.anims()["idle"]
server.ANIM_DIRS = saved_dirs
assert server.seed_anims(source=pkg, restore=True) == [
    "kept idle/Gone.vrma (edited)",
    "copied idle/Idle.vrma",
]
assert (server.ANIM_HOME / "idle" / "Idle.vrma").read_bytes() == b"idle-v2"
assert (server.ANIM_HOME / "idle" / "Gone.vrma").read_bytes() == b"mine"

# A white-labeled zrb runs under its own env prefix. The server imports no zrb, so it reads
# the prefix from zrb's bare bootstrap key `_ZRB_ENV_PREFIX`, the same variable zrb takes its
# own settings prefix from: one label, and her settings follow it.
saved_prefix = os.environ.pop(server.PREFIX_VAR, None)
assert server.env_name("PORT") == "ZRB_AVATAR_PORT"  # nothing set: zrb's own default
os.environ[server.PREFIX_VAR] = "ACME"
try:
    assert server.env_name("PORT") == "ACME_AVATAR_PORT"
    assert server.env_get("PORT", "7777") == "7777"  # ACME_AVATAR_PORT is not set
    os.environ["ACME_AVATAR_PORT"] = "8899"
    assert server.env_get("PORT") == "8899"
    assert server.env_get("PORT", "7777", prefix="OTHER") == "7777"  # an explicit prefix wins
finally:
    os.environ.pop("ACME_AVATAR_PORT", None)
    os.environ.pop(server.PREFIX_VAR, None)
    if saved_prefix is not None:
        os.environ[server.PREFIX_VAR] = saved_prefix

# The client's URL is <PREFIX>_AVATAR_URL. The retired <PREFIX>_FACE_URL is not read, so a
# leftover from a zrb-face setup cannot quietly point her at the old server.
saved_env = {k: os.environ.get(k) for k in ("ZRB_AVATAR_URL", "ZRB_FACE_URL")}
os.environ.pop(server.PREFIX_VAR, None)  # the client follows CFG.ENV_PREFIX, ZRB here
try:
    os.environ["ZRB_FACE_URL"] = "http://127.0.0.1:1/event"
    os.environ.pop("ZRB_AVATAR_URL", None)
    importlib.reload(face_client)
    assert face_client.AVATAR_URL == "http://127.0.0.1:7777/event", face_client.AVATAR_URL
    os.environ["ZRB_AVATAR_URL"] = "http://127.0.0.1:8899/event"
    importlib.reload(face_client)
    assert face_client.AVATAR_URL == "http://127.0.0.1:8899/event", face_client.AVATAR_URL
finally:
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if saved_prefix is not None:
        os.environ[server.PREFIX_VAR] = saved_prefix
    importlib.reload(face_client)

# Relay: a POSTed event reaches an open /events stream.
srv = server.serve("127.0.0.1", 0)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
listener = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
listener.request("GET", "/events")
stream = listener.getresponse()
time.sleep(0.2)  # let the stream register before posting
client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)


def get(path: str) -> tuple[int, bytes]:
    client.request("GET", path)
    response = client.getresponse()
    return response.status, response.read()


client.request("POST", "/event", '{"type":"state","value":"tool"}')
assert client.getresponse().status == 204
client.request("POST", "/event", "not json")
assert client.getresponse().status == 400
status, body = get("/?app")  # the desktop app's URL
assert status == 200 and b"<html" in body
assert json.loads(get("/models")[1]) == ["mine"]
pools = json.loads(get("/anims")[1])
assert (
    "wave" in pools["actions"] and "Jump" in pools["actions"]
)  # hers added to ours
assert "rest" in pools["idle"]
assert get("/mine.vrm") == (200, b"mine")
# A phone that lost its link at six of eighteen megabytes asks for the rest: the model route answers
# a byte range with 206 and the whole size it belongs to, so a stalled download resumes instead of
# starting the file again. A range it cannot serve is the whole file, which is always a correct
# answer to the question.
def get_range(path: str, asked: str | None = None) -> tuple[int, dict[str, str], bytes]:
    client.request("GET", path, headers={"Range": asked} if asked else {})
    response = client.getresponse()
    return response.status, dict(response.getheaders()), response.read()


status, headers, body = get_range("/mine.vrm", "bytes=2-")
assert (status, body) == (206, b"ne"), "a range is answered with something other than the rest"
assert (
    headers.get("Content-Range") == "bytes 2-3/4"
), "a resumed answer does not say which part of the file it is"
status, _, body = get_range("/mine.vrm", "bytes=0-1")
assert (status, body) == (206, b"mi"), "a bounded range is not the bytes it names"
status, _, body = get_range("/mine.vrm", "bytes=9-")
assert (status, body) == (200, b"mine"), "a range past the end is not the whole file"
assert (
    get_range("/mine.vrm")[1].get("Accept-Ranges") == "bytes"
), "a page is never told it may resume"

# Her notes live beside her model: {"covers": [...]} in <name>.json says what her outfit covers,
# so a close look at a hoodie is no longer read the same way as one at bare skin. A model with no
# notes file is a 404, which the page reads as "nothing covered" — today's behaviour without it.
assert get("/mine.json")[0] == 404
(user / "models" / "mine.json").write_text(
    '{"covers": ["chest", "hips", "thighs"]}', encoding="utf-8"
)
assert json.loads(get("/mine.json")[1]) == {"covers": ["chest", "hips", "thighs"]}
assert set(server.model_notes()) == {"mine"}
(user / "models" / "orphan.json").write_text('{"covers": []}', encoding="utf-8")
assert set(server.model_notes()) == {"mine"}  # no orphan.vrm: not a model's notes
assert get("/orphan.json")[0] == 404
# And the note is the one beside the model that name resolves to. Two folders: the earlier `.vrm`
# wins, so the `.json` left beside the losing one is nobody's notes — otherwise a hoodie's `covers`
# would answer for a bikini's chest, which is the misreading this file exists to prevent.
earlier = user / "earlier"
earlier.mkdir()
(earlier / "mine.vrm").write_bytes(b"earlier")
server.MODEL_DIRS = [earlier, user / "models"]
assert server.models()["mine"].read_bytes() == b"earlier"  # the earlier folder wins
assert server.model_notes() == {}  # nothing beside the model she will actually load
assert get("/mine.json")[0] == 404
server.MODEL_DIRS = [user / "models"]
assert json.loads(get("/mine.json")[1]) == {"covers": ["chest", "hips", "thighs"]}

assert get("/js/main.js") == (
    200,
    (server.HERE / "js" / "main.js").read_bytes(),
)  # the page's modules
assert get("/anim/idle/rest.vrma") == (200, b"override")
assert get("/anim/actions/wave.vrma") == (200, b"wave")
assert get("/anim/actions/greetings/hi.vrma") == (200, b"hi")
# Only listed files are served: nothing outside the folders, nothing by a made-up path.
for path in (
    "/missing.vrm",
    "/../face.py",
    "/server.py",
    "/anim/../server.py",
    "/anim/idle/%2e%2e/x.vrma",
    "/anim/idle/../../models/mine.vrm",
    "/models/mine.vrm",
    "/anim/actions/wave",
    "/anim/actions/greetings/../wave.vrma",
    "/anim/hi.vrma",
    "/anim/actions/nope/hi.vrma",
    "/js/../server.py",
    "/js/..",
    "/js/missing.js",
    "/js/main",
    "/js/x/../main.js",
):
    assert get(path)[0] == 404, path
assert json.loads(stream.readline().decode().removeprefix("data: ")) == {
    "type": "state",
    "value": "tool",
}

# Music is remembered: a page that connects later starts dancing right away.
client.request("POST", "/event", '{"type":"music","value":true}')
assert client.getresponse().status == 204
late = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
late.request("GET", "/events")
late_stream = late.getresponse()
assert json.loads(late_stream.readline().decode().removeprefix("data: ")) == {
    "type": "music",
    "value": True,
}


def post_event(body: str) -> int:
    client.request("POST", "/event", body)
    response = client.getresponse()
    response.read()
    return response.status


def next_event(events) -> dict:
    while not (line := events.readline().decode()).startswith("data: "):
        pass
    return json.loads(line.removeprefix("data: "))


# The model (avatar) is remembered too: a page that connects or reloads later and does not name
# one is told the chosen model. Only a model in the folders is taken; GET /model says the choice
# and what the open pages show.
(user / "models" / "yours.vrm").write_bytes(b"yours")
assert json.loads(get("/model")[1]) == {
    "chosen": None,
    "showing": [],
}  # the open pages didn't say
for bad in (
    '{"type":"model","value":"nope"}',
    '{"type":"model","value":"../mine"}',
    '{"type":"model","value":["mine"]}',
    '{"type":"model"}',
):
    assert post_event(bad) == 400, bad
assert server._model["value"] is None
assert post_event('{"type":"model","value":"yours"}') == 204
assert next_event(stream)["type"] == "music" and next_event(stream) == {
    "type": "model",
    "value": "yours",
}
page = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
page.request(
    "GET", "/events?model=mine"
)  # the page names a model: not the remembered one
page_stream = page.getresponse()
assert next_event(page_stream) == {"type": "music", "value": True}
# And then nothing about the model. A page that names one asked for it — `zrb-avatar mine` opens the
# window with it — so the remembered `yours` is not replayed over the top: the window would load
# what it was told and be switched a moment later, and the argument would be lost.
assert post_event('{"type":"state","value":"idle"}') == 204
assert next_event(page_stream) == {"type": "state", "value": "idle"}
assert next_event(stream) == {
    "type": "state",
    "value": "idle",
}  # this page asked for nothing: told once
assert json.loads(get("/model")[1]) == {"chosen": "yours", "showing": ["mine"]}

# A choice holds. `zrb-avatar music off` used to be undone by the watcher's very next poll —
# on macOS it asks every two seconds, so the switch appeared not to work at all. The watcher
# now goes on reporting what the system plays and `set_music` ignores it; `auto` hands the
# question back to the last thing the system said, without waiting for another poll.
assert json.loads(get("/music")[1]) == {
    "playing": True,
    "choice": True,
}  # the event above chose
assert post_event('{"type":"music","value":false}') == 204
assert next_event(stream) == {"type": "music", "value": False}
server.set_music(
    True
)  # media starts while the choice says no: the choice still decides
assert json.loads(get("/music")[1]) == {"playing": False, "choice": False}
assert post_event('{"type":"music","value":"auto"}') == 204
assert next_event(stream) == {"type": "music", "value": True}
assert json.loads(get("/music")[1]) == {"playing": True, "choice": None}
# "auto" is the CLI's word, not the page's: nothing but true or false reaches a page.
for bad in (
    '{"type":"music","value":"yes"}',
    '{"type":"music","value":1}',
    '{"type":"music"}',
):
    assert post_event(bad) == 400, bad
assert json.loads(get("/music")[1]) == {"playing": True, "choice": None}  # nothing took
# The choice and the watcher's answer are one decision, not two. The watcher runs in its own
# thread and a /event request in a thread of its own, so between `set_music`'s "is a choice in the
# way" and its write, a `music off` used to be able to land — and then be ignored forever, because
# a choice was set. Both hold `_music_lock` across the whole read-modify-write and the broadcast;
# inside the broadcast is where this checks it, because that is the whole window.
held, real = [], server.broadcast
server.broadcast = lambda message: held.append(
    server._music_lock.locked()
)  # and silent: no page
try:
    server.set_music(
        False
    )  # the watcher: no choice in the way, so it changes and says so
    server.choose_music(False)  # the user, at the same moment
    assert json.loads(get("/music")[1]) == {"playing": False, "choice": False}
    server.set_music(True)  # media starts again, as it does every few seconds
    assert json.loads(get("/music")[1]) == {
        "playing": False,
        "choice": False,
    }  # the choice decides
    server.choose_music(
        "auto"
    )  # back to what the system last said: True, from the line above
finally:
    server.broadcast = real
assert held == [
    True,
    True,
    True,
], held  # every one inside the lock: none can interleave
assert json.loads(get("/music")[1]) == {"playing": True, "choice": None}

# A pose she holds is hers to keep: the server remembers it, so a page that reloads does not lose
# it, and only a release (or another pose) lets go. A timed pose lets go on its own, so it is not
# kept — remembering it would bring back a posture that had already ended.
assert server._pose == {}
assert post_event('{"type":"pose","seconds":5,"head":"down"}') == 204
assert next_event(stream)["type"] == "pose"
assert server._pose == {}  # timed: not kept
assert post_event('{"type":"pose","hold":true,"rightHand":"chest"}') == 204
assert next_event(stream)["type"] == "pose"
assert server._pose == {"type": "pose", "hold": True, "rightHand": "chest"}
reloaded = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
reloaded.request("GET", "/events")
reloaded_events = reloaded.getresponse()
assert {"type": "pose", "hold": True, "rightHand": "chest"} in [
    next_event(reloaded_events) for _ in range(3)
]  # after music and model
assert post_event('{"type":"pose","release":true}') == 204
assert next_event(stream) == {"type": "pose", "release": True}
assert server._pose == {}
# And what a page has missed, plus joining the pages that hear what comes next, are one step: a
# pose or a release arriving between them would fall in neither, and the page would come up free
# while she holds — or holding what she has already let go of. This is that lock, from inside it.
replayed, real_replay = [], server._replay


def _watch_replay(client, chosen):
    replayed.append(server._lock.locked())
    return real_replay(client, chosen)


server._replay = _watch_replay
try:
    watcher = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    watcher.request("GET", "/events")
    assert (
        next_event(watcher.getresponse())["type"] == "music"
    )  # it connected, so the replay ran
finally:
    server._replay = real_replay
assert replayed == [True], replayed

# What she agrees to do on her own: five switches, each at its default until she says otherwise, and
# the answer kept in a file because a consent withdrawn tonight is not something tomorrow's restart
# should quietly restore. Only dance starts off -- it answers the media watcher rather than something
# the user did, and the watcher cannot tell a song from a lecture. `cover` is the one that moves her
# body without anyone asking: a no to it is a no to covering herself, and a pose already held comes
# down rather than staying up on a consent she has withdrawn.
defaults = {
    "dance": False,
    "blush": True,
    "emotion": True,
    "greeting": True,
    "cover": True,
}
assert json.loads(get("/autonomy")[1]) == defaults
for bad in (
    '{"type":"autonomy","value":{"danse":false}}',
    '{"type":"autonomy","value":{"dance":"no"}}',
    '{"type":"autonomy","value":true}',
    '{"type":"autonomy","value":[false]}',
    '{"type":"autonomy"}',
):
    assert post_event(bad) == 400, bad
assert (
    json.loads(get("/autonomy")[1]) == defaults
)  # a typo is refused, not half-applied
assert post_event('{"type":"autonomy","value":{"greeting":false}}') == 204
assert next_event(stream) == {
    "type": "autonomy",
    "value": {**defaults, "greeting": False},
}
assert json.loads(server.AUTONOMY_FILE.read_text()) == {
    "greeting": False
}  # only what she moved
server._autonomy["greeting"] = True  # as if this process had just started
server.load_autonomy()
assert json.loads(get("/autonomy")[1])["greeting"] is False  # the file is believed
server.AUTONOMY_FILE.write_text("not json", encoding="utf-8")
server._autonomy["greeting"] = True
server.load_autonomy()
assert (
    json.loads(get("/autonomy")[1]) == defaults
)  # unreadable is not a refusal: the defaults
# A file written by a version that stored every switch stays authoritative. A `dance: true` in one
# was neither chosen nor unchosen, and an upgrade does not get to guess which, so it is kept: the
# new default reaches the switches a file never mentions, never a value that is written down.
server.AUTONOMY_FILE.write_text(
    json.dumps({"dance": True, "blush": True, "emotion": True, "greeting": True}),
    encoding="utf-8",
)
server._autonomy.update(dict(server.AUTONOMY_DEFAULTS))
server.load_autonomy()
assert (
    json.loads(get("/autonomy")[1])["dance"] is True
)  # a legacy file is read, not reinterpreted
server.AUTONOMY_FILE.write_text(
    "{}", encoding="utf-8"
)  # nothing moved: every switch at its default
server._autonomy.update(dict(server.AUTONOMY_DEFAULTS))
server.load_autonomy()
assert json.loads(get("/autonomy")[1]) == defaults
# A page that connects while she has moved a switch is told, because it assumes the defaults. With
# every switch at its default there is nothing to say, which is why no other replay carries one.
assert post_event('{"type":"autonomy","value":{"blush":false}}') == 204
assert next_event(stream)["type"] == "autonomy"
holding = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
holding.request("GET", "/events")
holding_events = holding.getresponse()
assert {"type": "autonomy", "value": {**defaults, "blush": False}} in [
    next_event(holding_events) for _ in range(3)
]  # music and the model come first, as before
assert post_event('{"type":"autonomy","value":{"blush":true}}') == 204
assert (
    next_event(stream)["type"] == "autonomy"
)  # back to its default: the pages are told that too
# The home folder may not exist yet -- a ZRB_AVATAR_HOME that only ever had models elsewhere -- and
# a switch that cannot be written down is not applied at all: the file is what a restart believes,
# so keeping a state the file disagrees with would be a lie to the next run.
fresh = user / "newhome" / "autonomy.json"
server.AUTONOMY_FILE, remembered = fresh, server.AUTONOMY_FILE
assert post_event('{"type":"autonomy","value":{"blush":false}}') == 204
assert json.loads(fresh.read_text())["blush"] is False  # the folder was made for it
assert json.loads(get("/autonomy")[1])["blush"] is False
assert next_event(stream)["type"] == "autonomy"
(user / "not-a-dir").write_text(
    "", encoding="utf-8"
)  # a file where the folder would go
server.AUTONOMY_FILE = user / "not-a-dir" / "autonomy.json"
assert post_event('{"type":"autonomy","value":{"blush":true}}') == 400
assert (
    json.loads(get("/autonomy")[1])["blush"] is False
)  # not remembered, so not taken either
server.AUTONOMY_FILE = remembered
assert post_event('{"type":"autonomy","value":{"blush":true}}') == 204
assert next_event(stream)["type"] == "autonomy"
assert json.loads(get("/autonomy")[1]) == defaults


# One change at a time. Two posts arriving together used to share `autonomy.json.new`: one would
# fail on the other's rename, answer 400 for good input, and roll back over the change that had
# just won. Four at once now, watched from inside the write: every one holds the lock, and what is
# left behind is a state the file agrees with.
class _WatchedFile:
    """Stands in for the file, and notes whether the lock is held while it is being written.

    A `Path` subclass would be shorter, but `_flavour` moved between Python versions — 3.11 wants
    it on the subclass, 3.14 has removed it — and the suite runs on 3.11 through 3.14. This only
    has to be the part of a Path that `choose_autonomy` and the assertions use.
    """

    held = []

    def __init__(self, real: Path) -> None:
        self.real = real

    parent = property(lambda self: self.real.parent)
    name = property(lambda self: self.real.name)

    def with_name(self, name: str):
        return _WatchedFile(self.real.with_name(name))

    def write_text(self, *args, **kwargs):
        _WatchedFile.held.append(server._autonomy_lock.locked())
        return self.real.write_text(*args, **kwargs)

    def read_text(self, *args, **kwargs):
        return self.real.read_text(*args, **kwargs)

    def replace(self, target):
        return self.real.replace(
            target.real if isinstance(target, _WatchedFile) else target
        )


watched_file = _WatchedFile(user / "autonomy.json")
server.AUTONOMY_FILE, remembered = watched_file, server.AUTONOMY_FILE
refused = []


def _choose(on):
    try:
        server.choose_autonomy({"dance": on})
    except ValueError as e:
        refused.append(e)


try:
    racers = [
        threading.Thread(target=_choose, args=(on,))
        for on in (False, True, False, True)
    ]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()
    assert (
        refused == []
    ), refused  # four good answers, not one and three "could not remember"
    assert _WatchedFile.held == [True] * 4, _WatchedFile.held
    assert {**defaults, **json.loads(watched_file.read_text())} == json.loads(
        get("/autonomy")[1]
    )
finally:
    server.AUTONOMY_FILE = remembered
assert post_event('{"type":"autonomy","value":{"dance":true}}') == 204
assert json.loads(get("/autonomy")[1]) == {**defaults, "dance": True}
assert (
    post_event('{"type":"autonomy","value":{"dance":false}}') == 204
)  # back to its default,
# so the replay the assertions below read has nothing to say about autonomy: music alone
assert json.loads(get("/autonomy")[1]) == defaults
# The model she chose is remembered, for the reason the autonomy switches are: without it a restart
# puts back the first name in the folder, which is alphabetical order rather than a decision.
assert post_event('{"type":"model","value":"mine"}') == 204
assert (
    server.MODEL_FILE.read_text(encoding="utf-8") == "mine"
)  # written down, so a restart believes it
assert json.loads(get("/model")[1])["chosen"] == "mine"
assert (
    post_event('{"type":"model","value":"nobody-has-this"}') == 400
)  # a name she lacks is refused
assert server.MODEL_FILE.read_text(encoding="utf-8") == "mine"  # and not written over
server._model["value"] = None  # as if this process had just started
server.load_model()
assert (
    json.loads(get("/model")[1])["chosen"] == "mine"
)  # the file is what a restart believes
server.MODEL_FILE.write_text("no-longer-installed", encoding="utf-8")
server._model["value"] = None
server.load_model()
assert (
    json.loads(get("/model")[1])["chosen"] is None
)  # a memory of a model she no longer has
server.MODEL_FILE.write_bytes(b"\xff\xfe not utf-8")  # half written, or synced over
server._model["value"] = None
server.load_model()  # undecodable is unreadable: the server still starts, with no choice
assert json.loads(get("/model")[1])["chosen"] is None
(user / "models" / " spaced .vrm").write_bytes(
    b"spaced"
)  # NAME allows spaces around a name
server.MODEL_FILE.write_text(" spaced ", encoding="utf-8")
server._model["value"] = None
server.load_model()
assert (
    json.loads(get("/model")[1])["chosen"] == " spaced "
)  # trimmed nothing but a line ending
(
    user / "models" / " spaced .vrm"
).unlink()  # the rest of this file counts on two models
server.MODEL_FILE = user / "not-a-dir" / "model"  # a file where the folder would go
assert (
    post_event('{"type":"model","value":"mine"}') == 400
)  # not remembered, so not taken either
assert json.loads(get("/model")[1])["chosen"] is None
server.MODEL_FILE = user / "model"  # back to the test's own file, never the developer's
server._model["value"] = "yours"  # the state the rest of this file was written against
face_client.AVATAR_URL, saved = f"http://127.0.0.1:{port}/event", face_client.AVATAR_URL
try:  # what AvatarListModels and AvatarModel ask
    assert face_client.ask("models") == ["mine", "yours"]
    # `mine`: the page opened with `?model=mine` is still open, and it stays on what it asked for
    # while `yours` is what is remembered — the two answers are different on purpose.
    assert face_client.ask("model") == {"chosen": "yours", "showing": ["mine"]}
finally:
    face_client.AVATAR_URL = saved
face_client.AVATAR_URL, saved = "http://127.0.0.1:9/event", face_client.AVATAR_URL
assert face_client.ask("models", timeout=1) is None  # no server: None, no exception
face_client.AVATAR_URL = saved
(
    user / "models" / "yours.vrm"
).unlink()  # gone from the folder: no longer chosen, nor sent
assert json.loads(get("/model")[1])["chosen"] is None
again = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
again.request("GET", "/events")
again_stream = again.getresponse()
assert next_event(again_stream) == {"type": "music", "value": True}
assert post_event('{"type":"state","value":"idle"}') == 204
assert next_event(again_stream) == {
    "type": "state",
    "value": "idle",
}  # no model in between
# A view is kept for zrb, counted, and not relayed to the pages (the next event they get is the state).
# The reflex is pinned off for it rather than assumed: with ZRB_AVATAR_REFLEX=on in the shell (Go's
# is), a shy view is answered with her covering pose, and that pose -- not the state -- would be next.
reflex = server.REFLEX
server.REFLEX = False
try:
    assert json.loads(get("/view")[1]) == {"n": 0}
    assert (
        post_event('{"type":"view","part":"chest","side":"front","close":true,"shy":true}')
        == 204
    )
    assert json.loads(get("/view")[1]) == {
        "type": "view",
        "part": "chest",
        "side": "front",
        "close": True,
        "shy": True,
        "n": 1,
    }
    assert post_event('{"type":"state","value":"tool"}') == 204
    assert next_event(again_stream) == {"type": "state", "value": "tool"}
finally:
    server.REFLEX = reflex

# A blink she chose is relayed and forgotten, like a pose: a page that reloads does not start with
# her eyes shut for a blink that has already happened.
assert post_event('{"type":"blink","eyes":"right","seconds":0.3}') == 204
assert next_event(again_stream) == {"type": "blink", "eyes": "right", "seconds": 0.3}
assert server._pose == {}

# Actions are listed with what each looks like, through the same cascade: the user's own
# actions/wave.vrma (no description) is there, and the shipped ones fill in, described.
listed = {f"{a['pool']}/{a['name']}": a for a in json.loads(get("/actions")[1])}
assert (
    listed["actions/wave"]["description"] == ""
    and listed["actions/wave"]["seconds"] is None
)  # not glTF
assert listed["actions/Jump"]["seconds"] > 1
assert listed["idle/rest"]["pool"] == "idle"  # every pool, not just the actions
assert list(listed)[0].startswith("actions/")  # the actions first
assert set(listed) == {f"{p}/{n}" for p, names in server.anims().items() for n in names}

# A clip's file of the wrong shape (readable glTF JSON, but not what a .vrma holds) lists with no
# description or length instead of breaking the listing of the rest.


def glb(doc) -> bytes:
    chunk = json.dumps(doc).encode()
    chunk += b" " * (-len(chunk) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 20 + len(chunk))
        + struct.pack("<I4s", len(chunk), b"JSON")
        + chunk
    )


odd = user / "anim" / "actions" / "odd"
odd.mkdir()
shapes = {
    "list": [],
    "string-animation": {"animations": ["x"]},
    "list-extras": {"animations": [{"extras": [1]}]},
    "number-description": {"animations": [{"extras": {"description": 5}}]},
    "string-samplers": {"animations": [{"samplers": "x"}]},
    "text-max": {
        "animations": [{"samplers": [{"input": 0}]}],
        "accessors": [{"max": ["7"]}],
    },
    "true-max": {
        "animations": [{"samplers": [{"input": 0}]}],
        "accessors": [{"max": [True]}],
    },
    "inf-max": {
        "animations": [{"samplers": [{"input": 0}]}],
        "accessors": [{"max": [1e400]}],
    },
}
for name, doc in shapes.items():
    (odd / f"{name}.vrma").write_bytes(glb(doc))
(odd / "truncated.vrma").write_bytes(b"glTF")
(odd / "described.vrma").write_bytes(
    glb({"animations": [{"extras": {"description": "ok"}, "samplers": "x"}]})
)
listing = json.loads(get("/actions")[1])  # the listing still answers
odd_info = {c["name"]: c for c in listing if c["pool"] == "actions/odd"}
assert set(odd_info) == {*shapes, "truncated", "described"}
assert all(c["seconds"] is None for c in odd_info.values())
assert all(c["description"] == "" for n, c in odd_info.items() if n != "described")
assert odd_info["described"]["description"] == "ok"  # what is right is kept
assert any(
    c["name"] == "Jump" and c["seconds"] for c in listing
)  # and the rest listed as before
for file in odd.iterdir():
    file.unlink()
odd.rmdir()

# Names to play a clip by: the full pool/name always means that clip, and an action's short name
# is offered only where it is not some clip's full name. A user's actions/idle/Idle doesn't
# take "idle/Idle" from the idle loop; it is asked for, and listed, by its full path.
(user / "anim" / "actions" / "idle").mkdir()
(user / "anim" / "actions" / "idle" / "Idle.vrma").write_bytes(b"not the loop")
names = server.playable()
assert names["idle/Idle"]["pool"] == "idle"
assert names["actions/idle/Idle"]["pool"] == "actions/idle"
assert (
    names["wave"]["pool"] == "actions"
    and names["greetings/hi"]["pool"] == "actions/greetings"
)
assert (
    server.short_name(names["actions/idle/Idle"], names) == "actions/idle/Idle"
)
assert server.short_name(names["greetings/hi"], names) == "greetings/hi"
assert server.short_name(names["idle/Idle"], names) == "idle/Idle"
(user / "anim" / "actions" / "idle" / "Idle.vrma").unlink()

# A request body's length is checked before it is read: not a number or negative is 400, too big
# is 413, on both POST endpoints; none of them waits for a body that never comes.
import socket  # noqa: E402


def post_raw(path: str, length: str) -> int:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(
            f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n\r\n".encode()
        )
        answer = b""
        while chunk := raw.recv(4096):  # all of it: the server closes after an error
            answer += chunk
        return int(answer.split()[1])


server._snapshots["waiting"] = {"done": threading.Event(), "image": b"", "about": {}}
try:
    for path, too_big in (
        ("/event", server.MAX_EVENT_BYTES + 1),
        ("/snapshot?id=waiting", server.MAX_SNAPSHOT_BYTES + 1),
    ):
        for length, code in (("nope", 400), ("-1", 400), (str(too_big), 413)):
            assert post_raw(path, length) == code, (path, length)
    assert not server._snapshots["waiting"]["done"].is_set()
finally:
    server._snapshots.pop("waiting")

# And a body that stops short of the length it declared is not a message: the part that arrived put to
# zrb as a typed line would be a truncated sentence, and the page would believe it had been sent.
def post_truncated(path: str, declared: int, sent: bytes) -> int:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {declared}\r\n\r\n".encode() + sent)
        raw.shutdown(socket.SHUT_WR)  # the page went away in the middle of the line
        answer = b""
        while chunk := raw.recv(4096):
            answer += chunk
        return int(answer.split()[1])


assert post_truncated("/text", 40, b"only this much") == 400, "a cut-off body became a message"

# AvatarLook's picture: GET /snapshot asks the open window, which answers with a PNG and what she
# is doing; a made-up id, a non-PNG or an oversized answer is refused.
import base64  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"


def fake_window():
    """A page: reads the event stream and answers the first snapshot request."""
    page = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    page.request("GET", "/events")
    events = page.getresponse()
    while True:
        line = events.readline().decode()
        if line.startswith("data: ") and '"snapshot"' in line:
            key = json.loads(line.removeprefix("data: "))["id"]
            post = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            for bad, code in (
                ('{"image": "data:image/jpeg;base64,AAAA"}', 400),
                ("not json", 400),
            ):
                post.request("POST", f"/snapshot?id={key}", bad)
                assert post.getresponse().status == code
            body = json.dumps(
                {
                    "image": "data:image/png;base64," + base64.b64encode(PNG).decode(),
                    "about": {"state": "idle", "playing": "idle/wait"},
                }
            )
            post.request("POST", f"/snapshot?id={key}", body)
            assert post.getresponse().status == 204
            return


threading.Thread(target=fake_window, daemon=True).start()
time.sleep(0.3)  # let it connect
client.request("GET", "/snapshot")
shot = client.getresponse()
assert (
    shot.status == 200
    and shot.getheader("Content-Type") == "image/png"
    and shot.read() == PNG
)
assert json.loads(shot.getheader("X-Face")) == {
    "state": "idle",
    "playing": "idle/wait",
    "chosen": None,
}  # chosen: which avatar is meant to be showing; none is remembered in this home
client.request(
    "POST", "/snapshot?id=made-up", '{"image": "data:image/png;base64,AAAA"}'
)
assert client.getresponse().status == 404  # nobody asked for that one
assert server.take_snapshot(timeout=0.3) is None  # pages open, but none answers
# A reloading window is not a closed window: a page that connects after the ask still answers,
# which is what makes a look work again a moment after the window reopens (and why the old
# "no page is open" answer was a lie during a reload).
threading.Thread(target=lambda: (time.sleep(0.6), fake_window()), daemon=True).start()
early = time.monotonic()
waited = server.take_snapshot(timeout=3)
assert waited is not None and waited[0] == PNG
assert time.monotonic() - early > 0.4  # it waited for the page rather than give up
url = face_client.AVATAR_URL
face_client.AVATAR_URL = f"http://127.0.0.1:{port}/event"
try:
    threading.Thread(target=fake_window, daemon=True).start()
    time.sleep(0.3)
    assert face_client.snapshot() == (
        PNG,
        {"state": "idle", "playing": "idle/wait", "chosen": None},
    )
finally:
    face_client.AVATAR_URL = url
face_client.AVATAR_URL, saved = "http://127.0.0.1:9/event", face_client.AVATAR_URL
assert face_client.snapshot(timeout=1) is None  # no server: no picture, no exception
face_client.AVATAR_URL = saved

# zrb's remote camera: GET /frame asks the open page for one photo from a camera this machine does
# not have, and the page answers with a JPEG -- or with the sentence saying why it could not, which
# travels as the reason phrase, so /photo can say something the user can act on.
JPEG = b"\xff\xd8\xff\xe0a phone's photo, near enough"
# A name nobody has reported is asked of every page and waits out the ask, since the page with the
# camera may not have reported it yet. That is the right wait and a slow test, so the ask is short
# for this section -- the handler reads the module's constant, not a copy of it.
saved_frame_seconds = server.FRAME_SECONDS
server.FRAME_SECONDS = 2.0


def photographer(answer, wait=0.0, name=''):
    """A page: joins the event stream, then answers the first frame request with *answer*.

    *wait* is how long the page takes to answer, which is how a test puts two pages in order. A
    page that names itself says so on its stream, which is how the server knows which page has
    which camera: two pages are normal now -- a laptop and a phone.
    """
    page = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    page.request("GET", f"/events?page={name}" if name else "/events")
    events = page.getresponse()
    while True:
        line = events.readline().decode()
        if line.startswith("data: ") and '"frame"' in line:
            key = json.loads(line.removeprefix("data: "))["id"]
            if wait:
                time.sleep(wait)
            post = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            post.request("POST", f"/frame?id={key}", json.dumps(answer))
            assert post.getresponse().status == 204
            return


threading.Thread(
    target=photographer,
    args=({"image": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()},),
    daemon=True,
).start()
time.sleep(0.3)  # let it connect
client.request("GET", "/frame?device=phone-back")
photo = client.getresponse()
assert photo.status == 200 and photo.getheader("Content-Type") == "image/jpeg"
assert photo.read() == JPEG
# A page that cannot use its camera says why: the refusal is a status, and the sentence is the
# reason zrb repeats rather than a photo that is simply missing.
REFUSAL = "the page was not allowed to use its camera"
threading.Thread(target=photographer, args=({"refusal": REFUSAL},), daemon=True).start()
time.sleep(0.3)
client.request("GET", "/frame?device=phone-back")
refused = client.getresponse()
assert refused.status == 503 and REFUSAL in refused.reason
refused.read()
# An id nobody asked for is refused; so is a body that is not a JPEG; and a page with neither a
# photo nor a reason still says something, since a failure with no words is the one zrb cannot pass
# on to the user.
client.request("POST", "/frame?id=made-up", '{"image": "data:image/jpeg;base64,AAAA"}')
assert client.getresponse().status == 404
server._frames["waiting"] = {"done": threading.Event(), "image": b"", "refusals": []}
try:
    for body, code in (
        ('{"image": "data:image/png;base64,AAAA"}', 400),
        ("not json", 400),
        ("[]", 400),
        ("{}", 204),
    ):
        client.request("POST", "/frame?id=waiting", body)
        assert client.getresponse().status == code, body
    assert server._frames["waiting"]["refusals"]  # a sentence, not an empty failure
    for length, code in (("nope", 400), ("-1", 400), (str(server.MAX_FRAME_BYTES + 1), 413)):
        assert post_raw("/frame?id=waiting", length) == code
finally:
    server._frames.pop("waiting")
assert server.take_frame("nothing", timeout=0.3) == (b"", "")  # no page answers: no photo

# The names the pages give their cameras, by the page that gave them. A page labels a camera only
# once it has been allowed one, so this is a report rather than a discovery, and the last report
# from a page is the whole truth about that page.
client.request(
    "POST",
    "/frame/devices?page=phone",
    json.dumps({"devices": ["back camera", "front camera", "  "]}),
)
assert client.getresponse().status == 204
client.request("GET", "/frame/devices")
assert json.loads(client.getresponse().read()) == {"devices": ["back camera", "front camera"]}
assert server.page_cameras() == ["back camera", "front camera"]
# A second page's names are its own: they are offered beside the phone's, and the laptop's report
# neither erases nor stands in for the phone's.
client.request(
    "POST", "/frame/devices?page=laptop", json.dumps({"devices": ["FaceTime HD"]})
)
assert client.getresponse().status == 204
assert server.page_cameras() == ["back camera", "front camera", "FaceTime HD"]
client.request("POST", "/frame/devices?page=phone", json.dumps({"devices": []}))
assert client.getresponse().status == 204
assert server.page_cameras() == ["FaceTime HD"]  # the phone's grant went; the laptop still has one
client.request("POST", "/frame/devices", json.dumps(["not", "a report"]))
assert client.getresponse().status == 400  # a report that is not one changes nothing
assert server.page_cameras() == ["FaceTime HD"]

# Two pages, one photo. The page without the camera answers first, and its refusal must not become
# the answer: what ends the ask is a photo, or every page asked having said it cannot.
threading.Thread(target=photographer, args=({"refusal": REFUSAL},), daemon=True).start()
threading.Thread(
    target=photographer,
    args=({"image": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()}, 0.6),
    daemon=True,
).start()
time.sleep(0.5)  # both pages are open before anything is asked
client.request("GET", "/frame?device=back%20camera")
photo = client.getresponse()
assert photo.status == 200 and photo.read() == JPEG  # the photo, not the refusal that came first
# And a name belongs to the page that reported it, so the other page is not asked at all: a laptop
# with its own camera would answer at once, and it is not the camera that was asked for.
LAPTOP_JPEG = b"\xff\xd8\xff\xe0the laptop's own camera, near enough"
for page, devices in (("laptop", ["FaceTime HD"]), ("phone", ["back camera"])):
    client.request(
        "POST", f"/frame/devices?page={page}", json.dumps({"devices": devices})
    )
    assert client.getresponse().status == 204
for name, image in (("laptop", LAPTOP_JPEG), ("phone", JPEG)):
    threading.Thread(
        target=photographer,
        args=({"image": "data:image/jpeg;base64," + base64.b64encode(image).decode()},
              0.1 if name == "laptop" else 0.5, name),
        daemon=True,
    ).start()
time.sleep(0.5)
client.request("GET", "/frame?device=back%20camera")
photo = client.getresponse()
assert photo.status == 200 and photo.read() == JPEG  # the phone's, though the laptop answered sooner

# A label two pages report is nobody's camera, and the list says so: it is offered once per page,
# qualified, and asked for bare it is refused before any page is asked at all -- the first photo to
# arrive would be the right answer only half the time. Every other name stays exactly as it was,
# because a name in zrb's completion is one somebody has already learned and typed.
SHARED = "shared cam"
for page, devices in (
    ("phone", ["back camera", SHARED]),
    ("laptop", ["FaceTime HD", SHARED]),
):
    client.request(
        "POST", f"/frame/devices?page={page}", json.dumps({"devices": devices})
    )
    assert client.getresponse().status == 204
assert sorted(server.page_cameras()) == sorted(
    ["back camera", "FaceTime HD", f"{SHARED} (phone)", f"{SHARED} (laptop)"]
), server.page_cameras()
assert SHARED not in server.page_cameras()  # the bare name is offered for neither page alone


def ask_frame(name: str) -> tuple[int, str, bytes]:
    """One GET /frame for a camera, by the name zrb would ask with: status, reason, body."""
    client.request("GET", "/frame?device=" + name.replace(" ", "%20"))
    answer = client.getresponse()
    return answer.status, answer.reason, answer.read()


status, sentence, _ = ask_frame(SHARED)
assert status == 503, (status, sentence)  # refused, not taken from whichever page answered first
assert "phone" in sentence and "laptop" in sentence, sentence
assert f"{SHARED} (phone)" in sentence and f"{SHARED} (laptop)" in sentence, sentence
# And the qualified name is a name that picks a page: the laptop answers at once with its own
# camera, and the photo that comes back is still the phone's.
for name, image, wait in (("laptop", LAPTOP_JPEG, 0.1), ("phone", JPEG, 0.5)):
    threading.Thread(
        target=photographer,
        args=(
            {"image": "data:image/jpeg;base64," + base64.b64encode(image).decode()},
            wait,
            name,
        ),
        daemon=True,
    ).start()
time.sleep(0.5)
status, _, body = ask_frame(f"{SHARED} (phone)")
assert status == 200 and body == JPEG  # the phone's, though the laptop answered sooner
server.FRAME_SECONDS = saved_frame_seconds  # the real deadline back, for anything after this

# A page's cameras go with the page. A report kept for a page that has gone would grow by one set of
# names per reload, and `/frame/devices` would keep offering cameras from tabs that are closed.
leaving = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
leaving.request("GET", "/events?page=a-page-that-leaves")
leaving.getresponse()
time.sleep(0.2)
client.request(
    "POST",
    "/frame/devices?page=a-page-that-leaves",
    json.dumps({"devices": ["a camera that left"]}),
)
assert client.getresponse().status == 204
assert "a camera that left" in server.page_cameras()
leaving.close()  # gone without saying anything, which is how a closed tab goes
# The server finds a page that has gone by writing to it, so it has to be written to twice: the
# first write goes out unanswered and the second meets the reset.
for _ in range(3):
    server.broadcast(json.dumps({"type": "state", "value": "idle"}))
    time.sleep(0.15)
assert "a camera that left" not in server.page_cameras()

# The watcher: a player's line becomes a music event, and the state is only sent when it changes.
server.set_music(False)
server.watch_music(
    [sys.executable, "-c", "print('Playing')"]
)  # sys.executable: Windows has no /bin/sh
assert server._music["value"] is True
server.watch_music([sys.executable, "-c", "print('Paused')"])
assert server._music["value"] is False

import subprocess  # noqa: E402

# macOS: osascript answers, and a slow or missing one must not hang or crash the watcher.
from types import SimpleNamespace  # noqa: E402

which = server.shutil.which
server.shutil.which = lambda name: None  # without media-control, AppleScript answers
try:
    assert (
        server.macos_media(run=lambda *a, **k: SimpleNamespace(stdout=" Playing \n"))
        == "Playing"
    )
    assert server.macos_media(run=lambda *a, **k: SimpleNamespace(stdout="")) is None

    def _hang(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 1)

    assert (
        server.macos_media(run=_hang) is None
    )  # a permission prompt that never answers

    # With media-control (it also sees a browser tab playing), its JSON decides; anything it
    # can't answer, or answers oddly, falls back to AppleScript rather than stopping the watcher.
    server.shutil.which = lambda name: (
        "/usr/local/bin/media-control" if name == "media-control" else None
    )

    def _media(media_out, media_code=0, script_out="Paused"):
        def run(command, **kwargs):
            if command[0].endswith("media-control"):
                return SimpleNamespace(stdout=media_out, returncode=media_code)
            return SimpleNamespace(stdout=script_out, returncode=0)

        return run

    # media-control's own output (a YouTube tab in Chrome), playing and paused:
    tab = '{"title": "x", "bundleIdentifier": "com.google.Chrome", "playing": %s, "playbackRate": %s}'
    assert server.macos_media(run=_media(tab % ("true", 1))) == "Playing"
    assert server.macos_media(run=_media(tab % ("false", 0))) == "Paused"
    assert (
        server.macos_media(run=_media('{"is_playing": true}')) == "Playing"
    )  # older versions
    assert (
        server.macos_media(run=_media('{"title": "x", "playbackRate": 1}')) == "Playing"
    )
    assert (
        server.macos_media(run=_media('{"title": "x"}', script_out="Playing"))
        == "Playing"
    )  # no flag: AppleScript
    assert (
        server.macos_media(run=_media("null")) == "Paused"
    )  # nothing is playing at all
    for odd in (
        "",
        "not json",
        "[1, 2]",
        "3",
    ):  # unreadable or unexpected: ask AppleScript
        assert (
            server.macos_media(run=_media(odd, script_out="Playing")) == "Playing"
        ), odd
    assert (
        server.macos_media(
            run=_media('{"is_playing": true}', media_code=1, script_out="Paused")
        )
        == "Paused"
    )
finally:
    server.shutil.which = which
assert "Spotify" in server._MACOS_MEDIA and "Music" in server._MACOS_MEDIA
assert (
    "is running" in server._MACOS_MEDIA
)  # never starts a player it is only asking about

# The animations shipped in the package: real glTF, valid references, and every channel loops.
import re  # noqa: E402

anim = server.HERE / "anim"


def vrma(path: Path) -> dict:
    """Read a .vrma, checking it is a glTF binary with valid channel references."""
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    assert magic == b"glTF" and version == 2 and length == len(data), path
    chunks, offset = {}, 12
    while offset < len(data):
        size, kind = struct.unpack_from("<I4s", data, offset)
        chunks[kind] = data[offset + 8 : offset + 8 + size]
        offset += 8 + size
    gltf = json.loads(chunks[b"JSON"])
    binary = chunks[b"BIN\0"]
    assert "VRMC_vrm_animation" in gltf["extensionsUsed"], path
    humanoid = gltf["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]
    assert "hips" in humanoid, path
    (animation,) = gltf["animations"]
    frames = None
    for channel in animation["channels"]:
        sampler = animation["samplers"][channel["sampler"]]
        times, values = (
            gltf["accessors"][sampler["input"]],
            gltf["accessors"][sampler["output"]],
        )
        assert (
            times["count"] == values["count"] and values["componentType"] == 5126
        ), path
        frames = frames or times["count"]
        assert times["count"] == frames, path
        view = gltf["bufferViews"][values["bufferView"]]
        start = view.get("byteOffset", 0) + values.get("byteOffset", 0)
        assert start + view["byteLength"] <= len(binary), path
    assert frames and frames > 1, path
    return gltf


pools = sorted(p.name for p in anim.iterdir() if p.is_dir())
assert pools == [
    "actions",
    "attention",
    "dance",
    "idle",
    "permission",
    "speaking",
    "thinking",
    "tool",
], pools
for pool in pools:
    files = sorted((anim / pool).glob("*.vrma"))
    assert files, f"nothing to play in anim/{pool}"
    for file in files:
        vrma(file)

server.ANIM_DIRS = [server.HERE / "anim"]

# Every state the page shows has either its own pool or the idles to fall back to: motion.js loops
# `idle` for a pool with no clip in it, which is what keeps a home folder that lost one working.
page = (server.HERE / "js" / "main.js").read_text()
states = re.findall(r"(\w+)\s*:", re.search(r"const LOOK = \{(.*?)\}", page).group(1))
for state in [*states, "dance", "actions"]:
    have = server.anims()
    assert any(have.get(state, {}) or have.get("idle", {})), f"no clip for the {state} pool"

# The page still listens for its keys, and the two a hand has to press without a keyboard are buttons on
# its row (checked below). What the keys are is README.md's table now, not a hint standing over her
# boots: on 2026-11-28 Go asked for that line out, since a page whose foot is a row of buttons does not
# need a sentence naming keys. Nothing starts on load — a browser refuses a microphone and a sound
# before a gesture — and the two buttons are the gesture.
page_html = (server.HERE / "face.html").read_text(encoding="utf-8")
readme = (server.HERE.parent / "README.md").read_text(encoding="utf-8")
keys = re.findall(r"e\.key !== '([^']+)'", page)
assert keys, "the page listens for no key at all"
for key in keys:
    assert f"| `{key}` |" in readme, f"the page handles {key!r} and no table says so"
# The foot of the page is one stack in flow, whose height is measured rather than guessed. The status
# line used to be placed by its own `bottom`, a number tuned for two buttons and a hint: the box that
# typed lines added arrived underneath it and the line ended up behind the box's top border, reported
# from the phone on 2026-11-21 as "the UI is a bit misaligned".
assert 'id="bottom"' in page_html and "--bottom-h" in page_html, (
    "the status line and the keys are placed by two numbers that can drift apart"
)
assert not re.search(r"#status\s*\{[^}]*\bbottom\s*:", page_html), (
    "the status line is pinned to a number again, and the keys' height is a guess"
)
assert re.search(r"#subtitle\s*\{[^}]*bottom:\s*calc\(var\(--bottom-h", page_html), (
    "a caption lands on the keys instead of clearing them"
)
# And she is drawn into the whole window, with the foot of the page floating over her boots. The canvas
# used to be the window less that stack, which drew her into a space that changed with every hint that
# wrapped — and with the keyboard, which took a third of the height and drew her a third smaller for as
# long as the box was open. Go asked for the full canvas on 2026-11-28.
assert "function freeHeight()" in page and "renderer.setSize(innerWidth, freeHeight())" in page, (
    "the canvas is not the window, so the box pushes her feet up out of the way"
)
assert "tallHeight" not in page and "tallWidth" not in page, (
    "the canvas keeps a height the window no longer has, and no resize can be told from the keyboard's"
)
assert "html:not(.app) #bottom" in page_html and "linear-gradient(transparent" in page_html, (
    "the foot of the page floats over her boots with nothing to read it against"
)
assert re.search(r"createFraming\(camera[\s\S]{0,300}?window\.faceApp\?\.resize,[\s\S]{0,300}?viewport\)", page), (
    "she is fitted to the window rather than to the canvas the controls leave"
)
assert "const viewport = () => [innerWidth, freeHeight()]" in page, (
    "the canvas and the framing disagree about the area she is drawn into"
)
assert "innerHeight * (innerHeight > innerWidth" not in (server.HERE / "js" / "framing.js").read_text(), (
    "the framing sizes her by the window again"
)
assert "dispatchEvent(new Event('resize'))" in page_html, (
    "a stack of a different height never tells the canvas it changed"
)
# The app and the page are the same in this, and the height is the window's on both: nothing is remembered,
# so no resize has to be told from a keyboard's -- and none of them can be, which is what the review of PR
# #34 found twice on 2026-11-28.
assert re.search(r"function freeHeight\(\) \{\n  return Math\.max\(120, innerHeight\);\n\}", page), (
    "the canvas still keeps a height of its own instead of following the window"
)
assert "getBoundingClientRect().top" not in re.search(
    r"function freeHeight\(\) \{.*?\n\}", page, re.S).group(0), (
    "the canvas's height is the stack's place again, which the keyboard moves"
)
# And the caption is placed from the window's bottom edge, not from the top edge of the part of it that
# is on the screen: read the wrong way round it went a window's height above the top and was never seen
# at all, which is how Go found it on 2026-11-28. It clears the foot stack and the row besides.
assert "innerHeight - shown.bottom" in page, (
    "the caption is placed a window's height above the top of the window"
)
# And in the window it hangs under the row of buttons rather than over it (Go, 2026-11-28): the row is
# placed beside the model's feet, and the air the framing keeps below them is where a caption reads.
# Where there is no room under the row it goes above it, which is where it was.
assert "const underRow = row ? innerHeight - row.bottom - CONTROLS_GAP - height : -Infinity;" in page, (
    "the caption lands on the row of buttons instead of hanging under it"
)
assert "underRow >= 4 ? underRow : aboveFoot" in page, (
    "a caption with no room under the row is pushed off the window's bottom edge"
)
assert not re.search(r"html\.app #bottom\s*\{[^}]*display:\s*none", page_html), (
    "the app's own error line is hidden with the controls"
)
assert re.search(r"html\.app #status\.error\s*\{[^}]*display:\s*block", page_html), (
    "a failure in the app is drawn on a page that cannot say it"
)
# And the page speaks to whoever is running it. "type to her" is true of this avatar and not of the next
# user's — the same fault the README has — so what a reader sees names the avatar, or nothing at all.
visible_html = re.sub(r"<!--.*?-->", "", page_html, flags=re.S)
for phrase in ("type to her", "a message to her", "her face", "her feet", "drawing her", "loading her animations"):
    assert phrase not in visible_html, f"the page says {phrase!r}, which is one user's avatar"
code_lines = "\n".join(line for line in page.splitlines() if not line.strip().startswith("//"))
for phrase in ("drawing her", "loading her animations", "can't draw her", "can't load her"):
    assert phrase not in code_lines, f"the status line says {phrase!r}, which is one user's avatar"
# A phone has no keyboard, and two things follow from that. It must not shrink the page to 980 pixels
# and hold it there — reported from the phone on 2026-11-21 as "too small, I have to zoom in a lot".
assert "width=device-width" in page_html, "a phone shrinks the page instead of fitting it"
# And every key a phone must be able to press has a button on the row: the microphone and the camera are
# there as buttons rather than as stand-ins that press a key for it, because a press is the gesture the
# browser asks for before either, and a button can say what it did (see #controls).
for key, button in (("m", "talk"), ("c", "lens")):
    assert f"e.key !== '{key}'" in page, f"the page stopped listening for {key!r}"
    assert f'id="{button}"' in page_html, f"{key!r} is keyboard only, and a phone has no keyboard"
assert "new KeyboardEvent('keydown'" not in page, (
    "a button on the row still presses a key for the page instead of being the press itself"
)
# A phone has no keypad either, and a box to type in is the other half of that: the phone is the only
# keyboard in the room, so a line typed on the page has to reach zrb as a turn the way a spoken one
# does. The server frames and cuts it (typed.py); the page sends it to POST /text.
assert 'id="typed"' in page_html, "a phone has no keypad, and the page gives it nowhere to type"
typed_js = (server.HERE / "js" / "typed.js").read_text()
assert "fetch('/text'" in typed_js, (
    "the page's text box sends nothing to the server"
)
# The route has one name in two packages, so it is compared rather than copied. zrb declares
# `TYPED_PATH`; this server dispatches on a literal of its own; a rename on either side reaches
# the page as a 404 that the reader treats as "no text box here" -- the same silence as a zrb
# with no reader installed, so it is exactly the kind of fault that fails quietly. zrb arrives
# here through the `zrb` extra, and the comparison is skipped out loud when the installed zrb is
# older than the typed reader, since then there is nothing to ask.
pinned_typed_in = "/text/in"
assert f'if path == "{pinned_typed_in}":' in (server.HERE / "server.py").read_text(), (
    "the server answers a route this test does not know, so the page's box has no name left"
)
try:  # zrb 4.0 keeps the face server's contract here; earlier zrb has no typed reader
    from zrb.llm.contracts.util.face_server import TYPED_PATH
except ImportError:
    TYPED_PATH = None
    print("skipped: the installed zrb has no typed reader, so its route cannot be compared")
if TYPED_PATH is not None:
    assert TYPED_PATH == pinned_typed_in, (
        f"zrb reads {TYPED_PATH!r} and this server serves {pinned_typed_in!r}: "
        "the page's text box reaches a route nobody answers"
    )
# Typing a letter is not pressing a key: an `m` in a sentence must not start the microphone, and a `c`
# must not ask for the camera. The box's own keys are stopped before the page's key handlers run -- except
# Enter, which is how the box sends: a textarea does not submit a form, so stopping Enter here left a
# phone that typed a message and sent nothing (review of PR #34, 2026-11-28).
capture = page[page.index("addEventListener('keydown', e => {"):]
capture = capture[:capture.index("}, true);")]
assert "target.closest('input, textarea')" in capture, "a letter typed in the box also presses a key"
assert "if (e.key === 'Enter') return;" in capture and (
    capture.index("e.key === 'Enter'") < capture.index("e.stopImmediatePropagation()")), (
    "the box's Enter is stopped before the handler that sends the line can see it"
)
assert "input.addEventListener('keydown'" in typed_js, "the box has no Enter of its own to send with"
assert "interactive-widget=resizes-content" in page_html, (
    "the phone's own keyboard covers the box that was tapped"
)
# And the view was keyboard-only as well: no arrows and no wheel on a phone, so a page that would not
# turn. One finger orbits her and two pinch her, in the keys' own steps -- one view, two ways to move it.
framing = (server.HERE / "js" / "framing.js").read_text()
assert "canvasEl.addEventListener('pointerdown'" in page, "nothing starts a drag on her"
assert "onPointerMove" in page and "onPointerMove" in framing, "a drag moves nothing"
assert "view.yaw += (e.clientX - was.x)" in framing, "a drag across her does not turn her"
assert "view.zoom *= now / apart" in framing, "a pinch resizes nothing"
# In the app that press is the window's, not the camera's: she is dragged by her body, and the app
# moves the window a frame behind the cursor, so the camera taking the same press turned her as she
# slid -- and at the screen's edge, where the window stopped and the cursor did not, all the way with
# the cursor. The desktop bridge answers whether a press is its own, and the page asks before the
# framing hears it.
desktop = (server.HERE / "js" / "desktop.js").read_text()
assert "const takes = e => over && e.button === 0;" in desktop, (
    "the app's drag no longer says which presses are its own"
)
assert "if (desktop.takes(e)) return;" in page, "a press that moves her window also turns her"
# A drag that is still going has to push the settle back, as holding a key does: zrb is told the view
# once it settles, and a notice sent only at the start would hand it the angle the finger passed through.
assert "if (her.framing.onPointerMove(e)) her.view.notice();" in page, (
    "a drag tells zrb the view it started at, not the one it ended on"
)
assert "touch-action: none" in page_html, "the browser takes the drag for a scroll of the page"
# The camera is a switch on both, so a press says what it did: "camera on" once the names are published,
# "camera off" after the second press. The page used to carry its own `c` button and its own sentence in
# the hint; on 2026-11-28 Go asked for the same three buttons on the page as in the window.
assert 'data-key' not in page_html and 'id="camera"' not in page_html, (
    "the page still carries a second microphone and camera of its own"
)
assert "lensLabel.textContent = error ? 'refused' : names.length ? 'camera on' : 'camera off'" in page, (
    "the camera button says nothing a press can change"
)
# The box is a textarea: a long thought wraps instead of scrolling out of sight, and Enter still sends
# it -- a textarea does not submit its form, so without the key handler the line would never leave.
assert '<textarea id="typed"' in page_html and "#keys textarea" in page_html, (
    "the page's box is a one-line field, so a long thought scrolls out of sight"
)
assert "event.key !== 'Enter' || event.shiftKey" in typed_js, (
    "Enter in the page's textarea inserts a newline and never sends the line"
)
# And the hint is gone rather than hidden. It read "tap or click the page, then:" — advice for a page
# nobody had touched, standing under a phone that had already pressed it, and on a page whose foot is a
# row of buttons it is a line explaining what the buttons already say. Go asked for it out on
# 2026-11-28. The box stays; nothing else is written along that edge.
assert "tap or click" not in page_html and 'class="first"' not in page_html, (
    "the page still tells a hand to tap a page it has already tapped"
)
assert ".keys-only" not in page_html and "firstKey" not in page, (
    "the page still carries a row of key names under a foot that has no keys to name"
)
# The window keeps the row of keys out of sight, which leaves a window that shows a key nobody was told
# about and gives a hand with no keyboard nothing to press. So the row it does show carries the three
# things a hand needs without them -- what she is doing, the microphone, and the camera -- and they stay
# in sight rather than waiting behind a `?`: a control that has to be found is a control that is not
# there. Go asked for that on 2026-11-28, after pressing `m` blind for a morning.
assert ('id="talk"' in page_html and 'id="voice"' in page_html and 'id="lens"' in page_html
        and 'id="state"' in page_html), (
    "the window offers neither what she is doing nor the camera, only a key it never showed"
)
assert 'id="help"' not in page_html and "#keys.shown" not in page_html, (
    "the window still hides its own controls behind a `?`"
)
assert "stateLabel.textContent = said" in page, "the window's row never says what she is doing"
# And what it says comes from one place, so a state event and a status event cannot disagree. A state event
# used to write only the page's line, which a window hides: the row stayed empty through thinking and
# working, and changed only when a status happened to arrive (the review of PR #34, 2026-11-28).
assert "function sayState()" in page and re.search(r"function setState\(value\) \{.*?sayState\(\);", page, re.S), (
    "a plain state change never reaches the window's row"
)
assert "statusText || (state === 'idle' ? '' : state)" in page, (
    "the row says something other than the status sentence or the state word"
)
assert "stateLabel.textContent = statusText" not in page, (
    "the row is written from the status event alone again, so a state change leaves it stale"
)
assert 'id="buttons"' in page_html and re.search(
    r"html\.app #controls\s*\{[^}]*flex-direction:\s*column", page_html), (
    "the state and the buttons are on one line, where the state runs into the button being pressed"
)
assert re.search(r"#buttons\s*\{[^}]*flex-direction:\s*row", page_html), (
    "the page's buttons are stacked rather than lying along its foot"
)
assert re.search(r"html\.app #buttons\s*\{[^}]*flex-direction:\s*column", page_html), (
    "the window's buttons are a row rather than a stack beside her"
)
assert re.search(r"html\.app #controls\s*\{[^}]*align-items:\s*center", page_html), (
    "the state is not centred over the buttons it describes"
)
assert re.search(r"html\.app #subtitle\.status\s*\{[^}]*display:\s*none", page_html), (
    "the window says the state twice, under her and beside her feet"
)
assert "const hold = () => {" in page and "holding = true;" in page and "audio.startMic();" in page, (
    "the talk button does not hold the microphone open"
)
assert "talkButton.addEventListener('pointerup', letGo)" in page, (
    "letting go of the talk button is not what stops the microphone"
)
assert "if (!audio.hearing) audio.startEars();" not in page, (
    "the talk button turns her voice on, so a mute is undone by the next thing said into it"
)
assert "audio.hearing ? audio.stopEars() : audio.startEars()" in page, (
    "her voice has no button of its own, so only the microphone's press can bring it back"
)
assert "owned = !audio.listening;" in page and "if (owned) audio.stopMic();" in page, (
    "releasing the talk button while the browser is still asking leaves the microphone open behind it"
)
# The camera is a switch too: a grant that can only be given is a page zrb keeps asking for photos
# after the hand that allowed it has changed its mind. Go pressed the camera button and found no way
# back on 2026-11-28.
assert "pageCamera.names.length ? pageCamera.release() : pageCamera.ask()" in page, (
    "the camera button asks and never takes the names back"
)
# Whether a press would be heard is not something a page can guess, so zrb says it: its voice reaches
# the page as an event (`voice`), whose `listening` is the server's own knowledge of a reader on the
# microphone route. The talk button offers a press only while one would be heard, and the two switches
# beside it only while zrb's voice is this server's — a button that is always there is a button that
# looks broken when hands-free is off (Go, 2026-11-28).
assert "msg.type === 'voice'" in page and (
    "remote: !!msg.remote,\n      listening: !!msg.listening,\n      name: typeof msg.name === 'string' ? msg.name : ''," in page
), "the page never learns whether zrb can hear it, or what zrb calls itself"
assert "talkButton.disabled = !reach.listening;" in page, (
    "the talk button offers a press that nothing is reading the microphone for"
)
assert "voiceButton.disabled = !reach.remote;" in page and "lensButton.disabled = !reach.remote;" in page, (
    "a switch zrb cannot reach is offered as though it did something"
)
assert "function paintButtons()" in page and "onState: now => {" in page, (
    "the row is painted from more than one place, so one switch undoes what another said"
)
assert "${who} can't hear you" in page and "${who} isn't listening" in page, (
    "a disabled button says nothing about why it is disabled"
)
# And it says her name rather than the bare word "zrb": zrb sends what it calls itself with the same
# event, because the row is the one place on the page that writes about her (Go, 2026-11-28).
assert "const who = reach.name || 'zrb';" in page, (
    "the row says 'zrb' where zrb has said its own name"
)
# And nothing keeps running behind a button that can no longer be pressed: zrb moving its voice away
# closes the speaker, the microphone and the camera this page had open, or the page would hold them
# until it was reloaded with no way to switch them off (the review of #35, 2026-11-28).
assert "if (!reach.remote && audio.hearing) audio.stopEars();" in page, (
    "a speaker zrb no longer speaks through stays open behind a disabled button"
)
assert "if (!reach.listening && audio.listening) audio.stopMic();" in page, (
    "a microphone zrb has stopped reading stays open behind a disabled button"
)
assert "if (!reach.remote && pageCamera.names.length) pageCamera.release();" in page, (
    "a camera grant zrb can no longer ask for is left with no way to take it back"
)
# The `m` key opens the microphone and the speaker as one gesture, so it follows the same rule the
# buttons do -- while still working as an off, which must not need zrb's permission (the review of
# #35, 2026-11-28).
assert "if (audio.listening || audio.hearing) return audio.stop();" in page, (
    "the m key cannot close what it opened once zrb has gone"
)
assert "if (!reach.remote || !reach.listening) return;" in page, (
    "the m key opens a microphone and a speaker nothing on the other end will use"
)
assert "button:disabled" in page_html, "a disabled button does not look disabled"
# And what zrb says is asked again every second rather than once at load: `/set LLM_VOICE_REMOTE_URL`
# moves zrb's voice onto (or off) this server while the chat runs, and the page follows in the same
# breath instead of waiting for a restart.
tools = (server.HERE / "zrb_init.py").read_text()
assert "def voice_is_here() -> bool:" in tools and "get_remote_url()" in tools, (
    "the plugin never asks where zrb's voice is, so a page cannot be told"
)
assert "zrb.llm.util.face_server import get_remote_url" in tools, (
    "an older zrb's layout is read as 'no remote voice', so the page turns its own switches off"
)
assert '"remote": voice_is_here(),' in tools and (
    '"name": str(CFG.LLM_ASSISTANT_NAME or ""),' in tools
), "the plugin never tells the page that zrb's voice moved, or what zrb calls itself"
assert "said_voice" not in tools and '"remote": voice_is_here(),' in tools, (
    "the voice is said once, so a send that failed -- the server restarting -- never reaches a page"
)
server_py = (server.HERE / "server.py").read_text()
assert "def voice_state() -> dict:" in server_py and 'event.get("type") == "voice"' in server_py, (
    "the server neither holds what zrb said nor takes its word for it"
)
assert '"name": str(_voice["name"]),' in server_py and (
    '_voice["name"] = name.strip() if isinstance(name, str) else ""'
) in server_py, "the name zrb sent never reaches a page"
assert 'or voice["name"]' in server_py, (
    "a page that connects after zrb named itself is not told the name"
)
assert "audio.is_microphone_being_read()" in server_py and (
    "audio.end_reading(\"in\")" in server_py
), "the server never notices zrb arriving on the microphone route or leaving it"
assert "with _lock:\n        said = voice_state()" in server_py, (
    "what is sent is read before the lock that sends it, so a reader's end can land after its replacement's start"
)
camera = (server.HERE / "js" / "camera.js").read_text()
assert "async function release()" in camera and "await report([])" in camera, (
    "there is no way to tell the server this page is no longer a camera"
)
# And the button's "on" is a camera zrb can really ask for, not a count of cameras the page is not allowed
# to name: a button reading "camera on" over an ungranted camera spends its first press taking back what it
# never gave, and the press that should have asked for permission is gone. The review of PR #34 found that
# on 2026-11-28.
assert "names = found.filter(device => device.label).map(device => device.label);" in camera, (
    "the camera button is on because a camera exists, not because the page may use one"
)
assert "names.length ? await shoot(device) : CAMERA_OFF" in camera, (
    "a photo asked for before the camera was switched off is still taken after it"
)
# And a press that lands while the picture is being taken takes it back too: the photo is not sent, and the
# camera is not published again by the answer -- an off button a finished photo could undo is not a switch
# (the review of PR #34, 2026-11-28).
assert "const mine = asked;" in camera and "mine === asked ? result : CAMERA_OFF" in camera, (
    "a photo in flight when the camera is switched off is still sent, and the camera comes back on with it"
)
assert "if (ended.image) publish();" in camera, (
    "the photo's answer publishes the camera it no longer has"
)
# And the reports go one at a time: the server keeps the last one it receives, so an empty report that
# overtook the names behind it took the camera away while the button read "camera on". The review of PR
# #34 found that on 2026-11-28.
assert "let reporting = Promise.resolve();" in camera and "reporting = reporting" in camera, (
    "the page's camera reports race each other to the server, and the older one can land last"
)
assert "await report(names);" in camera, (
    "the names the page found are written nowhere, so zrb is never told the camera came on"
)
# And the reading is part of the same operation as the writing, not the writing alone: `enumerateDevices()`
# answers with what was true when it was asked, so a publish started on load -- before any grant -- could
# finish after the grant's own and post its pre-grant empty list behind the new names. A reading a newer
# press has overtaken is dropped instead of reported. The review of PR #34 found that on 2026-11-28.
assert "const mine = ++asked;" in camera and "if (mine !== asked) return names;" in camera, (
    "a reading of the cameras from before a grant still reaches the server after the grant's own"
)
assert "asked++;" in camera, (
    "an off press leaves a reading in flight, which can put the names back on afterwards"
)
assert "if (mine !== asked) return;" in camera, (
    "a press taken back while the browser was asking comes back on when the browser answers it"
)
# And the stream the browser hands back is given straight back in a finally, whichever way the ask ended:
# a camera left open behind a press that was taken back is a camera still filming.
assert "} finally {" in camera and "stream?.getTracks().forEach(track => track.stop());" in camera, (
    "a grant that arrives after the press was taken back leaves its camera open"
)
# And each button carries an icon beside its word, so the row is read before it is read.
assert page_html.count('class="icon"') == 3 and "#controls .icon" in page_html, (
    "the window's buttons are words only, in a row nobody can aim at without reading"
)
# The app passes every click through to whatever is behind the window, so a control in its corner needs
# the answer her body gets: without it the press on the talk button is a press on the window behind her.
assert "pointerenter" in desktop and "overControl" in desktop, (
    "the app takes the mouse over her body only, so a button in its corner cannot be pressed"
)
assert "desktop.watch(" in page, "the controls never tell the app the cursor is on them"
assert "const takes = e => over && e.button === 0;" in desktop, (
    "a press on a control is answered as a press on her, and moves her window"
)
# Where the row sits is hers, not the window's: a corner of a see-through window is somebody else's
# document, and the buttons were over the terminal's text until Go asked for them to be nearer her on
# 2026-11-28. Her stance is measured rather than the prism the framing fits, so the row stays beside her
# feet, and only the part of the window the display is showing counts.
assert "function placeControls(force)" in page and "controlsRow.classList.add('placed')" in page, (
    "the window's buttons are pinned to a corner of the window"
)
assert "STANCE_BONES" in page and "getNormalizedBoneNode(name)" in page, (
    "the buttons are placed against a reach that is not where she stands"
)
# And it is put down rather than dragged along: Go said on 2026-11-28 that it moves too much. So it moves
# only when she has drifted further than DRIFT, the side it is on is kept until the other side is clearly
# roomier, and a move is eased rather than jumped (see `#controls.placed` in face.html).
assert "const DRIFT = " in page and "const CROSS_ROOM = " in page and "placed.side === side" in page, (
    "the row follows every sway, which slides it under the pointer trying to press it"
)
assert re.search(r"html\.app #controls\.placed\s*\{[^}]*transition:", page_html), (
    "a move of the row is a jump rather than a step"
)
assert "placeControls(true); // the row is wider or narrower now" in page, (
    "the row keeps the width it had when what she is doing changes under it"
)
assert "placeControls(); // beside her feet" in page, "the row is placed once and then left behind"
assert re.search(r"html\.app #controls\.placed\s*\{[^}]*right:\s*auto", page_html), (
    "the row is still laid out where the window would have put it"
)

# The line channel as the server keeps it: one message a line, a blank one refused, and what was
# already typed follows the reader that replaced the one that left.
channel = typed.start_reading()
assert typed.put_line("  hello there  ") is True
assert channel.take(0) == "hello there"
assert typed.put_line("\n   \n") is False, "a blank line is not a message"
assert channel.take(0.05) == "", "a blank line reached zrb"
assert typed.put_line("two\nlines") is True
assert channel.take(0) == "two lines", "a line's own newline split it into two messages"
assert typed.put_line("x" * (typed.MAX_LINE_CHARS + 10)) is True
assert len(channel.take(0)) == typed.MAX_LINE_CHARS, "a paste is not cut to the length of a line"
left = typed.start_reading()
assert typed.put_line("still a question") is True
right = typed.start_reading()
assert left.ended and not right.ended, "a replaced reader keeps its route open"
assert right.take(0) == "still a question", "what was typed before the reader changed was lost"
# A page must not be told its line arrived when nothing will read it: the route counts the reader that
# holds it, and stops counting when that reader leaves — a reader that had already been replaced does
# not turn off the flag for the one that replaced it.
assert typed.reader_attached(), "a reader holding the route is not counted"
typed.stop_reading(left)  # replaced a moment ago, so not the reader the box should be refused for
assert typed.reader_attached(), "a reader that had already gone turned off the flag for the one there"
typed.stop_reading(right)
assert not typed.reader_attached(), "nothing reads the box, and a line is still accepted for it"
# And a queue nobody drains does not grow for ever: past MAX_WAITING a line is refused rather than
# dropped, so the page can keep the words instead of believing they were sent.
# A line a relay has taken but not written goes back to whoever holds the route now: the gap between
# the take and the write is where a reconnect happens, and a message typed into one is still a message.
assert typed.put_line("taken but not written") is True
hand = typed.start_reading()
assert hand.take(0) == "taken but not written"  # the relay has it in hand
newer = typed.start_reading()  # and zrb reconnected before it could write it
typed.unsend("taken but not written")
assert newer.take(0) == "taken but not written", "a line in a replaced reader's hand was lost"
typed.unsend("")  # a keepalive tick, which is not a line
assert newer.take(0.05) == "", "a keepalive tick came back as an empty message"
for i in range(typed.MAX_WAITING):
    assert typed.put_line(f"line {i}") is True
try:
    typed.put_line("one too many")
    assert False, "a line was dropped for a reader that was not taking them, and the page was told it arrived"
except typed.ChannelFull:
    pass
assert "nothing is reading the box yet" in typed_js, "a refused line says nothing a person can act on"

# A page that cannot draw her says so rather than sitting on the status line's own "loading…" for
# ever: a GPU a sleep took away leaves the window black and the line unchanged, and the line is the
# only place a failure can be seen. Both ways in are guarded — the renderer that throws as it is
# made, and the context that is lost under a page that was already drawing.
assert "webglcontextlost" in page, "a lost context leaves the window black and silent"
assert "giveUp(" in page, "nothing puts a drawing failure on the status line"
# One counter for both failures is the bug this pair prevents: a context lost after a page loaded
# would be cleared by the next load, and a GPU resetting every minute would reload for ever.
assert "CONTEXT_FAILURES" in page and "BOOT_FAILURES" in page
assert "removeItem(BOOT_FAILURES)" in page and "removeItem(CONTEXT_FAILURES)" in page
# And the message has to survive the page that keeps running: a state, camera or connection event
# arriving after the retries are spent would paint "idle" under a black window, which reads as working.
assert "if (failure) return;" in page, "nothing stops a status event from replacing a drawing failure"
assert "setAnimationLoop(null)" in page, "the loop keeps rendering into a context that is gone"
# A model that will not load is the same silence, and a likelier one: the camera publishes its names
# on the way up, so a page that never got her model sits there reading "camera: camera 1" under a black
# window. So the load reaches the same latch, saying which step failed, and counts the model as it
# arrives — a phone spends a minute on 18 MB, and a still number is how a stall is told from a page
# that works.
assert "LOAD_FAILURES" in page and "removeItem(LOAD_FAILURES)" in page, (
    "a model that loads after failing leaves the failure count standing, so the retries run out"
)
assert "reasonOf(error)" in page, "a model load fails without saying why"
assert "loading ${name}:" in page, "nothing counts her model as it arrives"
# A download that stops moving never ends by itself: no error, no reload, a number that will never
# change. Measured on the phone 2026-11-21 — the size arrived with the headers, not one byte did, and
# the page waited. So the wire is timed, and a stall has a sentence of its own.
assert "STALL_MS" in page, "a stalled download sits at a number that will never change"
assert "the download stalled at" in page, "a stall says nothing a reader can act on"
# And a stall is not the end of the load: the phone's screen went off at six of eighteen megabytes
# and every try after it asked for the file from the start, so a reload began the minute again. The
# rest is asked for, and the screen is held on while a model is on its way.
assert "STALL_TRIES" in page, "one stall ends a download that a phone could have finished"
assert "Range: `bytes=${from}-`" in page, "a resumed download asks for the whole file again"
assert "res.status !== 206" in page, "a page appends the whole file to the part it already has"
assert "navigator.wakeLock" in page, "the screen goes off and the download goes with it"
assert "sleepAgain();" in page, "the screen is held on after the model arrived"
# And the line belongs to the load while a load is in flight: the camera publishes its names on the way
# up and a state event can paint "idle" over a number that is still counting, which is what made the
# phone's working page and broken page look the same. Measured, not guessed — the first version of the
# stall timer painted a parse failure because a cancel ends a read before a rejection can be seen.
assert "let loading = null;" in page, "nothing keeps a load's number from being painted over"
assert "if (!loading && !loadError) { setStatusLine(state); sayState(); }" in page, (
    "a state event paints over a load in flight"
)
assert "if (!error && loading) return;" in page, "the camera's names paint over a load in flight"
# And a swap that fails is not painted over either: by the time it says why, the load has already
# cleared `loading`, so the next state event would put "idle" on the line a moment later and the page
# would look like it changed her. The reason is kept until another writer takes the line.
assert "let loadError = null;" in page, "a failed model swap is replaced by the next state event"
assert "if (loadError && !isError) return;" in page, "a kept load error is let go by a camera or state event"
assert page.count("loadError = null") == 2, "a load that worked does not clear the reason a load did not"
assert "loadError = `can't load ${name}.vrm:" in page, "a failed swap is written without being kept"
assert page.count("if (ticket === showing)") == 3, (
    "a load a newer one replaced can still speak, or a load that worked does not clear the reason"
)
assert page.count("statusEl.textContent = ") == 2, (
    "only setStatus and giveUp may write the line; every other writer goes through the latch"
)

# Every name AvatarPose (zrb_init.py) offers is one the page (js/pose.js) knows.
tools = (server.HERE / "zrb_init.py").read_text()
pose_js = (server.HERE / "js" / "pose.js").read_text()


def _offered_names(table):
    """What *table* offers, read from the syntax tree rather than from the text.

    A formatter that gave POSE_HANDS one entry per line broke a regex that
    assumed a single-line tuple, and the check is about the names, not the
    layout. `ast.parse` reads the file without importing it.
    """
    for node in ast.walk(ast.parse(tools)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == table
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Tuple), f"{table} is not a tuple"
            return [element.value for element in node.value.elts]
    raise AssertionError(f"{table} is not assigned in zrb_init.py")


for name, table in (
    ("HANDS", "POSE_HANDS"),
    ("HEAD", "POSE_HEAD"),
    ("BODY", "POSE_BODY"),
    ("FINGERS", "POSE_FINGERS"),
):
    block = re.search(rf"^const {name} = \{{(.*?)^\}};", pose_js, re.S | re.M).group(1)
    for offered in _offered_names(table):
        assert re.search(
            rf"(^|[{{,\s])'?{re.escape(offered)}'?: [\[{{]", block, re.M
        ), (name, offered)

# What the eleven tools say about themselves is paid on every request, before any of it is used, so
# their descriptions are the largest fixed cost this plugin adds to a turn. The vocabulary has to
# stay somewhere -- a model that cannot see the words cannot pose -- so the budget is on the
# explaining: what a tool has to say beyond its own contract, it says when it is called. AvatarPose()
# with nothing to move answers with the place and joint table, and every tool names its allowed
# values in the error it returns. Both numbers are what the eleven cost after that cut, rounded up.
TOOL_PROSE_BUDGET = 4_700  # characters, all eleven descriptions together
TOOL_PROSE_CAP = 1_400  # characters, any one of them

prose = {
    node.name: len(ast.get_docstring(node) or "")
    for node in ast.parse(tools).body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name.startswith("Avatar")
}
assert len(prose) == 11, sorted(prose)  # a tool that left, or arrived, without being priced
over = {name: size for name, size in prose.items() if size > TOOL_PROSE_CAP}
assert not over, f"a description is over {TOOL_PROSE_CAP} characters: {over}"
assert sum(prose.values()) <= TOOL_PROSE_BUDGET, sorted(
    prose.items(), key=lambda pair: -pair[1]
)[:3]

# Presence without a camera: how long since the keyboard or mouse last moved. The parsers first,
# each on the shape its command really prints, then the clock that turns a number into an answer.
from zrb_avatar.idle import (  # noqa: E402
    LastInput,
    parse_ioreg,
    parse_windows,
    parse_xprintidle,
    powershell_command,
)

assert parse_windows("24\n") == 24.0
# The Windows call itself failing is unknown, never "away": silence is not absence.
assert parse_windows("-1") is None
# PowerShell says hello on stderr; a stray line on stdout must not become a reading.
assert parse_windows("Preparing modules for first use.") is None
assert parse_xprintidle("1500\n") == 1.5  # milliseconds
assert parse_xprintidle("") is None
idle_ns = parse_ioreg('    | |   "HIDIdleTime" = 2763347000\n')
assert idle_ns is not None and abs(idle_ns - 2.763347) < 1e-9  # nanoseconds
assert parse_ioreg("ioreg printed nothing useful") is None
# The probe is one base64 argument: the script holds C# braces, and no command line should have
# to parse them. A half-quoted script is the bug this shape exists to prevent.
command = powershell_command(5)
assert command[0] == "powershell.exe" and command[-2] == "-EncodedCommand"
probe_script = base64.b64decode(command[-1]).decode("utf-16-le")
assert "GetLastInputInfo" in probe_script
assert "Start-Sleep -Milliseconds 5000" in probe_script
assert "__INTERVAL__" not in probe_script


# A reading is cached between polls, a failed refresh leaves the last one standing, and three polls
# without an answer means the probe cannot tell — not that the chair is empty.
async def _a_reading_goes_stale_rather_than_false():
    asked, answers = [], [12.0, None, None, None]

    async def ask():
        asked.append(1)
        return answers[min(len(asked), len(answers)) - 1]

    reader = LastInput(poll_seconds=0.02, ask=ask)
    assert await reader.seconds() == 12.0  # the first answer
    assert await reader.seconds() == 12.0  # and inside the same poll, without asking again
    assert len(asked) == 1
    await asyncio.sleep(0.03)
    assert await reader.seconds() == 12.0  # a failed refresh answers nothing, so it changes nothing
    assert len(asked) == 2
    await asyncio.sleep(0.07)  # three polls with no answer
    assert await reader.seconds() is None
    reader.close()


asyncio.run(_a_reading_goes_stale_rather_than_false())


# What she knows is not what she may say. The sensing loop writes the camera, the avatar and the
# music down whatever ZRB_AVATAR_TALK_FIRST says, and one line of it goes in front of every turn.
# This needs zrb, which the stdlib-only checks above do not, so it comes last.
import zrb_avatar.zrb_init as face_zrb  # noqa: E402

# A blink she chose, through the tool: the event the page holds for that long, what it takes, and
# what it says back. The automatic blink is the page's own and needs no tool; this is the one that
# is a gesture. Nothing here is remembered: a blink that arrived while a page reloaded has happened.
sent, real_send = [], face_zrb.send
face_zrb.send = sent.append
try:
    assert face_zrb.AvatarBlink() == "Blinked for 0.2 s."
    assert sent[-1] == {"type": "blink", "eyes": "both", "seconds": 0.2}
    assert face_zrb.AvatarBlink(eyes="right", seconds=0.5) == (
        "Winked with the right eye (0.5 s)."
    )
    assert sent[-1] == {"type": "blink", "eyes": "right", "seconds": 0.5}
    assert face_zrb.AvatarBlink(eyes=" Right ") == "Winked with the right eye (0.2 s)."
    assert sent[-1]["eyes"] == "right"  # as typed: trimmed, and one case
    # A length a lid cannot mean is pinned to the ends rather than sent as asked.
    assert face_zrb.AvatarBlink(seconds=60) == "Blinked for 2 s."
    assert sent[-1]["seconds"] == 2.0
    assert face_zrb.AvatarBlink(seconds=0) == "Blinked for 0.08 s."
    # And a name or a length it cannot make sense of is refused, not guessed at.
    sent.clear()
    assert face_zrb.AvatarBlink(eyes="wink").startswith(
        "eyes must be one of both, left, right"
    )
    assert face_zrb.AvatarBlink(seconds="slow").startswith("seconds must be a number")
    assert face_zrb.AvatarBlink(seconds=True).startswith("seconds must be a number")
    assert sent == []  # nothing went out for any of them
finally:
    face_zrb.send = real_send

# A feeling on purpose: the same emotion event zrb already sends, so the page believes nothing new,
# plus the one word for how hard it is worn. `pout` and the other four blends are in the list
# although no model carries them as shapes — each is two presets at once.
sent, real_send = [], face_zrb.send
face_zrb.send = sent.append
try:
    assert face_zrb.AvatarFace() == "pout, normal, fading after six seconds."
    assert sent[-1] == {"type": "emotion", "value": "pout", "strength": 1.0}
    assert face_zrb.AvatarFace(" Angry ") == "angry, normal, fading after six seconds."
    assert sent[-1] == {"type": "emotion", "value": "angry", "strength": 1.0}
    # Strength is one word, never a number: the dial is what the page refuses to be handed.
    assert face_zrb.AvatarFace("smirk", "soft") == "smirk, soft, fading after six seconds."
    assert sent[-1]["strength"] == 0.5
    assert face_zrb.AvatarFace("sleepy", "STRONG") == "sleepy, strong, fading after six seconds."
    assert sent[-1]["strength"] == 1.5
    assert face_zrb.AvatarFace("smug").startswith("emotion must be one of happy")
    assert face_zrb.AvatarFace("pout", "hard").startswith("strength must be one of soft")
    assert len(sent) == 4  # neither refusal sent anything
finally:
    face_zrb.send = real_send

# A pose, and the table it hands out. A call naming nothing used to be an error; it is now the place
# and joint table, which is where the description's long ranges went: the words a pose needs are paid
# by the call that poses, not by every request that might.
sent, real_send = [], face_zrb.send
face_zrb.send = sent.append
try:
    table = face_zrb.AvatarPose()
    assert table.startswith("Nothing to move.")
    for joint in ("wrist", "elbow", "arm", "shoulder", "spine", "leg", "knee", "ankle"):
        assert f"{joint} " in table, joint
    assert "0 keeps it until AvatarRest" in table
    assert sent == []  # the table is an answer, not a pose
    assert face_zrb.AvatarPose(left_hand="chest", seconds=0) == (
        "Holding left_hand: chest until you let go (AvatarRest)."
    )
    assert sent[-1] == {"type": "pose", "hold": True, "leftHand": "chest"}
finally:
    face_zrb.send = real_send

# The faces and the strengths the tool offers are exactly the ones the page knows: a name in one
# list and not in the other is a call that answers "pout" and changes nothing on screen.
faces_js = (server.HERE / "js" / "expression.js").read_text()
presets_js = re.findall(
    r"'(\w+)'", re.search(r"const EMOTIONS = \[(.*?)\]", faces_js).group(1)
)
blends_js = re.findall(
    r"(\w+): \{", re.search(r"const BLENDS = \{(.*?)\n\};", faces_js, re.S).group(1)
)
assert [*face_zrb.FACES] == [*presets_js, *blends_js], (
    face_zrb.FACES,
    presets_js,
    blends_js,
)
strengths_js = re.findall(
    r"(\w+): [\d.]+", re.search(r"const STRENGTHS = \{(.*?)\};", faces_js).group(1)
)
assert [*face_zrb.FACE_STRENGTHS] == strengths_js, (
    face_zrb.FACE_STRENGTHS,
    strengths_js,
)

# The init spells the same noun the server does: <PREFIX>_AVATAR_*, never the retired _FACE_.
from zrb.config.config import CFG  # noqa: E402

saved_setting = os.environ.get(f"{CFG.ENV_PREFIX}_AVATAR_PRESENCE")
os.environ[f"{CFG.ENV_PREFIX}_AVATAR_PRESENCE"] = "size"
os.environ[f"{CFG.ENV_PREFIX}_FACE_PRESENCE"] = "idle"  # what zrb-face read, now ignored
try:
    assert face_zrb._setting("PRESENCE", "off") == "size"
finally:
    os.environ.pop(f"{CFG.ENV_PREFIX}_FACE_PRESENCE", None)
    if saved_setting is None:
        os.environ.pop(f"{CFG.ENV_PREFIX}_AVATAR_PRESENCE", None)
    else:
        os.environ[f"{CFG.ENV_PREFIX}_AVATAR_PRESENCE"] = saved_setting

# What a turn she starts is asked for is a setting too: the default keeps her quiet unless she has
# something to say, {nothing_to_say} is the one placeholder it reads, and an empty value asks for
# nothing at all — a white label's own voice, without patching this file.
saved_prompt = face_zrb.PROACTIVE_PROMPT
try:
    assert face_zrb._proactive("a turn worth taking", "test").text == (
        "[test, not typed by the user] a turn worth taking Say something short and natural "
        f"only if it is worth saying; otherwise reply exactly {face_zrb.NOTHING_TO_SAY}"
    )
    face_zrb.PROACTIVE_PROMPT = "Only if it matters; else {nothing_to_say}"
    assert face_zrb._proactive("x", "test").text == (
        "[test, not typed by the user] x Only if it matters; else "
        f"{face_zrb.NOTHING_TO_SAY}"
    )
    face_zrb.PROACTIVE_PROMPT = ""
    assert face_zrb._proactive("x", "test").text == "[test, not typed by the user] x"
finally:
    face_zrb.PROACTIVE_PROMPT = saved_prompt

assert face_zrb._ambient_line() == (
    "face — presence: not looked (no watch) · camera: nothing reported yet · "
    "avatar: unknown · music: off"
)
face_zrb._face.update({"model": "tara-red-hoodie", "music": True})
assert "avatar: tara-red-hoodie" in face_zrb._ambient_line()
assert "music: playing" in face_zrb._ambient_line()
face_zrb._face.update(
    {"present": False, "presence_from": "photo size", "away_since": None}
)
assert "presence: nobody at the desk by photo size" in face_zrb._ambient_line()
face_zrb._face.update(
    {
        "present": True,
        "present_since": None,
        "view_at": None,
        "view": {
            "part": "chest",
            "side": "front",
            "close": True,
            "shy": True,
            "shows": 0.5,
        },
    }
)
line = face_zrb._ambient_line()
assert "presence: someone at the desk by photo size" in line, line
assert (
    "camera: a close look at her chest from the front, which she is shy about" in line
), line
# What the frame holds is said in words, not in the page's arithmetic: `shows` is her height
# across the window's narrower side, and a figure nobody would say out loud describes nothing.
assert "0.5" not in line, line


def _camera_says(view: dict, when=None) -> str:
    """The camera segment of one view's ambient line, the rest of the face held still."""
    saved = dict(face_zrb._face)
    try:
        face_zrb._face.update({"view": dict(view), "view_at": when})
        return [
            part
            for part in face_zrb._ambient_line().split(" · ")
            if part.startswith("camera: ")
        ][0]
    finally:
        face_zrb._face.clear()
        face_zrb._face.update(saved)


# All of her, with air at both ends: 1.0 is the page's own threshold, so both sides of it.
assert _camera_says({"part": "chest", "side": "front", "shows": 1.4}) == (
    "camera: all of her, head to heels, with air above and below, from the front"
)
assert _camera_says({"part": "chest", "side": "front", "shows": 1.0}).startswith(
    "camera: all of her"
)
assert _camera_says({"part": "chest", "side": "front", "shows": 0.99}).startswith(
    "camera: most of her"
)
# Most of her, and the part the window is centred on, which is what the old line named.
assert _camera_says({"part": "face", "side": "side", "shows": 0.75}) == (
    "camera: most of her, with her face in the middle, from the side"
)
# Under the page's `close` the frame is a close look at that one part, and said from where.
assert _camera_says({"part": "hips", "side": "behind", "close": True, "shows": 0.4}) == (
    "camera: a close look at her hips from behind"
)
# A shy close-up says so, after the side and before how long ago.
assert _camera_says(
    {"part": "chest", "side": "front", "close": True, "shy": True, "shows": 0.5},
    time.monotonic() - 120,
) == (
    "camera: a close look at her chest from the front, which she is shy about, 2 min ago"
)
# No figure from the page reaches the words at any distance, and a view the page sent no size
# for claims no more than the part it is centred on.
for shows in (2.0, 1.0, 0.99, 0.75, 0.6, 0.5, 0.25):
    said = _camera_says(
        {"part": "chest", "side": "front", "close": shows < 0.6, "shows": shows}
    )
    assert str(shows) not in said, said
    assert not any(character.isdigit() for character in said), said
assert _camera_says({"part": "belly", "side": "above"}) == (
    "camera: her belly in the middle of the window, from above"
)
# A guess says so. "size" is a photo size, never a face; "idle" is the clock, and neither claims
# to have seen one — "face" is still reserved for something that actually does.
assert face_zrb.PRESENCE_SOURCES == {
    "off": None,
    "size": "photo size",
    "idle": "input idle",
}


class _Photo:
    def __init__(self, size: int) -> None:
        self.data = b"x" * size


assert face_zrb.presence_size(_Photo(face_zrb.CAMERA_PRESENT_BYTES + 1))
assert not face_zrb.presence_size(_Photo(face_zrb.CAMERA_PRESENT_BYTES))
# The clock answers the question the photo answered: input this recent is somebody at the desk.
assert face_zrb.presence_idle(0.0)
assert face_zrb.presence_idle(face_zrb.PRESENCE_SECONDS - 1)
assert not face_zrb.presence_idle(face_zrb.PRESENCE_SECONDS)


# The stamps are what "since when" reads, so they move only when the answer does: a probe asked
# every few seconds must not restart the clock it is reporting.
def _presence_over_a_day():
    saved = dict(face_zrb._face)
    try:
        face_zrb._face.update(
            {
                "present": None,
                "presence_from": "",
                "present_since": None,
                "away_since": None,
            }
        )
        changed, away = face_zrb._set_presence(True, "input idle", 100.0)
        assert changed and away is None
        assert face_zrb._face["present_since"] == 100.0
        assert face_zrb._face["presence_from"] == "input idle"
        changed, _ = face_zrb._set_presence(True, "input idle", 200.0)
        assert not changed and face_zrb._face["present_since"] == 100.0
        changed, _ = face_zrb._set_presence(False, "input idle", 300.0)
        assert changed and face_zrb._face["away_since"] == 300.0
        # Coming back reports how long they had been gone, which is what gates the turn.
        changed, away = face_zrb._set_presence(True, "input idle", 1000.0)
        assert changed and away == 300.0
    finally:
        face_zrb._face.clear()
        face_zrb._face.update(saved)


_presence_over_a_day()
# A return is announced only for a switch that names it; with none named, no turn.
assert not face_zrb._worth_saying_back(True, True, 0.0, 10_000.0)
# The same line, on demand: knowing does not depend on being allowed to speak.
assert face_zrb.AvatarStatus() == face_zrb._ambient_line()
# One trigger carries every source, and it is registered whether or not she may speak first.
assert face_zrb.sense_watch in face_zrb.llm_chat.triggers


# A poll that fails answers nothing, so it changes nothing: the face server restarting for a moment
# must not turn a known avatar into "unknown" and playing music into silence until a real answer.
def _down(*_, **__):
    return None


async def _failed_polls_keep_the_last_answer():
    face_zrb._face.update({"model": "tara-office", "music": True})
    saved, face_zrb.ask = face_zrb.ask, _down
    polling = asyncio.create_task(face_zrb.sense_poll(asyncio.Queue()))
    try:
        await asyncio.sleep(
            1.5
        )  # one tick, and the model/music poll that comes with it
    finally:
        polling.cancel()
        face_zrb.ask = saved
    return face_zrb._face["model"], face_zrb._face["music"]


assert asyncio.run(_failed_polls_keep_the_last_answer()) == ("tara-office", True)


# And the sensing runs beside a turn, not inside it. A trigger is a generator, so the moment it
# yields, it is suspended until the chat has finished that turn — seconds, for a model's answer.
# This takes a turn and then sits on it, and the state is written anyway: the separation, checked.
def _answers(name, *_, **__):
    return {
        "view": {"n": 0},
        "model": {"chosen": None, "showing": ["tara-office"]},
        "music": {"playing": True, "choice": None},
    }.get(name)


async def _sensing_goes_on_during_a_turn():
    face_zrb._face.update({"model": None, "music": False})
    saved, face_zrb.ask = face_zrb.ask, _answers
    gen = face_zrb.sense_watch()
    try:
        taken = asyncio.create_task(anext(gen))
        await asyncio.sleep(
            0.1
        )  # the poller is up by now; the generator is parked on the queue
        face_zrb._turns.put_nowait(face_zrb._proactive("a turn worth taking", "test"))
        assert "a turn worth taking" in (await taken).text
        # The turn is the chat's from here: it will not ask for another for as long as it likes.
        await asyncio.sleep(1.5)
        return face_zrb._face["model"], face_zrb._face["music"]
    finally:
        await gen.aclose()
        face_zrb.ask = saved


assert asyncio.run(_sensing_goes_on_during_a_turn()) == ("tara-office", True)

# Her own switches, through the tool and back: the round trip to the server is the point, so this
# is not a mock. And the greeting hook asks before it plays, which is the one switch zrb acts on.
face_client.AVATAR_URL, saved_lib_url = (
    f"http://127.0.0.1:{port}/event",
    face_client.AVATAR_URL,
)
try:
    said = face_zrb.AvatarAutonomy()
    assert (
        "willing to: blush, emotion, greeting" in said
    )  # dance is off until she says otherwise
    assert "but not dance" in said
    turned_off = face_zrb.AvatarAutonomy(greeting=False)
    assert turned_off.endswith("but not dance, greeting."), turned_off
    assert json.loads(get("/autonomy")[1])["greeting"] is False
    assert face_client.consents("greeting") is False
    assert face_client.consents("dance") is False
    assert "Each switch is true or false" in face_zrb.AvatarAutonomy(
        dance="no"
    )  # not half-applied
    assert json.loads(get("/autonomy")[1])["dance"] is False
    assert "but not greeting" not in face_zrb.AvatarAutonomy(greeting=True)
    assert face_client.consents("greeting") is True
    real_send, face_zrb.send = (
        face_zrb.send,
        lambda message: None,
    )  # a server that refuses it
    try:
        # A switch already where it is being sent cannot show a refusal, so this one starts on.
        assert "did not take greeting" in face_zrb.AvatarAutonomy(greeting=False)
    finally:
        face_zrb.send = real_send
    assert face_client.consents("greeting") is True  # and nothing changed
finally:
    face_client.AVATAR_URL = saved_lib_url
# No server is not a refusal: every one of these did its thing before she could say otherwise.
face_client.AVATAR_URL = "http://127.0.0.1:9/event"
try:
    assert face_client.consents("greeting") is True
finally:
    face_client.AVATAR_URL = saved_lib_url
# Every reminder is zrb-nudge's now: its own file, its own trigger, its own tools. What is left
# here is the one thing a reminder needs from a face -- whether someone is at the desk -- and the
# nudge must not come back by accident, or a lunch would be said twice.
assert not hasattr(face_zrb, "nudge_watch")
assert not any(
    getattr(trigger, "__name__", "") == "nudge_watch"
    for trigger in face_zrb.llm_chat.triggers
)
assert not [name for name in dir(face_zrb) if name.startswith("NUDGE")]
assert face_zrb.AvatarStatus().startswith("face — presence: ")
# An hour range is still a setting, and a value that does not look like the example falls back to
# the default rather than taking the hours away.
var = lambda name: f"{face_zrb.CFG.ENV_PREFIX}_AVATAR_{name}"  # noqa: E731
assert face_zrb._hours("AWAKE_HOURS", "8-22") == range(8, 22)
os.environ[var("AWAKE_HOURS")] = "9-18"
try:
    assert face_zrb._hours("AWAKE_HOURS", "8-22") == range(9, 18)  # a working day only
finally:
    del os.environ[var("AWAKE_HOURS")]
os.environ[var("AWAKE_HOURS")] = "eight to ten"
try:
    assert face_zrb._hours("AWAKE_HOURS", "8-22") == range(8, 22)  # the default
finally:
    del os.environ[var("AWAKE_HOURS")]

# A refused look: her body answers on its own, on the round trip the view event already makes, and
# lets go when the look does. One pose per look, not one per event, however many the page sends.
os.environ["ZRB_AVATAR_REFLEX"] = "0"
try:
    assert server._on("ZRB_AVATAR_REFLEX") is False  # "0" says no
    os.environ["ZRB_AVATAR_REFLEX"] = "on"
    assert server._on("ZRB_AVATAR_REFLEX") is True
finally:
    del os.environ["ZRB_AVATAR_REFLEX"]
assert server._on("ZRB_AVATAR_REFLEX") is False  # unset says no: a public install gets nothing
server.REFLEX = False
server._answer_a_refused_look(True)
assert server._pose == {}  # the switch is off, so her body says nothing
server.REFLEX = True
try:
    server._view.update({"part": "chest", "side": "front"})
    server._answer_a_refused_look(True)
    assert server._pose["reflex"] is True
    # A folder of her own, named for what the look was at: which clip in it is the page's, so a
    # cover dropped in by hand answers the look it was made for without anything else changing.
    assert server._pose["pool"] == "cover/chest"
    held = dict(server._pose)
    server._answer_a_refused_look(True)  # the same look again
    assert server._pose == held  # no second clip for every event
    server._view.update({"part": "hips"})
    server._answer_a_refused_look(True)  # the look moved: what answers it moves too
    assert server._pose["pool"] == "cover/bottom"
    server._view.update({"side": "below"})
    server._answer_a_refused_look(True)  # from below, which sees all of her at once
    assert server._pose["pool"] == "cover/all"
    server._answer_a_refused_look(False)  # the look is over
    assert server._pose == {}  # and a page connecting now starts her free
finally:
    server.REFLEX = False
    with server._lock:
        server._pose.clear()

# She can take the covering back, and the reflex is hers to grant: `cover` is a switch like the
# others, so a no to it is a no to the pose -- and one already held comes down at once, because a
# consent withdrawn is not something to act on at the next look. The machine's `ZRB_AVATAR_REFLEX`
# still decides whether the reflex exists at all; this decides whether she goes along with it.
server.REFLEX = True
saved_autonomy_file = (
    server.AUTONOMY_FILE.read_text(encoding="utf-8")
    if server.AUTONOMY_FILE.is_file()
    else None
)
try:
    server.choose_autonomy({"cover": False})
    server._answer_a_refused_look(True)
    assert server._pose == {}  # she said no: her body does not answer the look
    assert json.loads(server.AUTONOMY_FILE.read_text(encoding="utf-8")) == {
        "cover": False
    }  # written down: a no tonight is not undone by tomorrow's restart
    assert server.agreed("cover") is False

    server.choose_autonomy({"cover": True})  # and a yes, which replaces it
    server._answer_a_refused_look(True)
    assert server._pose["reflex"] is True
    server.choose_autonomy({"cover": False})  # withdrawn while she is covering
    assert server._pose == {}  # it comes down now, not at the next look

    # Temporary: it holds for its lifetime and then the file's answer is hers again, without a
    # timer having to run and without a restart inheriting a decision she made for a minute.
    server.choose_autonomy({"cover": True})
    server.choose_autonomy({"cover": False}, seconds=0.2)
    assert server.agreed("cover") is False
    assert (
        json.loads(server.AUTONOMY_FILE.read_text(encoding="utf-8")) == {}
    )  # the lifetime is not written down: the file keeps her standing answer, which is the default
    time.sleep(0.25)
    assert server.agreed("cover") is True  # it ran out: the file answers again
    assert json.loads(get("/autonomy")[1])["cover"] is True
    assert post_event(
        '{"type":"autonomy","value":{"cover":false},"seconds":"soon"}'
    ) == 400  # a lifetime that is not a number of seconds is refused
finally:
    server.choose_autonomy({"cover": True})
    server.REFLEX = False
    with server._lock:
        server._pose.clear()
    if saved_autonomy_file is None:
        server.AUTONOMY_FILE.unlink(missing_ok=True)
    else:
        server.AUTONOMY_FILE.write_text(saved_autonomy_file, encoding="utf-8")

# A view that arrives late must not undo a newer one. The page fires both without waiting and this
# server answers each on its own thread, so arrival order is not generation order: a refused
# close-up followed quickly by an ordinary view could otherwise leave her covered in front of a
# view she never refused.
saved_view = dict(server._view)
try:
    server._view.update({"run": "r1", "seq": 3, "n": 7})
    assert server._accepts_view({"run": "r1", "seq": 2}) is False  # the older one, arriving late
    assert server._accepts_view({"run": "r1", "seq": 3}) is False  # the one already applied
    assert server._accepts_view({"run": "r1", "seq": 4}) is True  # the next one
    assert server._accepts_view({"run": "r2", "seq": 1}) is True  # a reloaded page outranks it
    assert server._accepts_view({"part": "hips"}) is True  # an older page numbers nothing
    assert server._accepts_view({"run": "r1", "seq": "4"}) is True  # not a number: taken as it comes
finally:
    server._view.clear()
    server._view.update(saved_view)

import shutil  # noqa: E402  (subprocess is imported further up, next to the music watcher)

# The converters in tools/ are what turns a capture into a clip, and a syntax break there is
# silent until somebody needs one. Python answers for its own; node, when it is installed,
# parses the rest without running them.
TOOLS = Path(__file__).parent / "tools"
assert (TOOLS / "gltf2vrma.py").is_file(), TOOLS
help_text = subprocess.run(
    [sys.executable, str(TOOLS / "gltf2vrma.py"), "--help"],
    capture_output=True,
    text=True,
)
assert help_text.returncode == 0 and "OUT_DIR" in help_text.stdout, help_text.stderr
if shutil.which("node"):
    for script in ("vrma-writer.mjs", "fbx2vrma.mjs", "bvh2vrma.mjs", "vrma-info.mjs"):
        parsed = subprocess.run(
            ["node", "--check", str(TOOLS / script)], capture_output=True, text=True
        )
        assert parsed.returncode == 0, (script, parsed.stderr)

# AvatarLook shows the frame through a helper inside zrb, and that helper has moved once already:
# 0.3.2 imported it from `zrb.llm.agent.run`, which zrb no longer has, and then reported the
# ImportError as "this zrb cannot pass pictures to you" — a limit no released zrb had. And a zrb
# that keeps its shared leaves in `llm/shared` broke the plugin at load, at the one import that
# still named the old spot. Nothing else runs the plugin's zrb side, so every zrb import it makes
# is resolved here, against the zrb that is installed.
PLUGIN = Path(__file__).parent / "zrb_avatar" / "zrb_init.py"
tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))


def _resolves(node):
    try:
        imported = importlib.import_module(node.module)
    except ImportError:
        return False
    return all(hasattr(imported, alias.name) for alias in node.names)


# An import inside an `except` handler is the other layout a name may live under, for a zrb that
# predates a move: it is meant not to resolve on the zrb installed here, so it is not checked on
# its own. What it rescues is checked instead — the pair passes when either layout resolves, since
# which one this zrb has is the whole question the plugin asks.
fallbacks = {
    id(node)
    for handler in ast.walk(tree)
    if isinstance(handler, ast.ExceptHandler)
    for node in ast.walk(handler)
    if isinstance(node, ast.ImportFrom)
}
parents = {
    id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
}


def _rescues(node):
    """The imports in the handlers of the try whose body holds `node`, where any one may answer."""
    child = node
    while id(child) in parents:
        child = parents[id(child)]
        if isinstance(child, ast.Try):
            return [
                other
                for handler in child.handlers
                for other in ast.walk(handler)
                if isinstance(other, ast.ImportFrom)
                and other.module
                and sorted(a.name for a in other.names)
                == sorted(a.name for a in node.names)
                and _resolves(other)
            ]
    return []


for node in ast.walk(tree):
    if not isinstance(node, ast.ImportFrom) or not node.module:
        continue
    if node.module != "zrb" and not node.module.startswith("zrb."):
        continue
    if id(node) in fallbacks:
        continue
    assert _resolves(node) or _rescues(node), (node.module, "no layout resolves")

# The token: what stands in front of her once something other than this machine can reach her.
# ---------------------------------------------------------------------------------------------
# Empty is a plain local run, and it must stay exactly what it was: nothing asked for, no header on
# any of zrb's calls. Set, it guards every route -- the page, the JSON, the event stream and every
# write alike -- and a first visit with `?token=` trades it for a cookie, because a phone's address
# is typed once and its own fetches and EventSource carry a cookie without being told to.
import contextlib  # noqa: E402  (with the block it belongs to, not with the rest)
import io  # noqa: E402

auth = server.auth
assert auth.is_loopback("127.0.0.1") and auth.is_loopback("::1") and auth.is_loopback("localhost")
for exposed in ("0.0.0.0", "192.168.1.4", "100.101.102.103", "her-machine", ""):
    assert not auth.is_loopback(exposed), exposed  # an unknown name is exposed, not local

assert auth.presented({}, "", "")  # no token wanted: every request is allowed, offered or not
assert auth.presented({"Cookie": "a=b"}, "model=mine", "")

TOKEN = "correct-horse-battery-staple"  # long enough for an exposed bind
assert not auth.presented({}, "", TOKEN)  # nothing offered
assert auth.presented({"Authorization": f"Bearer {TOKEN}"}, "", TOKEN)  # what zrb sends
assert not auth.presented({"Authorization": f"Bearer {TOKEN[:-1]}x"}, "", TOKEN)
assert not auth.presented({"Authorization": TOKEN}, "", TOKEN)  # the scheme is not optional
assert auth.presented({}, f"model=mine&token={TOKEN}", TOKEN)  # what a curl sends
assert not auth.presented({}, "model=mine", TOKEN)
assert auth.presented({"Cookie": f"other=1; {auth.COOKIE}={TOKEN}"}, "", TOKEN)  # a page
assert not auth.presented({"Cookie": f"{auth.COOKIE}=wrong"}, "", TOKEN)

# The cookie it hands back is one the page never reads, and one a same-origin fetch will send.
assert auth.cookie_header(TOKEN).startswith(f"{auth.COOKIE}={TOKEN}; Path=/; HttpOnly")
assert auth.wants_a_cookie(f"/?token={TOKEN}", TOKEN)  # the phone's link, on the page
assert auth.wants_a_cookie(f"/?app&model=mine&token={TOKEN}", TOKEN)  # the window's own address
assert not auth.wants_a_cookie("/?model=mine", TOKEN)  # an ordinary visit sets nothing
assert not auth.wants_a_cookie(f"/?token={TOKEN}", "")  # an unguarded server has no cookie
# Not an API route: a `curl` after JSON is answered with the JSON, not redirected somewhere else.
assert not auth.wants_a_cookie(f"/models?token={TOKEN}", TOKEN)
# The redirect keeps the address it was given, minus the token: `?app` is the window's own URL, and
# rendering it as `app=` would hand the window back a page that is no longer the app.
assert auth.url_without_token(f"/?app&model=mine&token={TOKEN}") == "/?app&model=mine"
assert auth.url_without_token(f"/?token={TOKEN}") == "/"
assert auth.url_without_token(f"/mine.vrm?token={TOKEN}") == "/mine.vrm"
assert auth.url_without_token("/?app") == "/?app"  # nothing to take out

# A bind anyone can reach is refused without a usable token, and there is no flag that talks it out
# of it: an exposed face switches models, plays clips and photographs the window.
quiet = contextlib.redirect_stderr(io.StringIO())
auth.refuse_insecure_bind("127.0.0.1", "", "ZRB_AVATAR_TOKEN")  # loopback: never a refusal
for host, token in (("0.0.0.0", ""), ("0.0.0.0", "too-short")):
    with quiet:
        try:
            auth.refuse_insecure_bind(host, token, "ZRB_AVATAR_TOKEN")
        except SystemExit as stopped:
            assert stopped.code == 1, stopped.code
        else:
            raise AssertionError(f"exposed without a usable token: {host} {token!r}")
auth.refuse_insecure_bind("0.0.0.0", TOKEN, "ZRB_AVATAR_TOKEN")  # guarded: allowed

# The page's ears and mouth: raw 16-bit mono PCM one way, her voice back at whatever rate the
# backend made it. One format is taken going in — a page that resamples to anything else is refused
# rather than transcribed as noise — and the outbound rate travels in a header, since raw PCM has
# nowhere in the body to put it.
MIC = bytes(range(256)) * 8  # 2048 bytes: 64 ms of 16 kHz mono, quiet enough to hear nothing
SAID = b"\x01\x02" * 1200  # 2400 bytes at 24 kHz: what speech made, at its own rate


def post_audio(path: str, body, headers: dict, chunked: bool = False) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = [body] if chunked else body  # an iterable is framed as one chunk
    connection.request(
        "POST", path, body=payload, headers=headers, encode_chunked=chunked
    )
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


# Whether zrb can hear this page is a fact this server knows by itself: a reader on the microphone
# route is the whole of "listening" (see `_relay_audio_to_zrb`). So a page can be told without a word
# from zrb, and told the moment it changes, which is what lets the talk button offer a press only
# while one would be heard. With hands-free off zrb's dictation holds no reader, and a press on the
# page's microphone went into a queue nobody drained — Go asked on 2026-11-28 why the button did
# nothing there. Nothing has read the route yet at this point, so the count starts at zero.
voice_ping = server.audio.PING_SECONDS
server.audio.PING_SECONDS = 0.05  # a route that ended is noticed inside this test
try:
    assert server.audio.is_microphone_being_read() is False, "something is already reading"


    def voice_event(events) -> dict:
        while (event := next_event(events))["type"] != "voice":
            pass  # what a connecting page is replayed first: the music, the model, the pose
        return event


    watcher = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    watcher.request("GET", "/events")
    watched_voice = watcher.getresponse()
    time.sleep(0.2)
    assert post_event('{"type":"voice","remote":true,"name":"Tara"}') == 204, (
        "zrb cannot say where its voice is"
    )
    assert voice_event(watched_voice) == {
        "type": "voice",
        "remote": True,
        "listening": False,
        "name": "Tara",
    }, "a page is not told that zrb's voice has moved onto this server, or what it is called"
    reader = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    reader.request("GET", "/audio/in")
    held = reader.getresponse()  # held: a response nobody keeps is a socket that closes
    time.sleep(0.2)
    assert voice_event(watched_voice)["listening"] is True, (
        "a page is not told that zrb started reading its microphone, so its button stays off"
    )
    # A replaced reader ends after the one that replaced it: its end must not say nobody is reading.
    second = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    second.request("GET", "/audio/in")
    replaced = second.getresponse()
    time.sleep(0.2)
    reader.close()
    time.sleep(0.2)
    assert server.audio.is_microphone_being_read() is True, (
        "the reader that was replaced took the count of the one reading with it"
    )
    second.close()
    del replaced
    time.sleep(0.2)
    assert voice_event(watched_voice)["listening"] is False, (
        "a page is not told that zrb stopped reading, so it offers a press nothing hears"
    )
    # Back to what the rest of this file assumes: a page connecting now is replayed nothing about zrb.
    assert post_event('{"type":"voice","remote":false}') == 204
    assert voice_event(watched_voice)["remote"] is False
    watcher.close()
finally:
    server.audio.PING_SECONDS = voice_ping


def listen_once(path: str, size: int, send) -> tuple[dict, bytes]:
    """Attach a reader, put audio through it, and read *size* bytes.

    The live order in both directions: a page opens its stream and is reading before anything is
    said, and zrb is reading the microphone while the page talks. Attaching first is also what makes
    this reader the one of its direction — a reader that has gone is replaced by the next, rather
    than left holding the audio that arrives.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    time.sleep(0.15)  # registered as the reader, before there is anything to read
    send()
    try:
        return dict(response.getheaders()), response.read(size)
    finally:
        connection.close()


# A measured body first: the fallback for a browser that will not stream.
headers, body = listen_once(
    "/audio/in",
    len(MIC),
    lambda: post_audio("/audio", MIC, {"X-Sample-Rate": "16000"}),
)
assert body == MIC
assert headers["X-Sample-Rate"] == "16000"
assert headers["X-Sample-Width"] == "2" and headers["X-Channels"] == "1"

# The page sends it both ways, and the one that carried real speech was the second: a browser that
# streams a body posts one request whose body is the microphone, and one that will not — no `duplex`,
# or a request that never opens at all — posts the same blocks one at a time instead. Both are in the
# page because the difference only shows in a browser: a real Chrome refused the streamed request
# outright with an ALPN error, and the fallback carried the audio from there.
audio_js = (server.HERE / "js" / "audio.js").read_text(encoding="utf-8")
open_post = audio_js[audio_js.index("function openPost()"):audio_js.index("function postEachBlock()")]
assert "if (!streamsBody()) return postEachBlock();" in open_post
assert "duplex: 'half'" in open_post and "postEachBlock()" in open_post  # a start that failed goes on
assert ".catch(() => {})" not in open_post  # a dead upload is not a silence to swallow

# A release is a silence, not merely a request that stopped. zrb cuts an utterance on quiet blocks --
# LLM_DICTATION_SILENCE, one second of them -- and the face server answers a quiet queue with one 20 ms
# frame every fifteen seconds, so a page that just stopped posting left the utterance open until
# `max_utterance` half a minute later: the press looked dead. Measured against zrb's own cutter, two
# seconds of speech and then that ping is still an utterance in progress, and a second and a half of
# silence ends it at once. So the page says the release in the one language the cutter reads.
release = audio_js[audio_js.index("function releaseMic()"):audio_js.index("async function startMic()")]
assert "const CLOSING = 1.5;" in audio_js, "the closing silence has no length"
assert "if (wasListening) (postFinal ?? postPcm)?.(new Uint8Array(Math.round(RATE * CLOSING) * 2))" in release, (
    "a released button stops posting without saying the utterance ended, so nothing is transcribed"
)
assert release.index("(postFinal ?? postPcm)?.") < release.index("endPost?.()"), (
    "the stream closes before the silence is in it"
)
# And the closing silence is not dropped for arriving behind speech: the block-at-a-time fallback throws a
# block away while two are in flight, which is right for speech and wrong for the goodbye -- a release with
# two posts still out said nothing, and the utterance ran to its half-minute timeout (PR #34, 2026-11-28).
fallback = audio_js[audio_js.index("function postEachBlock()"):audio_js.index("function releaseMic()")]
assert "postFinal = pcm => {" in fallback and "if (!open || sent >= AHEAD) return;" not in fallback[
    fallback.index("postFinal = pcm => {"):], (
    "the fallback drops the closing silence the way it drops old speech, so a release can end nothing"
)

# And chunked, which is what a page streaming its microphone sends: no length to give.
_, body = listen_once(
    "/audio/in",
    len(MIC),
    lambda: post_audio("/audio", MIC, {"X-Sample-Rate": "16000"}, chunked=True),
)
assert body == MIC

# One reader per direction: a page that reloaded attaches, and the reader it replaced must not be
# left holding what arrives next — it would write it into a socket nobody is reading.
abandoned = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
abandoned.request("GET", "/audio/in")
abandoned.getresponse()
time.sleep(0.2)
_, body = listen_once(
    "/audio/in",
    len(MIC),
    lambda: post_audio("/audio", MIC, {"X-Sample-Rate": "16000"}),
)
assert body == MIC  # the reader that is attached now, not the one that was left
abandoned.close()

# Refused, not resampled: speech-to-text takes one rate, and a page that sends another is a page to
# fix rather than audio to guess at.
assert post_audio("/audio", MIC, {"X-Sample-Rate": "44100"}) == 415
assert post_audio("/audio", MIC, {"X-Channels": "2"}) == 415

# Her voice, at the rate she was made at: the page configures its AudioContext from the header.
headers, body = listen_once(
    "/audio/out",
    len(SAID),
    lambda: post_audio("/say", SAID, {"X-Sample-Rate": "24000"}),
)
assert body == SAID and headers["X-Sample-Rate"] == "24000"
assert post_audio("/say", SAID, {"X-Sample-Rate": "0"}) == 415  # no rate a voice can have

saved_ping = server.audio.PING_SECONDS
server.audio.PING_SECONDS = 0.05  # so a stream that ends is noticed inside this test
try:
    # A sentence at another rate opens a new stream: the one that is open ends rather than playing
    # it at the old pitch, and what was made is carried to the reader that asks next.
    old = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    old.request("GET", "/audio/out")
    old_response = old.getresponse()
    time.sleep(0.15)
    assert post_audio("/say", SAID, {"X-Sample-Rate": "22050"}) == 204
    # To the end of that stream: nothing but the silence it pings with, and then EOF rather than one
    # sentence played at the other rate's pitch.
    ended = old_response.read()
    assert ended.strip(b"\x00") == b"" and len(ended) % 960 == 0  # 20 ms at 24 kHz
    old.close()
    fresh = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    fresh.request("GET", "/audio/out")
    fresh_response = fresh.getresponse()
    assert dict(fresh_response.getheaders())["X-Sample-Rate"] == "22050"
    assert fresh_response.read(len(SAID)) == SAID
    fresh.close()

    # A quiet queue is answered with silence rather than with nothing: raw PCM has no comment
    # syntax, so one inaudible frame is also the only write that notices a page whose tab has gone.
    _, body = listen_once("/audio/in", 640, lambda: None)  # 20 ms of 16 kHz mono
    assert body == b"\x00" * 640
finally:
    server.audio.PING_SECONDS = saved_ping

# The channel and its rate are one step, because the page configures itself from that header: a rate
# read apart from the channel it describes is a sentence played at the wrong pitch. A sentence at
# another rate rotates the channel, and the reader that asks next starts at the new rate rather than
# naming the old one over the new blocks.
old_channel, old_rate = server.audio.start_reading_outbound()
assert old_rate == 22050, old_rate  # what the last sentence was made at
server.audio.put_outbound(SAID, 24000)
next_channel, next_rate = server.audio.start_reading_outbound()
assert next_rate == 24000 and next_channel is not old_channel
assert next_channel.take(1) == SAID  # carried over: the sentence made for the stream that asks
assert old_channel.take(0.05) is None  # and the reader it replaced is left with nothing

# The cap is a length of audio, not a number of blocks: the page sends a tenth of a second at a time,
# and a count of blocks held a fifth of a second rather than the two seconds it promises -- so a
# reader that lagged by 300 ms lost the words in the middle. Twenty of the page's blocks fit, and the
# oldest goes when the next one arrives.
block = b"\x00" * 3200  # 100 ms of 16 kHz mono 16-bit
channel = server.audio.Channel()
for _ in range(25):
    channel.put(block)
assert len(channel.drain()) == 20
# A block bigger than the whole cap is not cut for being long -- it is a sentence somebody made --
# so it is kept while it is alone, and leaves only when newer audio arrives behind it.
channel = server.audio.Channel()
channel.put(b"\x00" * 200_000)  # past two seconds of this format, handed over at once
assert len(channel.drain()) == 1
channel.put(b"\x00" * 200_000)
channel.put(block)
assert channel.drain() == [block]

# And the reader is told the moment its stream is replaced, rather than at the end of its own read
# timeout: the page asks again as soon as this response ends, so fifteen seconds of waiting here is
# fifteen seconds of silence in the middle of a conversation.
saved_ping = server.audio.PING_SECONDS
server.audio.PING_SECONDS = 15.0  # the read that a replaced stream must not have to wait out
try:
    stale = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    stale.request("GET", "/audio/out")
    stale_response = stale.getresponse()
    time.sleep(0.15)  # the stream is the reader before anything is said
    started = time.monotonic()
    assert post_audio("/say", SAID, {"X-Sample-Rate": "16000"}) == 204
    ended = stale_response.read()  # to the end of the response, which the rate change ends
    assert time.monotonic() - started < 1.0  # ended because it was replaced, not by a timeout
    assert ended.strip(b"\x00") == b""  # and nothing of the new sentence at the old pitch
    stale.close()
finally:
    server.audio.PING_SECONDS = saved_ping

# And the server itself, on the port that is already open above: guarded now, unguarded again after.
def guarded(path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    answer = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return answer


def guarded_post(path, body, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path, body, headers=headers or {})
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


server.TOKEN = TOKEN
try:
    assert guarded("/")[0] == 401  # the page, the JSON and the event stream alike
    assert guarded("/models")[0] == 401
    assert guarded("/events")[0] == 401
    assert guarded("/mine.vrm")[0] == 401
    assert guarded_post("/event", '{"type":"state","value":"idle"}') == 401
    assert guarded("/", {"Authorization": f"Bearer {TOKEN}"})[0] == 200
    assert guarded("/models", {"Authorization": f"Bearer {TOKEN}"})[0] == 200
    assert guarded("/models", {"Cookie": f"{auth.COOKIE}={TOKEN}"})[0] == 200
    assert guarded(f"/models?token={TOKEN}")[0] == 200
    assert (
        guarded_post(
            "/event",
            '{"type":"state","value":"idle"}',
            {"Authorization": f"Bearer {TOKEN}"},
        )
        == 204
    )
    # The phone's one typed address: the token goes out, a cookie comes back, and the page loads.
    status, _, sent = guarded(f"/?app&model=mine&token={TOKEN}")
    assert status == 302 and sent["Location"] == "/?app&model=mine", (status, sent.get("Location"))
    assert sent["Set-Cookie"].startswith(f"{auth.COOKIE}={TOKEN}; Path=/")
    # A wrong one is refused rather than traded for a cookie.
    assert guarded("/?token=wrong")[0] == 401
finally:
    server.TOKEN = ""  # back to a plain local run, which is what the rest of this file assumes
assert guarded("/models")[0] == 200  # nothing set: answered as before

print("ok")
