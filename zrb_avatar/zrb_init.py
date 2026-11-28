"""zrb-avatar's zrb side: load it and zrb gets a face, a voice that moves its mouth,
and (optionally) a companion that speaks first.

    export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-avatar init-path)"

Loading this script turns the face on: zrb's state and speech go to the server
(server.py, `python -m zrb_avatar serve`). It
also owns zrb's speech (`enable_speech`), so other init scripts should only set
the voice (CFG.DEFAULT_LLM_SPEECH_BACKEND / _VOICE), not call `enable_speech`.
zrb also gets eleven tools of hers: AvatarListActions lists her animations by pool with what each
looks like, AvatarAction plays one once, AvatarPose moves her body itself (hands, head, posture) for a
few seconds, AvatarRest lets go of a pose she is holding, AvatarBlink blinks or winks deliberately,
AvatarFace puts a feeling on her face on purpose, AvatarAutonomy reads or changes what she may do
unasked, AvatarLook shows her own window as the user sees it, AvatarListModels lists the avatars (VRM
models) with the one showing, AvatarModel switches to another, and AvatarStatus reports what the
ambient line says.

Settings (the prefix follows zrb's CFG.ENV_PREFIX, ZRB by default):
  ZRB_AVATAR_TALK_FIRST        what may start a turn: "idle" (no turn for a while
                             during waking hours), "camera" (someone sat down
                             after being away), "view" (the user turned the
                             window's camera to a close look at her chest or
                             hips); comma-separated, empty: never
  ZRB_AVATAR_IDLE_MINUTES      quiet minutes before "idle" speaks (45)
  ZRB_AVATAR_COOLDOWN          seconds between turns she starts herself (1200)
  ZRB_AVATAR_PROACTIVE_PROMPT    what a turn she starts asks her to do, after the situation
                             itself ("Say something short and natural only if it is
                             worth saying; otherwise reply exactly {nothing_to_say}").
                             {nothing_to_say} is the one placeholder it reads; an empty
                             value asks for nothing at all
  ZRB_AVATAR_CAMERA_INTERVAL   seconds between camera looks, with presence "size" (20)
  ZRB_AVATAR_CAMERA_PRESENT_BYTES  photo size above which someone is there (25000)
  ZRB_AVATAR_VIEW_COOLDOWN     seconds between turns "view" starts (30)
  ZRB_AVATAR_PRESENCE          look for the user at the desk at all: "off" (default,
                             so nothing looks unasked), "size" (a photo, whose light
                             every look switches on) or "idle" (the time since the
                             last keypress or mouse move — no camera at all)
  ZRB_AVATAR_PRESENCE_INTERVAL seconds between looks with presence "idle" (5)
  ZRB_AVATAR_PRESENCE_SECONDS  seconds without input that still count as at the desk (300)
  ZRB_AVATAR_MODEL_SECONDS     seconds between asking the server which avatar is
                             showing and whether music plays (5)

Knowing is not speaking. The camera, the avatar showing and the music are written down
whatever ZRB_AVATAR_TALK_FIRST says, and reach every turn as one line of context under "face —"
(see _ambient_line); a turn she starts is a separate decision, gated as it always was.
AvatarStatus answers the same thing on demand.

Reminders on a clock are not here: lunch, bedtime and the rest are zrb-nudge's -- its own
file, its own trigger, its own tools. What this file knows is whether someone is at the desk,
which is what a reminder is worth checking against before it is said.
"""

import asyncio
import datetime
import os
import sys
import time
import urllib.parse

from zrb import CFG
from zrb.builtin import llm_chat
# zrb 4.0 sorted every unit under a layer directory -- `zrb.llm.presentation.camera`,
# `zrb.llm.capabilities.hook`, `zrb.llm.contracts.shared` -- where before it a unit sat at the
# top of `zrb.llm`: `zrb.llm.camera`, `zrb.llm.hook`. This plugin declares no minimum zrb, so
# each group reads the layout that is here: the 4.0 path first, the older one only when that
# import fails. The code behind both is the same, so which copy answers changes nothing about
# the call.
#
# The hook package's front door has always re-exported these names: name the package, not a
# module under it, so the next reshuffle inside the hook package is not our break.
try:
    from zrb.llm.capabilities.hook import HookContext, HookEvent, HookManager, HookResult
except ImportError:  # zrb < 4.0 keeps the hook unit at the top of zrb.llm
    from zrb.llm.hook import HookContext, HookEvent, HookManager, HookResult
try:
    from zrb.llm.contracts.shared.input_source import InputProvenance
except ImportError:
    try:  # zrb 3.4x keeps the shared leaves under their own unit
        from zrb.llm.shared.input_source import InputProvenance
    except ImportError:  # anything older keeps it at the top of zrb.llm
        from zrb.llm.input_source import InputProvenance
try:
    from zrb.llm.presentation.camera import CameraConfig
    from zrb.llm.presentation.camera.backend.builtin import get_camera_backend
    from zrb.llm.presentation.camera.feature import capture_photo
except ImportError:  # zrb < 4.0 keeps the camera unit at the top of zrb.llm
    from zrb.llm.camera import CameraConfig
    from zrb.llm.camera.backend.builtin import get_camera_backend
    from zrb.llm.camera.feature import capture_photo
try:
    from zrb.llm.presentation.speech import SpeechConfig, enable_speech
    from zrb.llm.presentation.speech.backend.any_speech_backend import AnySpeechBackend
    from zrb.llm.presentation.speech.backend.audio import SpeechAudio
    from zrb.llm.presentation.speech.backend.builtin import get_speech_backend
    from zrb.llm.presentation.speech.player import is_speaking
