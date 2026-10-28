import ast
import asyncio
import datetime
import http.client
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
from zrb_avatar.client import MOUTH_STEP, Mouth, loudness

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
server.ANIM_DIRS = [user / "anim", server.HERE / "anim"]
assert server.anims()["idle"]["rest"].read_bytes() == b"override"
assert "Idle" in server.anims()["idle"]  # the shipped ones are still there
assert server.anims()["actions/greetings"]["hi"].read_bytes() == b"hi"
assert not any(" " in pool for pool in server.anims())

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
import importlib  # noqa: E402

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

# What she agrees to do on her own: four switches, each at its default until she says otherwise, and
# the answer kept in a file because a consent withdrawn tonight is not something tomorrow's restart
# should quietly restore. Only dance starts off -- it answers the media watcher rather than something
# the user did, and the watcher cannot tell a song from a lecture.
defaults = {"dance": False, "blush": True, "emotion": True, "greeting": True}
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
try:  # what FaceModels and FaceModel ask
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

# FaceLook's picture: GET /snapshot asks the open window, which answers with a PNG and what she
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
assert json.loads(shot.getheader("X-Face")) == {"state": "idle", "playing": "idle/wait"}
client.request(
    "POST", "/snapshot?id=made-up", '{"image": "data:image/png;base64,AAAA"}'
)
assert client.getresponse().status == 404  # nobody asked for that one
assert server.take_snapshot(timeout=0.3) is None  # pages open, but none answers
url = face_client.AVATAR_URL
face_client.AVATAR_URL = f"http://127.0.0.1:{port}/event"
try:
    threading.Thread(target=fake_window, daemon=True).start()
    time.sleep(0.3)
    assert face_client.snapshot() == (PNG, {"state": "idle", "playing": "idle/wait"})
finally:
    face_client.AVATAR_URL = url
face_client.AVATAR_URL, saved = "http://127.0.0.1:9/event", face_client.AVATAR_URL
assert face_client.snapshot(timeout=1) is None  # no server: no picture, no exception
face_client.AVATAR_URL = saved

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

# Every state the page shows has a pool.
page = (server.HERE / "js" / "main.js").read_text()
states = re.findall(r"(\w+)\s*:", re.search(r"const LOOK = \{(.*?)\}", page).group(1))
for state in [*states, "dance", "actions"]:
    assert any(
        pool for pool in server.anims().get(state, {})
    ), f"no clip for the {state} pool"

# Every name FacePose (zrb_init.py) offers is one the page (js/pose.js) knows.
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
    "camera: a close-up of her chest from the front, 0.5 of her height across the window"
    in line
), line
assert "which she is shy about" in line, line
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
assert face_zrb.FaceStatus() == face_zrb._ambient_line()
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
    said = face_zrb.FaceAutonomy()
    assert (
        "willing to: blush, emotion, greeting" in said
    )  # dance is off until she says otherwise
    assert "but not dance" in said
    turned_off = face_zrb.FaceAutonomy(greeting=False)
    assert turned_off.endswith("but not dance, greeting."), turned_off
    assert json.loads(get("/autonomy")[1])["greeting"] is False
    assert face_client.consents("greeting") is False
    assert face_client.consents("dance") is False
    assert "Each switch is true or false" in face_zrb.FaceAutonomy(
        dance="no"
    )  # not half-applied
    assert json.loads(get("/autonomy")[1])["dance"] is False
    assert "but not greeting" not in face_zrb.FaceAutonomy(greeting=True)
    assert face_client.consents("greeting") is True
    real_send, face_zrb.send = (
        face_zrb.send,
        lambda message: None,
    )  # a server that refuses it
    try:
        # A switch already where it is being sent cannot show a refusal, so this one starts on.
        assert "did not take greeting" in face_zrb.FaceAutonomy(greeting=False)
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
# The nudge: its own hours and its own ledger. The rules are cheap and answerable every ten
# minutes; the turn they start is where the model decides whether it is worth saying anything.
face_zrb.NUDGE_FILE = user / "nudge.json"


def clock(hour, minute=0):
    return datetime.datetime(2026, 10, 26, hour, minute)


# Lunch, once a day, inside its own window and not outside it.
assert face_zrb.nudge_candidate(clock(11, 59), {}) is None
assert face_zrb.nudge_candidate(clock(12, 30), {})[0] == "lunch"
assert face_zrb.nudge_candidate(clock(12, 30), {"lunch": True}) is None
assert face_zrb.nudge_candidate(clock(13, 45), {}) is None  # the window closes
# Bedtime is after AWAKE_HOURS ends, which is why the nudge cannot use the shared gate: at 23:00
# `_may_speak_first` says no for every other source, and "go to bed" is exactly that hour.
assert face_zrb.nudge_candidate(clock(23), {})[0] == "bed"
assert face_zrb.nudge_candidate(clock(2), {}) is None  # and 2am is not a time to nudge
assert 23 not in face_zrb.AWAKE_HOURS and 23 in face_zrb.NUDGE_HOURS
face_zrb._companion["last_proactive"] = time.monotonic() - 2 * face_zrb.COOLDOWN_SECONDS
face_zrb._companion["busy"] = False
# Whether something is speaking is the runner's business, not this test's, and in CI it is not
# always false -- the hour is the half being checked, so the others are held still.
real_is_speaking, face_zrb.is_speaking = face_zrb.is_speaking, lambda: False
try:
    assert face_zrb._may_nudge(clock(23)) is True
    assert face_zrb._may_nudge(clock(2)) is False
