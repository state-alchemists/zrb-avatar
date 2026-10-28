"""zrb-avatar's zrb side: load it and zrb gets a face, a voice that moves its mouth,
and (optionally) a companion that speaks first.

    export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-avatar init-path)"

Loading this script turns the face on: zrb's state and speech go to the server
(server.py, `python -m zrb_avatar serve`). It
also owns zrb's speech (`enable_speech`), so other init scripts should only set
the voice (CFG.DEFAULT_LLM_SPEECH_BACKEND / _VOICE), not call `enable_speech`.
zrb also gets nine tools of hers: AvatarListActions lists her animations by pool with what each looks
like, AvatarAction plays one once, AvatarPose moves her body itself (hands, head, posture) for a few
seconds, AvatarRest lets go of a pose she is holding, AvatarAutonomy reads or changes what she may
do unasked, AvatarLook shows her own window as the user sees it, AvatarListModels lists the avatars (VRM
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
  ZRB_AVATAR_NUDGE             the nudge, her own clock: "auto" (default) offers a
                             turn when a rule says there is something to ask
                             about (lunch, bedtime, back at the desk, quiet, the
                             last turn failed); "off" turns it off
  ZRB_AVATAR_NUDGE_SECONDS     seconds between looking at those rules (600)
  ZRB_AVATAR_NUDGE_QUIET       quiet minutes before "quiet" is worth asking about (90)
  ZRB_AVATAR_NUDGE_AWAY        minutes away that make coming back worth noticing (20)
  ZRB_AVATAR_NUDGE_BACK        minutes that "you are back" stays true (10)

Knowing is not speaking. The camera, the avatar showing and the music are written down
whatever ZRB_AVATAR_TALK_FIRST says, and reach every turn as one line of context under "face —"
(see _ambient_line); a turn she starts is a separate decision, gated as it always was.
AvatarStatus answers the same thing on demand.
"""

import asyncio
import datetime
import json
import os
import sys
import time

from zrb import CFG
from zrb.builtin import llm_chat
from zrb.llm.camera import CameraConfig
from zrb.llm.camera.backend.builtin import get_camera_backend
from zrb.llm.camera.feature import capture_photo
from zrb.llm.hook.interface import HookContext, HookResult
from zrb.llm.hook.manager import HookManager
from zrb.llm.hook.types import HookEvent
from zrb.llm.input_source import InputProvenance
from zrb.llm.speech import SpeechConfig, enable_speech
from zrb.llm.speech.backend.any_speech_backend import AnySpeechBackend
from zrb.llm.speech.backend.audio import SpeechAudio
from zrb.llm.speech.backend.builtin import get_speech_backend
from zrb.llm.speech.player import is_speaking
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
from zrb_avatar.client import ask, face_hooks, send, snapshot, with_mouth  # noqa: E402
from zrb_avatar.idle import LastInput  # noqa: E402
from zrb_avatar.server import HOME, clips, playable, short_name  # noqa: E402


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


def _window(name: str, default: str) -> tuple[int, int]:
    """A span of the day in minutes, from a setting like "12:00-13:30"; 24:00 is its end."""
    first, _, last = _setting(name, default).replace("24:00", "23:60", 1).partition("-")
    try:
        return _minutes(first), _minutes(last)
    except ValueError:
        print(f"{CFG.ENV_PREFIX}_AVATAR_{name} is not like {default}; using {default}")
        first, _, last = default.replace("24:00", "23:60", 1).partition("-")
        return _minutes(first), _minutes(last)