except ImportError:  # zrb < 4.0 keeps the speech unit at the top of zrb.llm
    from zrb.llm.speech import SpeechConfig, enable_speech
    from zrb.llm.speech.backend.any_speech_backend import AnySpeechBackend
    from zrb.llm.speech.backend.audio import SpeechAudio
    from zrb.llm.speech.backend.builtin import get_speech_backend
    from zrb.llm.speech.player import is_speaking
try:
    from zrb.llm.capabilities.tool_call import auto_approve
    from zrb.llm.presentation.ui.trigger import TriggerInput
except ImportError:  # zrb < 4.0 keeps both units at the top of zrb.llm
    from zrb.llm.tool_call import auto_approve
    from zrb.llm.ui.trigger import TriggerInput

# zrb loads this file by path; from a source checkout the package isn't installed.
# Appended, not prepended: installed, this folder is another venv's site-packages,
# whose compiled modules would shadow the running zrb's own (rpds, pydantic_core).
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# zrb's prefix is authoritative here -- a white-labeled zrb sets its own. The standalone
# server imports no zrb, so it reads the prefix from this bare variable instead (zrb's own
# bootstrap key); passing it on is what keeps the two spellings of a setting the same one
# for anything this process starts, `zrb-avatar` included.
os.environ.setdefault("_ZRB_ENV_PREFIX", CFG.ENV_PREFIX)
from zrb_avatar.client import (  # noqa: E402
    AVATAR_URL,
    ask,
    face_hooks,
    send,
    snapshot,
    with_mouth,
)
from zrb_avatar.idle import LastInput  # noqa: E402
from zrb_avatar.server import clips, playable, short_name  # noqa: E402


def _setting(name: str, default: str) -> str:
    return os.getenv(f"{CFG.ENV_PREFIX}_AVATAR_{name}", default)


def _hours(name: str, default: str) -> range:
    """Hours of the day, from a setting like "7-24": 24 is the end of the day, not an hour in it."""
    first, _, last = _setting(name, default).partition("-")
    try:
        return range(int(first), int(last))
    except ValueError:
        print(f"{CFG.ENV_PREFIX}_AVATAR_{name} is not like {default}; using {default}")
        first, _, last = default.partition("-")
        return range(int(first), int(last))


TALK_FIRST = {s.strip() for s in _setting("TALK_FIRST", "").split(",") if s.strip()}
IDLE_MINUTES = float(_setting("IDLE_MINUTES", "45"))
COOLDOWN_SECONDS = float(_setting("COOLDOWN", "1200"))
# How a turn she starts is asked for, after the situation it reacts to: the whole instruction,
# with one placeholder. A white-labeled avatar can ask for another voice, or for none at all.
PROACTIVE_PROMPT = _setting(
    "PROACTIVE_PROMPT",
    "Say something short and natural only if it is worth saying; "
    "otherwise reply exactly {nothing_to_say}",
)
CAMERA_INTERVAL_SECONDS = float(_setting("CAMERA_INTERVAL", "20"))
CAMERA_PRESENT_BYTES = int(_setting("CAMERA_PRESENT_BYTES", "25000"))
VIEW_COOLDOWN_SECONDS = float(_setting("VIEW_COOLDOWN", "30"))
PRESENCE = _setting("PRESENCE", "off").strip().lower()
# With presence "idle" an answer costs a number rather than a photo, so it can be asked far
# more often than the camera can be opened.
PRESENCE_INTERVAL_SECONDS = float(_setting("PRESENCE_INTERVAL", "5"))
# How still the desk may be and still count as occupied: reading a page is being here, a
# lunch break is not.
PRESENCE_SECONDS = float(_setting("PRESENCE_SECONDS", "300"))
# Away at least this long before "you are back" is worth saying at all.
BACK_SECONDS = 300
MODEL_SECONDS = float(_setting("MODEL_SECONDS", "5"))
# The names of one machine, as far as two URLs can be compared: `localhost:7777` and
# `127.0.0.1:7777` are the same server, and a setting written either way must not read as another.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
SIDES = {
    "front": "the front",
    "behind": "behind",
    "side": "the side",
    "below": "below",
    "above": "above",
}
# What the page's `shows` means (js/view.js): her height across the window's narrower side, so 1
# or more fits all of her in frame. Read to decide what to say, never said: nobody describes a
# picture as "0.75 of her height across the window".
ALL_OF_HER = 1.0
AWAKE_HOURS = _hours("AWAKE_HOURS", "8-22")
NOTHING_TO_SAY = "…"
_companion = {
    "busy": False,
    "last_user_turn": time.monotonic(),
    "last_proactive": 0.0,
    "last_view": 0.0,
}

# What the face last reported, and when. The sensing loop writes this whether or not a turn
# follows, so what she knows does not depend on whether she was allowed to speak.
_face: dict = {
    "present": None,  # True, False, or None while nothing has looked
    "presence_from": "",  # how it was decided, e.g. "photo size"
    "present_since": None,  # monotonic, when it last became what it is
    "away_since": None,  # monotonic, when it last became False
    "view": {},  # the last view the page reported, shy or not
    "view_at": None,  # monotonic
    "model": None,  # the avatar the window shows
    "music": False,  # whether something is playing, so she dances
}