finally:
    face_zrb.is_speaking = real_is_speaking
face_zrb._companion["last_proactive"] = time.monotonic() - 2 * face_zrb.COOLDOWN_SECONDS
# A turn that ended in an error is worth a word, once per such turn; a normal stop is not.
face_zrb._companion["failed"] = 2
assert face_zrb.nudge_candidate(clock(15), {"failed": 1})[0] == "failure"
assert face_zrb.nudge_candidate(clock(15), {"failed": 2}) is None
face_zrb._companion["failed"] = 0
# Quiet: after NUDGE_QUIET_MINUTES, and not again until its own wait has passed.
face_zrb._companion["last_user_turn"] = time.monotonic() - 95 * 60
assert face_zrb.nudge_candidate(clock(15), {})[0] == "quiet"
assert face_zrb.nudge_candidate(clock(15), {"quiet": time.time()}) is None
assert (
    face_zrb.nudge_candidate(clock(15), {"quiet": time.time() - 91 * 60})[0] == "quiet"
)
face_zrb._companion["last_user_turn"] = time.monotonic()
# Back at the desk after twenty minutes away -- and only while that is still true: ten minutes
# later the moment has passed, and saying "you are back" would be a lie about the present.
face_zrb._face.update(
    {
        "present": True,
        "present_since": time.monotonic(),
        "away_since": time.monotonic() - 30 * 60,
    }
)
assert face_zrb.nudge_candidate(clock(15), {})[0] == "return"
face_zrb._face["present_since"] = time.monotonic() - 20 * 60
assert face_zrb.nudge_candidate(clock(15), {}) is None
face_zrb._face.update({"present": None, "present_since": None, "away_since": None})
# The ledger: written down as it is used, read back after a restart, and emptied when the day
# turns over -- zrb restarted at 13:00 must not mention lunch a second time.
face_zrb._nudge.update({"date": "", "said": {}})
assert face_zrb._nudge_day(clock(12, 30)) == {}
face_zrb._nudge["said"]["lunch"] = True
face_zrb._nudge_save()
face_zrb._nudge.update({"date": "", "said": {}})  # as if the chat had just started
assert face_zrb._nudge_day(clock(12, 31)) == {"lunch": True}
assert face_zrb._nudge_day(clock(0, 1).replace(day=27)) == {}  # a new day starts empty
assert face_zrb.NUDGE in ("auto", "off")
assert face_zrb.nudge_watch in face_zrb.llm_chat.triggers
# Every hour and window above is a setting, not a constant in the file, and a value that does not
# look like the example falls back to the default rather than taking the nudge down with it.
var = lambda name: f"{face_zrb.CFG.ENV_PREFIX}_AVATAR_{name}"  # noqa: E731
assert face_zrb._hours("NUDGE_HOURS", "7-24") == range(7, 24)
assert face_zrb._window("NUDGE_BED", "22:30-24:00") == (22 * 60 + 30, 24 * 60)
assert face_zrb._window("NUDGE_LUNCH", "12:00-13:30") == (12 * 60, 13 * 60 + 30)
os.environ[var("NUDGE_HOURS")] = "9-18"
try:
    assert face_zrb._hours("NUDGE_HOURS", "7-24") == range(9, 18)  # a working day only
finally:
    del os.environ[var("NUDGE_HOURS")]
os.environ[var("NUDGE_BED")] = "half past ten"
try:
    assert face_zrb._window("NUDGE_BED", "22:30-24:00") == (
        22 * 60 + 30,
        24 * 60,
    )  # the default
finally:
    del os.environ[var("NUDGE_BED")]

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
    server._answer_a_refused_look(True)
    assert server._pose["reflex"] is True
    assert server._pose["leftHand"] == "chest"  # the forearm across her chest
    assert server._pose["rightHand"] == "hips" and server._pose["head"] == "away"
    assert server._pose["hold"] is True  # held until she lets go, not for a few seconds
    held = dict(server._pose)
    server._answer_a_refused_look(True)  # the same look again
    assert server._pose == held  # no second pose for every event
    server._answer_a_refused_look(False)  # the look is over
    assert server._pose == {}  # and a page connecting now starts her free
finally:
    server.REFLEX = False
    with server._lock:
        server._pose.clear()

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

print("ok")