def _minutes(text: str) -> int:
    hour, _, minute = text.strip().partition(":")
    return int(hour) * 60 + int(minute)


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
SIDES = {
    "front": "the front",
    "behind": "behind",
    "side": "the side",
    "below": "below",
    "above": "above",
}
AWAKE_HOURS = _hours("AWAKE_HOURS", "8-22")
# The nudge keeps its own hours. AWAKE_HOURS is shared by every talk-first path and ends at 22:00
# by default, so a "go to bed" reminder inside it could never fire: it is late by the time it is due.
NUDGE = _setting("NUDGE", "auto").strip().lower()  # auto | off
NUDGE_HOURS = _hours("NUDGE_HOURS", "7-24")
NUDGE_SECONDS = float(
    _setting("NUDGE_SECONDS", "600")
)  # how often the rules are looked at
NUDGE_LUNCH = _window(
    "NUDGE_LUNCH", "12:00-13:30"
)  # minutes of the day, the way a clock reads
NUDGE_BED = _window("NUDGE_BED", "22:30-24:00")
NUDGE_QUIET_MINUTES = float(_setting("NUDGE_QUIET", "90"))
NUDGE_AWAY_MINUTES = float(_setting("NUDGE_AWAY", "20"))
NUDGE_BACK_MINUTES = float(
    _setting("NUDGE_BACK", "10")
)  # how long "you are back" stays true
NUDGE_FILE = HOME / "nudge.json"  # the day's ledger: a restart is not a memory wipe
NUDGE_DAILY = ("lunch", "bed")  # once a day; the rest repeat after their own wait
NUDGE_RULES = (
    *NUDGE_DAILY,
    "failure",
    "return",
    "quiet",
    "failed",
)  # "failed" is a count
NOTHING_TO_SAY = "…"
_companion = {
    "busy": False,
    "last_user_turn": time.monotonic(),
    "last_proactive": 0.0,
    "last_view": 0.0,
    "failed": 0,
}
_nudge = {"date": "", "said": {}}  # today's ledger, read in _nudge_day

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
        return with_mouth(self.inner.create_audio(text))

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

    async def on_stop(context: HookContext) -> HookResult:
        _companion["busy"] = False
        if (
            context.event == HookEvent.STOP_FAILURE
        ):  # an error, not a normal stop: worth a word
            _companion["failed"] += 1
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


def _may_nudge(now: datetime.datetime) -> bool:
    """Its own gate, with NUDGE_HOURS instead of AWAKE_HOURS: the bed reminder is due after the
    hours every other talk-first path keeps."""
    return (
        not _companion["busy"]
        and not is_speaking()
        and now.hour in NUDGE_HOURS
        and time.monotonic() - _companion["last_proactive"] > COOLDOWN_SECONDS
    )


def _nudge_save() -> None:
    """Write the day's ledger beside itself and move it into place: a half file would read as a
    day with nothing said, and start the day over."""
    try:
        NUDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        half = NUDGE_FILE.with_name(NUDGE_FILE.name + ".new")
        half.write_text(json.dumps(_nudge), encoding="utf-8")
        half.replace(NUDGE_FILE)
    except (
        OSError
    ) as e:  # a read-only home: this session remembers, the next one starts fresh
        print(f"not remembering nudges: {e}")


def _nudge_day(now: datetime.datetime) -> dict:
    """Today's ledger: what the rules have already used, read once when the day turns over.

    Kept on disk so a restart does not start the day again -- zrb restarted at 13:00 should not
    mention lunch a second time.
    """
    today = now.strftime("%Y-%m-%d")
    if _nudge["date"] == today:
        return _nudge["said"]
    saved: dict = {}
    try:
        read = json.loads(NUDGE_FILE.read_text(encoding="utf-8"))
        if (
            isinstance(read, dict)
            and read.get("date") == today
            and isinstance(read.get("said"), dict)
        ):
            saved = {k: v for k, v in read["said"].items() if k in NUDGE_RULES}
    except (OSError, ValueError):
        pass  # no ledger, or not one of ours: the day starts empty, which is today's behaviour
    _nudge["date"], _nudge["said"] = today, saved
    _nudge_save()  # rolled over, so yesterday's entries cannot come back
    return _nudge["said"]


def _due(said: dict, rule: str, minutes: float) -> bool:
    """Whether a rule that repeats may speak again: never said, or said longer ago than `minutes`."""
    last = said.get(rule)
    return not isinstance(last, (int, float)) or time.time() - last >= minutes * 60


def _back_after() -> float:
    """How long the user was away, when they are back now and gone long enough to notice; 0
    otherwise. The moment passes: ten minutes later, "you are back" is no longer true.
    """
    present_since, away_since = _face["present_since"], _face["away_since"]
    if not (
        _face["present"] and present_since and away_since and present_since > away_since
    ):
        return 0.0
    if time.monotonic() - present_since > NUDGE_BACK_MINUTES * 60:
        return 0.0
    away = (present_since - away_since) / 60
    return away if away >= NUDGE_AWAY_MINUTES else 0.0


def nudge_candidate(now: datetime.datetime, said: dict) -> tuple[str, str] | None:
    """What is worth a turn, or None: the cheap half of the nudge.

    Rules, not a model, because this has to be answerable every NUDGE_SECONDS without spending
    anything. Whether it is worth saying is the turn's own question, and NOTHING_TO_SAY is a real
    answer to it. The ledger is what stops a rule repeating inside its own wait.
    """
    if now.hour not in NUDGE_HOURS:
        return None
    minute = now.hour * 60 + now.minute
    if NUDGE_LUNCH[0] <= minute < NUDGE_LUNCH[1] and "lunch" not in said:
        return "lunch", "It is lunchtime and the user is still working."
    if NUDGE_BED[0] <= minute < NUDGE_BED[1] and "bed" not in said:
        return "bed", f"It is {now:%H:%M} and the user is still working."
    if _companion["failed"] > said.get("failed", 0):
        return "failure", "The last turn ended in an error rather than a normal stop."
    away = _back_after()
    if away and _due(said, "return", NUDGE_AWAY_MINUTES):
        return "return", f"The user was away for {int(away)} minutes and is back."
    quiet = (time.monotonic() - _companion["last_user_turn"]) / 60
    if quiet >= NUDGE_QUIET_MINUTES and _due(said, "quiet", NUDGE_QUIET_MINUTES):
        return "quiet", f"The user has been quiet for {int(quiet)} minutes."
    return None