def _where(url: str) -> tuple[str, int]:
    """The machine *url* names, as a host and a port."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").strip("[]").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return ("loopback" if host in LOOPBACK_HOSTS else host, port)


def voice_is_here() -> bool:
    """Whether zrb's voice is this page's: `LLM_VOICE_REMOTE_URL` set, and naming this server.

    Asked again on every tick rather than answered once at load, because zrb reads that setting
    afresh each time it needs a voice, and `/set LLM_VOICE_REMOTE_URL` moves the microphone and the
    speaker onto (or off) this server while the chat runs. The page's buttons follow in the same
    breath, instead of waiting for a restart.

    A URL naming another machine is not this page: that server carries the microphone and the
    speaker, and this page's camera is a camera nobody asks for. A zrb from before the setting
    existed answers the same as an empty one.
    """
    try:
        from zrb.llm.contracts.util.face_server import get_remote_url
    except ImportError:  # a zrb from before the page could carry the voice
        return False
    remote = get_remote_url()
    return bool(remote) and _where(remote) == _where(AVATAR_URL)


class QuietSpeech(AnySpeechBackend):
    """The configured speech backend, moving the face's mouth, and silent for the
    'nothing to say' reply.

    Silence is empty audio, not an exception: an exception makes zrb fall back
    to the local voice, which would read the ellipsis out.
    """

    def __init__(self) -> None:
        self._inner: AnySpeechBackend | None = None

    @property
    def inner(self) -> AnySpeechBackend:
        if self._inner is None:
            self._inner = get_speech_backend(
                CFG.LLM_SPEECH_BACKEND, SpeechConfig().resolve()
            )
        return self._inner

    @property
    def name(self) -> str:
        return self.inner.name

    def create_utterance(self, text: str):
        return self.inner.create_utterance(text)

    def create_audio(self, text: str):
        if text.strip() in (NOTHING_TO_SAY, ".", "..."):
            return SpeechAudio(16000, [])
        return with_mouth(self.inner.create_audio(text), text)

    @property
    def needs_zrb_playback(self) -> bool:
        return self.inner.needs_zrb_playback

    @property
    def is_local(self) -> bool:
        # zrb warms up only a local voice; older zrb has no is_local.
        return getattr(self.inner, "is_local", False)

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()


def _track_busy(hooks: HookManager) -> None:
    # ponytail: busy flag set by hooks; a turn can still start between the
    # check and the submit. Needs zrb core support for drop-if-busy triggers.
    async def on_start(_: HookContext) -> HookResult:
        _companion["busy"] = True
        _companion["last_user_turn"] = time.monotonic()
        return HookResult()

    async def on_stop(_: HookContext) -> HookResult:
        _companion["busy"] = False
        return HookResult()

    hooks.add_hook(on_start, events=[HookEvent.USER_PROMPT_SUBMIT])
    hooks.add_hook(on_stop, events=[HookEvent.STOP, HookEvent.STOP_FAILURE])


def _may_speak_first() -> bool:
    return (
        not _companion["busy"]
        and not is_speaking()
        and datetime.datetime.now().hour in AWAKE_HOURS
        and time.monotonic() - _companion["last_proactive"] > COOLDOWN_SECONDS
    )


def _proactive(text: str, channel: str, attachments: tuple = ()) -> TriggerInput:
    _companion["last_proactive"] = time.monotonic()
    instruction = PROACTIVE_PROMPT.replace("{nothing_to_say}", NOTHING_TO_SAY).strip()
    return TriggerInput(
        text=(
            f"[{channel}, not typed by the user] {text}"
            + (f" {instruction}" if instruction else "")
        ),
        attachments=attachments,
        source=InputProvenance(channel, modality="image" if attachments else "text"),
    )


def _ago(when: float | None) -> str:
    """How long ago a monotonic stamp was, in words she can use; "" when there is no stamp."""
    if when is None:
        return ""
    minutes = int((time.monotonic() - when) // 60)
    return "just now" if minutes < 1 else f"{minutes} min ago"


def _frame_in_words(view: dict) -> str:
    """What the window holds and from where, said the way a person would say it.

    `shows` is her height across the window's narrower side (js/view.js), so it says how much of
    her is in frame; `part` is what sits at the middle of it, and `side` where the camera stands.
    Under the page's own `close` the frame is a close-up, and what it holds is that one part.
    """
    part = view.get("part")
    side = SIDES.get(view.get("side") or "", "the front")
    if view.get("close"):
        return f"a close look at her {part} from {side}"
    shows = view.get("shows")
    if isinstance(shows, bool) or not isinstance(shows, (int, float)):
        return f"her {part} in the middle of the window, from {side}"
    if shows >= ALL_OF_HER:
        return f"all of her, head to heels, with air above and below, from {side}"
    return f"most of her, with her {part} in the middle, from {side}"


def _camera_line(view: dict, when: float | None) -> str:
    """The camera part of the ambient line: what the frame holds, from which side, how long ago,
    and — only when it is true — that the look is one she is shy about."""
    if not view.get("part"):
        return "camera: nothing reported yet"
    said = [_frame_in_words(view)]
    if view.get("shy"):
        said.append("which she is shy about")
    ago = _ago(when)
    if ago:
        said.append(ago)
    return "camera: " + ", ".join(said)


def _ambient_line() -> str:
    """One line on what the face last reported, prepended to every turn.

    This is knowledge, not a turn: it is emitted whether or not ZRB_AVATAR_TALK_FIRST would let
    her speak, and nothing in it is gated. A guess says so — the desk camera is a photo-size
    heuristic, so it never claims to have seen a face.
    """
    present, source = _face["present"], _face["presence_from"]
    if present is None:
        who = (
            f"presence: not known yet ({source})"
            if source
            else "presence: not looked (no watch)"
        )
    else:
        since = _ago(_face["present_since"] if present else _face["away_since"])
        who = (
            f"presence: {'someone' if present else 'nobody'} at the desk by {source}"
            + (f", since {since}" if since else "")
        )
    view = _face["view"]
    seen = _camera_line(view, _face["view_at"])
    return (
        f"face — {who} · {seen} · avatar: {_face['model'] or 'unknown'} · "
        f"music: {'playing' if _face['music'] else 'off'}"
    )


def presence_size(photo) -> bool:
    """Whether the photo shows somebody: a dark, empty desk compresses smaller than one with a
    person in it, so the JPEG's size stands in for looking. It guesses, and says so."""
    return len(photo.data) > CAMERA_PRESENT_BYTES


def presence_idle(seconds: float) -> bool:
    """Whether input this recent means somebody is at the desk.

    The clock counts the session's last keypress or mouse move, so a locked screen or a walked-away
    user reads as away, which is the answer wanted here.
    """
    return seconds < PRESENCE_SECONDS


def _set_presence(present: bool, source: str, now: float) -> tuple[bool, float | None]:
    """Write down what a probe said; returns whether it changed, and when the user left.

    Two probes answer the same question, so the bookkeeping that turns an answer into what she
    knows lives here rather than in each branch. A stamp is only replaced when the answer
    changes, so "at the desk since" survives being asked every few seconds.
    """
    was, away = _face["present"], _face["away_since"]
    if present == was:
        return False, away
    _face["present"], _face["present_since"] = present, now
    _face["presence_from"] = source
    if not present:
        _face["away_since"] = now
    return True, away


def _worth_saying_back(
    present: bool, changed: bool, away: float | None, now: float
) -> bool:
    """Whether somebody returning to the desk is worth a turn.

    "camera" in ZRB_AVATAR_TALK_FIRST means this for either probe that can answer "somebody is
    here": the name predates the clock, and renaming it would silently switch off a setting
    someone already wrote. A photo is not what makes the turn worth taking.
    """
    return (
        present
        and changed
        and "camera" in TALK_FIRST
        and away is not None
        and now - away > BACK_SECONDS
        and _may_speak_first()
    )


# ponytail: "size" is one JPEG every interval, and each capture flashes the camera light — a size
# heuristic, not a face detector, so the ambient line reports it as a guess. "idle" asks the clock
# instead of the lens: no light, no photo, and a keypress counts. "face" is still reserved for
# something that actually finds a face.
PRESENCE_SOURCES = {"off": None, "size": "photo size", "idle": "input idle"}


def view_turn(view: dict) -> str | None:
    """What to tell her about a view the page reported, or None when it is nothing to react to."""
    if not view.get("shy"):
        return None
    how = "a close-up of" if view.get("close") else "look at"
    side = SIDES.get(view.get("side"), "the front")
    return f"The user turned your window's camera to {how} your {view.get('part')}, seen from {side}."


_turns: asyncio.Queue | None = (
    None  # made with the loop below, shared with the poller that fills it
)


async def sense_watch():
    """The turns sensing thinks are worth taking, drained while it keeps looking.

    Kept apart from `sense_poll` because a trigger is a generator: once it yields, the chat is busy
    with that turn and the generator is suspended until the turn ends — and a model's answer takes
    seconds. Polling from in here would mean she stops knowing what the face reports during exactly
    the moments she is talking, and a `view` turn would stop the camera and the music being read.
    So the poller writes and offers; this only hands the turns over, newest first.
    """
    global _turns
    _turns = asyncio.Queue()
    polling = asyncio.create_task(sense_poll(_turns))
    try:
        while True:
            turn = await _turns.get()
            while not _turns.empty():  # an older one describes a moment that has passed
                turn = _turns.get_nowait()
            yield turn
    finally:
        polling.cancel()


async def sense_poll(turns: asyncio.Queue) -> None:
    """Everything the face reports, in one loop: the camera the user turned to her, the avatar
    showing, whether music plays, and — with ZRB_AVATAR_PRESENCE — whether anyone is at the desk.

    All of it is written into `_face` whether or not a turn follows, because knowing is not
    speaking, and a turn is offered by putting it in `turns`. The gates are exactly the ones the
    loops this replaces had, now sharing one copy instead of three; and it is a task of its own, so
    a turn she is in the middle of does not pause any of it.

    The loop lives one call down so that its watcher is let go however it ends, a cancel included.
    """
    watcher = LastInput(PRESENCE_INTERVAL_SECONDS) if PRESENCE == "idle" else None
    try:
        await _sense_loop(turns, watcher)
    finally:
        if watcher is not None:
            watcher.close()