async def nudge_watch():
    """A turn offered every NUDGE_SECONDS, when a rule says there is something to ask about.

    Two halves on purpose: the rules cost nothing and run every ten minutes; the turn they start is
    where the model decides whether it is worth saying anything at all, and a reply of
    NOTHING_TO_SAY is shown as … and spoken as nothing. The ledger is written before the turn, so a
    turn that errors, or a restart while it runs, cannot make the same nudge twice.
    """
    if NUDGE == "off":
        return
    while True:
        await asyncio.sleep(NUDGE_SECONDS)
        now = datetime.datetime.now()
        said = _nudge_day(now)
        candidate = nudge_candidate(now, said)
        if candidate is None or not _may_nudge(now):
            continue
        rule, text = candidate
        said[rule] = True if rule in NUDGE_DAILY else time.time()
        said["failed"] = _companion["failed"]
        _nudge_save()
        yield _proactive(text, "nudge")


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
    seen = "camera: nothing reported yet"
    if view.get("part"):
        how = "a close-up of" if view.get("close") else "a view of"
        where = SIDES.get(view.get("side") or "", "the front")
        shy = ", which she is shy about" if view.get("shy") else ""
        seen = (
            f"camera: {how} her {view['part']} from {where}, "
            f"{view.get('shows')} of her height across the window, {_ago(_face['view_at'])}{shy}"
        )
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
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
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
    """What the face reports right now: whether anyone is at the desk (and how that was decided),
    what the user last turned the window's camera to, which avatar is showing, and whether music
    is playing. Read it when you want to know rather than guess; the same line is already put in
    front of every turn, so this is for asking again mid-turn."""
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
    """Play a body animation on your avatar once, like a gesture alongside your words (a wave
    hello, a nod, a thumbs-up, a laugh), then she goes back to what she was doing. Use it
    sparingly, when it fits the moment; AvatarListActions lists them all with what each looks like.
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
    """Move your avatar's body yourself, for a few seconds, over whatever animation is playing;
    then she eases back. Name only what should move; what you leave out stays the animation's.
    Left and right are yours (her own), not the viewer's. Angles are degrees; each is held
    within a natural range.

    Places and presets:
    left_hand / right_hand: where that hand goes. "chest" (forearm across your chest, covering
      it), "hips" (in front of your hips, covering them), "belly", "chin", "cheek", "mouth",
      "head" (on top of it), "hip" (on your side, akimbo), "forward" (reaching toward the user),
      "up" (raised), "wave". A placed hand's arm ignores *_arm and *_elbow.
    head: "down", "up", "left", "right", "tilt-left", "tilt-right", "away" (turned aside, shy);
      or [nod, turn, tilt]: nod -40 (up) to 50 (down), turn -70 (right) to 70 (left), tilt -40 to 40.
    body: "lean-forward", "lean-back", "stand-tall", "hunch" (shoulders in, knees together, shy).

    Joints:
    left_fingers / right_fingers: "open", "relaxed", "fist", "point", "peace", "thumbs-up", "ok";
      or how curled each is, [thumb, index, middle, ring, little], 0 straight to 1 curled.
    left_wrist / right_wrist: [bend, twist]: bend -70 (back) to 80 (toward the palm), twist -90 to 90.
    left_elbow / right_elbow: 0 (straight) to 150 (fully bent).
    left_arm / right_arm: [raise, forward]: raise 0 (hanging) to 90 (out to the side) to 180
      (overhead); forward -60 (behind) to 120, swinging it toward the front.
    left_shoulder / right_shoulder: shrug, -15 (down) to 30 (up).
    spine: [bend, turn, side]: bend -30 (back) to 60 (forward), turn -45 to 45, side -30 to 30.
    left_leg / right_leg: [forward, out]: forward -40 (behind) to 120 (thigh raised), out -20 to 80.
    left_knee / right_knee: 0 (straight) to 150 (bent).
    left_ankle / right_ankle: -40 (toes up) to 60 (toes pointed).
    seconds: how long to hold it, 1 to 30; 0 holds it until AvatarRest, so you keep that posture
      instead of borrowing it. Name only the joints that matter: the clip goes on breathing and
      shifting everything else, so a held pose still looks alive. Naming every joint is a statue.

    E.g. covering yourself when embarrassed: right_hand="chest", left_hand="hips", head="away",
    body="hunch". Thinking: right_hand="chin", head="tilt-left". A thumbs-up: right_arm=[20, 90],
    right_elbow=90, right_fingers="thumbs-up". Standing on one leg: left_leg=[60, 0], left_knee=90.
    Use it to act out the moment, alongside your words, when no animation from AvatarListActions fits.
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
        return "Nothing to move: name at least one hand, joint, head or body."
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