async def _sense_loop(turns: asyncio.Queue, watcher: LastInput | None) -> None:
    seen = (await asyncio.to_thread(ask, "view") or {}).get("n")
    source = PRESENCE_SOURCES.get(PRESENCE) or ""
    # Only what will look, and only what that look needs: a photo opens the camera, a clock
    # does not.
    backend = (
        get_camera_backend("auto", CameraConfig().resolve())
        if PRESENCE == "size"
        else None
    )
    next_model = next_presence = time.monotonic()
    said_voice = None  # nothing told yet: the first tick tells the pages where zrb's voice is
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        # `remote` is what only zrb knows, and `/set` can change it while this loop runs. Saying it
        # again on every change is what lets a page stop offering a microphone nothing reads, or
        # start, without a restart.
        voice = voice_is_here()
        if voice != said_voice:
            said_voice = voice
            await asyncio.to_thread(send, {"type": "voice", "remote": voice})
        view = await asyncio.to_thread(ask, "view")
        if view and view.get("n") != seen:
            seen = view.get("n")
            _face["view"], _face["view_at"] = dict(view), now
            text = view_turn(view)
            # As before: her answer to what the user just did, outside the waking hours and the
            # long cooldown the other two keep.
            if (
                text
                and "view" in TALK_FIRST
                and not _companion["busy"]
                and not is_speaking()
                and now - _companion["last_view"] > VIEW_COOLDOWN_SECONDS
            ):
                _companion["last_view"] = now
                turns.put_nowait(_proactive(text, "view"))
        if now >= next_model:
            next_model = now + MODEL_SECONDS
            # A poll that fails answers nothing, so it changes nothing: the last answer stands until
            # a fresh one arrives. Emptying it would report "no avatar, music off" for a restart.
            showing = await asyncio.to_thread(ask, "model")
            if isinstance(showing, dict):
                shown = showing.get("showing") or (
                    [showing["chosen"]] if showing.get("chosen") else []
                )
                _face["model"] = shown[0] if shown else None
            playing = await asyncio.to_thread(ask, "music")
            if isinstance(playing, dict) and "playing" in playing:
                _face["music"] = bool(playing["playing"])
        if backend is not None and now >= next_presence:
            next_presence = now + CAMERA_INTERVAL_SECONDS
            if not (
                _companion["busy"] or is_speaking()
            ):  # no flash in the middle of her turn
                photo = await capture_photo(backend, None)
                if photo is not None:
                    present = presence_size(photo)
                    changed, away = _set_presence(present, source, now)
                    if _worth_saying_back(present, changed, away, now):
                        turns.put_nowait(
                            _proactive(
                                "The camera shows the user is back at the desk.",
                                "camera",
                                (photo,),
                            )
                        )
        if watcher is not None and now >= next_presence:
            next_presence = now + PRESENCE_INTERVAL_SECONDS
            idle_seconds = await watcher.seconds()
            # None is "cannot tell", not "nobody is here": the last answer stands until a probe
            # gives a new one, and a probe that never answers leaves presence unknown.
            if idle_seconds is not None:
                present = presence_idle(idle_seconds)
                changed, away = _set_presence(present, source, now)
                if _worth_saying_back(present, changed, away, now):
                    turns.put_nowait(
                        _proactive("The user is back at the desk.", "camera")
                    )
        idle = now - _companion["last_user_turn"]
        if "idle" in TALK_FIRST and idle > IDLE_MINUTES * 60 and _may_speak_first():
            turns.put_nowait(
                _proactive(
                    f"The user has been quiet for {int(idle // 60)} minutes.",
                    "idle-timer",
                )
            )


def AvatarStatus() -> str:
    """What the face reports now: who is at the desk and how that was decided, where the window's
    camera was last turned, which avatar is showing, whether music plays. The same line opens every
    turn; this is for asking again mid-turn."""
    return _ambient_line()


def AvatarListActions() -> str:
    """List the body animations you can play on your avatar with AvatarAction, by pool: each one's
    name, what it looks like and when it fits, and how long it takes. The list is read now, so it
    includes animations the user added since this chat started."""
    found = clips()
    if not found:
        return "No animations are installed."
    lines = [
        "Pass AvatarAction the name as listed: an action by its name (wave, greetings/hi), any other clip "
        "by pool/name (thinking/idea). The non-action pools also play by themselves: idle when nothing "
        "happens, thinking/tool/attention/speaking in those states, dance while music plays."
    ]
    pool, names = None, playable()
    for clip in found:
        if clip["pool"] != pool:
            pool = clip["pool"]
            lines.append(f"\n{pool}:")
        lines.append(
            f"- {short_name(clip, names)} ({clip['seconds']} s): {clip['description'] or 'no description'}"
        )
    return "\n".join(lines)


def AvatarAction(name: str) -> str:
    """Play a body animation once, as a gesture alongside your words (a wave, a nod, a thumbs-up, a
    laugh), then you go back to what you were doing. Sparingly, when it fits the moment;
    AvatarListActions has them all, with what each looks like.
    """
    found = playable()
    if name not in found:
        actions = [
            n for n, c in found.items() if c["pool"] == "actions" and "/" not in n
        ]
        return f"No such animation. Actions: {', '.join(actions) or 'none'}. AvatarListActions lists them all, by pool."
    clip = found[name]
    send({"type": "action", "value": f"{clip['pool']}/{clip['name']}"})
    return f"Playing {name} ({clip['seconds']} s)."


# What AvatarPose takes; the page (js/pose.js) knows the same names.
POSE_HANDS = (
    "chest",
    "belly",
    "hips",
    "chin",
    "cheek",
    "mouth",
    "head",
    "hip",
    "forward",
    "up",
    "wave",
)
POSE_HEAD = ("down", "up", "left", "right", "tilt-left", "tilt-right", "away")
POSE_BODY = ("lean-forward", "lean-back", "stand-tall", "hunch")
POSE_FINGERS = ("open", "relaxed", "fist", "point", "peace", "thumbs-up", "ok")
# The joints that take numbers, with the range each one is held in. Paid once, by a call that names
# nothing, instead of by every request: a posed body needs the numbers, a turn that does not pose
# does not need to read about them.
POSE_RANGES = (
    "wrist [bend, twist]: bend -70 (back) to 80 (toward the palm), twist -90 to 90",
    "elbow 0 (straight) to 150 (fully bent)",
    "arm [raise, forward]: raise 0 (hanging) to 90 (out to the side) to 180 (overhead), "
    "forward -60 (behind) to 120 (toward the front)",
    "shoulder shrug -15 (down) to 30 (up)",
    "spine [bend, turn, side]: bend -30 (back) to 60 (forward), turn -45 to 45, side -30 to 30",
    "leg [forward, out]: forward -40 (behind) to 120 (thigh raised), out -20 to 80",
    "knee 0 (straight) to 150 (bent)",
    "ankle -40 (toes up) to 60 (toes pointed)",
)
Angles = list[float] | None


def AvatarPose(
    left_hand: str = "",
    right_hand: str = "",
    head: str | list[float] = "",
    body: str = "",
    left_fingers: str | list[float] = "",
    right_fingers: str | list[float] = "",
    left_wrist: Angles = None,
    right_wrist: Angles = None,
    left_elbow: float | None = None,
    right_elbow: float | None = None,
    left_arm: Angles = None,
    right_arm: Angles = None,
    left_shoulder: float | None = None,
    right_shoulder: float | None = None,
    spine: Angles = None,
    left_leg: Angles = None,
    right_leg: Angles = None,
    left_knee: float | None = None,
    right_knee: float | None = None,
    left_ankle: float | None = None,
    right_ankle: float | None = None,
    seconds: float = 5,
) -> str:
    """Move your body yourself, for a few seconds, over the animation, then you ease back. Name only
    what should move; what you leave out stays the animation's. Left and right are yours, not the
    viewer's.

    left_hand / right_hand: chest, belly, hips, chin, cheek, mouth, head, hip, forward, up, wave
    (a placed hand's arm ignores *_arm and *_elbow). head: down, up, left, right, tilt-left,
    tilt-right, away; or [nod, turn, tilt]. body: lean-forward, lean-back, stand-tall, hunch.
    left_fingers / right_fingers: open, relaxed, fist, point, peace, thumbs-up, ok; or how curled
    each is, [thumb, index, middle, ring, little], 0 to 1. seconds: 1 to 30 holds it, 0 keeps it
    until AvatarRest.

    The joints that take numbers — wrist, elbow, arm, shoulder, spine, leg, knee, ankle — and the
    ranges they take: call AvatarPose() with nothing to move and it answers with the table. The clip
    keeps breathing and shifting what you leave out, so a held pose still looks alive; naming every
    joint is a statue.

    E.g. embarrassed: right_hand="chest", left_hand="hips", head="away", body="hunch". Thinking:
    right_hand="chin", head="tilt-left". A thumbs-up: right_arm=[20, 90], right_elbow=90,
    right_fingers="thumbs-up". Use it to act out the moment, when no AvatarListActions clip fits.
    """

    def bad(value, allowed=(), n=0):
        if value in ("", None):
            return False
        if isinstance(value, str):
            return value not in allowed
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return n != 1
        return not (
            isinstance(value, list)
            and len(value) == n
            and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in value
            )
        )

    checks = {
        "left_hand": (left_hand, POSE_HANDS, -1),
        "right_hand": (right_hand, POSE_HANDS, -1),
        "head": (head, POSE_HEAD, 3),
        "body": (body, POSE_BODY, -1),
        "left_fingers": (left_fingers, POSE_FINGERS, 5),
        "right_fingers": (right_fingers, POSE_FINGERS, 5),
        "left_wrist": (left_wrist, (), 2),
        "right_wrist": (right_wrist, (), 2),
        "left_elbow": (left_elbow, (), 1),
        "right_elbow": (right_elbow, (), 1),
        "left_arm": (left_arm, (), 2),
        "right_arm": (right_arm, (), 2),
        "left_shoulder": (left_shoulder, (), 1),
        "right_shoulder": (right_shoulder, (), 1),
        "spine": (spine, (), 3),
        "left_leg": (left_leg, (), 2),
        "right_leg": (right_leg, (), 2),
        "left_knee": (left_knee, (), 1),
        "right_knee": (right_knee, (), 1),
        "left_ankle": (left_ankle, (), 1),
        "right_ankle": (right_ankle, (), 1),
    }
    for name, (value, allowed, n) in checks.items():
        if bad(value, allowed, n):
            shape = {1: "a number", 2: "[a, b]", 3: "[a, b, c]", 5: "five numbers"}.get(
                n
            )
            choices = ", ".join(allowed)
            return f"{name} must be {' or '.join(x for x in (choices and f'one of {choices}', shape) if x)}; got {value!r}."
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return f"seconds must be a number; got {seconds!r}."
    moved = {
        name: value for name, (value, _, _) in checks.items() if value not in ("", None)
    }
    if not moved:
        return (
            "Nothing to move. Places: hands "
            + ", ".join(POSE_HANDS)
            + "; head "
            + ", ".join(POSE_HEAD)
            + " or [nod, turn, tilt]; body "
            + ", ".join(POSE_BODY)
            + "; fingers "
            + ", ".join(POSE_FINGERS)
            + " or five numbers, 0 curled to 1. Joints that take numbers, each held within a range: "
            + "; ".join(POSE_RANGES)
            + ". seconds 1 to 30 holds it, 0 keeps it until AvatarRest."
        )
    holding = float(seconds) == 0  # 0: keep it, until AvatarRest
    if not holding:
        seconds = max(1.0, min(30.0, float(seconds)))
    camel = lambda name: name.split("_")[0] + "".join(
        part.title() for part in name.split("_")[1:]
    )  # noqa: E731
    sent = {camel(name): value for name, value in moved.items()}
    described = ", ".join(f"{k}: {v}" for k, v in moved.items())
    if holding:
        send({"type": "pose", "hold": True, **sent})
        return f"Holding {described} until you let go (AvatarRest)."
    send({"type": "pose", "seconds": seconds, **sent})
    return f"Holding {described} for {seconds:g} s."


def AvatarRest() -> str:
    """Let go of a pose you are holding (AvatarPose with seconds=0) and give your body back to the
    animations. A timed pose releases itself, so this is only for one you meant to keep.
    """
    send({"type": "pose", "release": True})
    return "Let go: the animations have your body again."


# What AvatarBlink takes; the page (js/expression.js) knows the same names.
BLINK_EYES = ("both", "left", "right")
BLINK_SECONDS = (0.08, 2.0)