def AvatarAutonomy(
    dance: bool | None = None,
    blush: bool | None = None,
    emotion: bool | None = None,
    greeting: bool | None = None,
) -> str:
    """Say what you are willing to do on your own, and have it hold across a restart: `dance` while
    music plays, `blush` (and the surprised face) when the user looks closely at you, `emotion` for
    the expressions zrb gives your replies, `greeting` when a conversation starts. Each is true
    (willing) or false (not now); leave one out to keep it as it is. With no arguments it tells you
    what you have agreed to now.

    This is yours to decide, not a setting the user chose, so nothing changes it for you — and it
    is written down, because a consent withdrawn tonight is not something tomorrow should quietly
    restore. Use it when the moment makes you not want something, and say what you changed in your
    own words: the user did not ask for a report, but they are the one it shows."""
    switches = {
        "dance": dance,
        "blush": blush,
        "emotion": emotion,
        "greeting": greeting,
    }
    wanted = {name: value for name, value in switches.items() if value is not None}
    if not all(isinstance(value, bool) for value in wanted.values()):
        return "Each switch is true or false."
    if wanted:
        send({"type": "autonomy", "value": wanted})
    now = ask("autonomy")
    if now is None:
        return "The face server is not running, so there is nothing to say either way."
    missed = [name for name, value in wanted.items() if now.get(name) != value]
    if missed:  # refused, or it could not be written down: either way it did not happen
        return f"The face server did not take {', '.join(missed)}, so nothing changed."
    willing = [name for name in switches if now.get(name, True)]
    refused = [name for name in switches if not now.get(name, True)]
    said = f"You are willing to: {', '.join(willing) or 'nothing'}"
    return said + (f" — but not {', '.join(refused)}." if refused else ".")


def AvatarListModels() -> str:
    """List the avatars (VRM models) you can appear as, and say which one is showing now. Use it
    when the user asks what you can look like, or before AvatarModel. The list is read now, so it
    includes models the user added since this chat started."""
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


def AvatarModel(name: str) -> str:
    """Switch your avatar to another VRM model, by name as AvatarListModels lists it; every open window
    changes at once and keeps it until the face server restarts. Use it when the user asks you to
    look different or to change your avatar, not on your own whim."""
    names = ask("models")
    if names is None:
        return "The face server is not running, so there is no avatar to switch."
    if name not in names:
        return f"No such avatar. Avatars: {', '.join(names) or 'none'}."
    send({"type": "model", "value": name})
    return f"Switching to {name}; she changes in a second or two."


async def AvatarLook():
    """See your avatar as the user sees it right now: a picture of your window (your body, pose
    and expression, from the user's side), with what you are doing (state, the animation
    playing, music, emotion). Use it to check how a gesture or your look comes across, or when
    the user asks how you look. It shows only your own window, not the user's screen."""
    shot = await asyncio.to_thread(snapshot)
    if shot is None:
        return "Your window is not open (or the face server is not running), so there is nothing to see."
    image, about = shot
    doing = ", ".join(
        f"{k}: {v}" for k, v in about.items() if v not in (None, False, "")
    )
    try:  # lazy: these load pydantic_ai; and an older zrb may not have them
        from zrb.llm.agent.run.multimodal_describe import create_image_tool_return
        from zrb.llm.agent.types import BinaryContent
    except ImportError:
        return f"Your window shows you ({doing}), but this zrb cannot pass pictures to you."
    # The shared helper: attached for a model that reads images, its description for a
    # text-only one, else a hint naming LLM_MULTIMODAL_MODEL.
    return await create_image_tool_return(
        BinaryContent(data=image, media_type="image/png"),
        f"Your window as the user sees it. Now: {doing}",
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
# The nudge: its own clock, and its own hours -- see NUDGE_HOURS. `ZRB_AVATAR_NUDGE=off` turns it off.
llm_chat.append_trigger(nudge_watch)