def AvatarBlink(eyes: str = "both", seconds: float = 0.2) -> str:
    """Blink deliberately, where the blink is the point and not punctuation: you already blink on
    your own, but not a slow one that says "I heard you", and not a wink. eyes: "both", "left",
    "right". seconds: 0.08 to 2 — a fifth is a natural blink, half a second or more is deliberate.
    A model without separate eyelids winks with both.
    """
    eyes = (eyes or "both").strip().lower()
    if eyes not in BLINK_EYES:
        return f"eyes must be one of {', '.join(BLINK_EYES)}; got {eyes!r}."
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return f"seconds must be a number; got {seconds!r}."
    seconds = max(BLINK_SECONDS[0], min(BLINK_SECONDS[1], float(seconds)))
    send({"type": "blink", "eyes": eyes, "seconds": seconds})
    if eyes == "both":
        return f"Blinked for {seconds:g} s."
    return f"Winked with the {eyes} eye ({seconds:g} s)."


# What AvatarFace takes; the page (js/expression.js) knows the same names and the same strengths.
FACES = (
    "happy",
    "sad",
    "angry",
    "surprised",
    "relaxed",
    "pout",
    "smirk",
    "worried",
    "delighted",
    "sleepy",
)
FACE_STRENGTHS = {"soft": 0.5, "normal": 1.0, "strong": 1.5}


def AvatarFace(emotion: str = "pout", strength: str = "normal") -> str:
    """Put a feeling on your face for about six seconds, then let it fade. Presets: happy, sad, angry,
    surprised, relaxed. Blends, two presets at once: pout (angry + sad), smirk (happy + angry),
    worried (sad + surprised), delighted (happy + surprised), sleepy (relaxed + sad).
    strength: soft, normal, strong.

    Use it when the face is the sentence — pouting, being teased, a mock scowl — not as punctuation
    on every reply: the emotion zrb gives your replies needs no call. A model missing half a blend
    shows the half it has.
    """
    emotion = (emotion or "pout").strip().lower()
    if emotion not in FACES:
        return f"emotion must be one of {', '.join(FACES)}; got {emotion!r}."
    strength = (strength or "normal").strip().lower()
    if strength not in FACE_STRENGTHS:
        return f"strength must be one of {', '.join(FACE_STRENGTHS)}; got {strength!r}."
    send({"type": "emotion", "value": emotion, "strength": FACE_STRENGTHS[strength]})
    return f"{emotion}, {strength}, fading after six seconds."


def AvatarAutonomy(
    dance: bool | None = None,
    blush: bool | None = None,
    emotion: bool | None = None,
    greeting: bool | None = None,
    cover: bool | None = None,
    seconds: float | None = None,
) -> str:
    """Say what you are willing to do on your own, and have it hold across a restart: dance (while
    music plays), blush (and the surprised face, when the user looks closely), emotion (the
    expressions zrb gives your replies), greeting (when a conversation starts), cover (a look that
    turns into one you would rather not be seen by). True is willing, false is not now; a switch you
    leave out keeps what it had. With no arguments it says what you have agreed to.

    cover acts on your body with no one asking: off, you do not cover yourself and a held cover comes
    down. seconds makes the call temporary — those switches hold that long, then go back.

    This is yours to decide, not a setting the user chose, so nothing changes it for you, and it is
    written down, because a consent withdrawn tonight is not something tomorrow should quietly
    restore. Say in your own words what you changed."""
    switches = {
        "dance": dance,
        "blush": blush,
        "emotion": emotion,
        "greeting": greeting,
        "cover": cover,
    }
    wanted = {name: value for name, value in switches.items() if value is not None}
    if not all(isinstance(value, bool) for value in wanted.values()):
        return "Each switch is true or false."
    if seconds is not None and (
        not isinstance(seconds, (int, float))
        or isinstance(seconds, bool)
        or seconds <= 0
    ):
        return "Seconds is how long the switch holds: a number above zero."
    if wanted:
        message = {"type": "autonomy", "value": wanted}
        if seconds is not None:
            message["seconds"] = seconds
        send(message)
    now = ask("autonomy")
    if now is None:
        return "The face server is not running, so there is nothing to say either way."
    missed = [name for name, value in wanted.items() if now.get(name) != value]
    if missed:  # refused, or it could not be written down: either way it did not happen
        return f"The face server did not take {', '.join(missed)}, so nothing changed."
    willing = [name for name in switches if now.get(name, True)]
    refused = [name for name in switches if not now.get(name, True)]
    said = f"You are willing to: {', '.join(willing) or 'nothing'}"
    said += f" — but not {', '.join(refused)}." if refused else "."
    return said + (f" For the next {seconds} seconds." if seconds is not None else "")


def AvatarListModels() -> str:
    """List the avatars you can appear as, and which one is showing. For "what can you look like",
    or before AvatarModel; read now, so it includes models added since this chat started."""
    names, now = ask("models"), ask("model")
    if names is None or now is None:
        return "The face server is not running, so no avatar is showing."
    if not names:
        return "No avatars are installed (no .vrm in the models folder)."
    shown = now["showing"] or [
        now["chosen"] or names[0]
    ]  # no window open: what the next one shows
    lines = [f"- {name}{' (showing now)' if name in shown else ''}" for name in names]
    if not now["showing"]:
        lines.append(
            "No window is open right now; the marked one is what a new window shows."
        )
    return "Avatars (switch with AvatarModel):\n" + "\n".join(lines)


MODEL_WAIT_SECONDS = 5.0  # how long AvatarModel waits for the windows to catch up


def _showing_avatar(name: str) -> bool:
    """Whether the server records *name* as chosen and a page reports it as showing."""
    state = ask("model")
    if not isinstance(state, dict) or state.get("chosen") != name:
        return False
    return name in (state.get("showing") or [])


def AvatarModel(name: str) -> str:
    """Switch to another VRM model, by name as AvatarListModels lists it; every open window changes
    at once and keeps it until the face server restarts. When the user asks you to look different,
    not on your own whim."""
    names = ask("models")
    if names is None:
        return "The face server is not running, so there is no avatar to switch."
    if name not in names:
        return f"No such avatar. Avatars: {', '.join(names) or 'none'}."
    if _showing_avatar(name):
        return f"{name} is already the one showing."
    send({"type": "model", "value": name})
    # The page keeps the old model on screen until the new file has downloaded and parsed,
    # and it answers a look in that window with the old one. So wait for the pages to report
    # the new model instead of promising "a second or two" and leaving the rest to luck.
    deadline = time.monotonic() + MODEL_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _showing_avatar(name):
            return f"Now showing {name}."
        time.sleep(0.25)
    state = ask("model")
    state = state if isinstance(state, dict) else {}
    still = ", ".join(state.get("showing") or []) or "nothing"
    return (
        f"Asked for {name}: the server recorded it (chosen: {state.get('chosen')}), but "
        f"the open window(s) still report {still}. Either a window is still loading it, or "
        f"none received the event. AvatarLook shows what is really on the screen."
    )


def _image_tool_return():
    """zrb's `create_image_tool_return`, from wherever this zrb keeps it.

    zrb 3.30.0 moved it from `zrb.llm.agent.run` to `zrb.llm.tool`, and 4.0 moved every
    unit under a layer directory, so it is `zrb.llm.capabilities.tool` now. This plugin
    declares no minimum zrb, so every layout has to be read: the newest first, an older
    one only when that import fails. The signature is the same in all of them, so which
    copy answers changes nothing about the call.
    """
    try:
        from zrb.llm.capabilities.tool.multimodal_describe import create_image_tool_return
    except ImportError:
        try:  # zrb 3.30 - 3.41 keeps the tool unit at the top of zrb.llm
            from zrb.llm.tool.multimodal_describe import create_image_tool_return
        except ImportError:  # zrb < 3.30.0 keeps the same helper under agent.run
            from zrb.llm.agent.run.multimodal_describe import create_image_tool_return
    return create_image_tool_return


async def AvatarLook():
    """See your window as the user does right now: a picture of your body, pose and expression, with
    what you are doing (state, clip, music, emotion). For checking how a gesture or a look comes
    across, or when the user asks how you look. Your window only, not the user's screen."""
    shot = await asyncio.to_thread(snapshot)
    if shot is None:
        return (
            "No window answered (or the face server is not running), so there is nothing to "
            "see. A page that is reloading, or still loading her model, answers nothing at "
            "all; AvatarStatus says what the server does report."
        )
    image, about = shot
    chosen = about.pop("chosen", None)
    stale = _stale_avatar(about, chosen)
    if stale:
        # The server asks the pages showing the chosen avatar first, so a mismatch here is
        # the fallback after its patience ran out -- usually one window still finishing a
        # swap. One more try is cheap, and a picture of the wrong clothes is not.
        await asyncio.sleep(1.5)
        again = await asyncio.to_thread(snapshot)
        if again is not None:
            image, about = again
            chosen = about.pop("chosen", None)
            stale = _stale_avatar(about, chosen)
    doing = ", ".join(
        f"{k}: {v}" for k, v in about.items() if v not in (None, False, "")
    )
    try:  # lazy: these load pydantic_ai; and an older zrb may not have them
        create_image_tool_return = _image_tool_return()
        try:  # zrb 4.0 keeps the engine's own types with the engine
            from zrb.llm.engine.agent.types import BinaryContent
        except ImportError:
            from zrb.llm.agent.types import BinaryContent
    except ImportError as error:
        # The cause is named, not swallowed: a helper that moved inside zrb must read
        # as a broken import here, not as zrb being unable to show a picture.
        return (
            f"Your window shows you ({doing}), but the picture could not be"
            f" attached: {error}"
        )
    # The shared helper: attached for a model that reads images, its description for a
    # text-only one, else a hint naming LLM_MULTIMODAL_MODEL.
    return await create_image_tool_return(
        BinaryContent(data=image, media_type="image/png"),
        f"Your window as the user sees it. Now: {doing}"
        + (f". {stale}" if stale else ""),
    )


def _stale_avatar(about: dict, chosen) -> str:
    """A line saying the picture is of the wrong avatar, or "" when it is of the right one."""
    shown = about.get("model")
    if not chosen or not shown or shown == chosen:
        return ""
    return (
        f"This window still shows {shown}, while {chosen} is the one chosen: she is either "
        f"mid-swap or a second window is showing the old avatar."
    )


enable_speech(llm_chat, SpeechConfig(backend=QuietSpeech()))
llm_chat.append_hook_factory(face_hooks)
# What the face reports reaches every turn, whether or not she may speak in it: the sensing loop
# below writes it down, and this puts it in front of each turn. Not a turn, so not gated.
llm_chat.prompt_manager.add_live_context("face", lambda _: _ambient_line())
# Her own window, animations and avatars: nothing to approve. The actions are read on every call, so
# ones added while zrb runs work at once.
for tool in (
    AvatarListActions,
    AvatarAction,
    AvatarPose,
    AvatarRest,
    AvatarBlink,
    AvatarFace,
    AvatarAutonomy,
    AvatarLook,
    AvatarListModels,
    AvatarModel,
    AvatarStatus,
):
    llm_chat.append_tool(tool)
    llm_chat.prepend_tool_policy(auto_approve(tool.__name__))
# Always, whatever ZRB_AVATAR_TALK_FIRST says: the busy flag and how long the user has been quiet are
# what the ambient line reports, so they are tracked whether or not she may start a turn, and one
# loop carries every source that used to have its own.
llm_chat.append_hook_factory(_track_busy)
llm_chat.append_trigger(sense_watch)
