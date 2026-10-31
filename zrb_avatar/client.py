"""Sending to the face: state and emotion from zrb's hooks (`face_hooks`), mouth
openness from the speech audio (`with_mouth`). zrb_init.py wires both into zrb.
"""

import asyncio
import json
import math
import os
import urllib.request
from array import array

from zrb.config.config import CFG

# zrb spells an avatar's settings <ENV_PREFIX>_AVATAR_* here, where CFG.ENV_PREFIX is
# authoritative: a white-labeled zrb sets its own prefix and the variables follow. The
# standalone server (server.py, which imports no zrb) reads the same prefix from the bare
# `_ZRB_ENV_PREFIX` -- zrb's bootstrap key -- so the two agree without sharing code.
AVATAR_URL = os.getenv(
    f"{CFG.ENV_PREFIX}_AVATAR_URL", "http://127.0.0.1:7777/event"
)
MOUTH_STEP = 0.04  # seconds of audio per mouth value
# Calibration knob: raise it if the mouth barely opens, lower it if it never closes.
MOUTH_GAIN = float(os.getenv(f"{CFG.ENV_PREFIX}_AVATAR_MOUTH_GAIN", "4"))
# Captions under her: the sentence being spoken, in the window. Off for a machine on a shared
# desk, or for anyone who would rather read in zrb's own terminal.
SUBTITLE = os.getenv(f"{CFG.ENV_PREFIX}_AVATAR_SUBTITLE", "on").strip().lower() not in (
    "off",
    "0",
    "false",
    "no",
)


def send(message: dict) -> None:
    request = urllib.request.Request(
        AVATAR_URL, json.dumps(message).encode(), {"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=0.2).close()
    except OSError:
        pass  # face app not running


def ask(path: str, timeout: float = 2):
    """The server's JSON answer to GET <path> ("models", "model"); None while it is not running."""
    url = AVATAR_URL.rsplit("/", 1)[0] + "/" + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def consents(what: str) -> bool:
    """Whether she agreed to do `what` on her own (`dance`, `blush`, `emotion`, `greeting`).

    No answer -- no server, or one too old to have the switches -- means yes, which is what every
    one of these did before she could say otherwise.
    """
    agreed = ask("autonomy", timeout=0.5)
    return True if not isinstance(agreed, dict) else agreed.get(what, True) is not False


def snapshot(timeout: float = 10) -> tuple[bytes, dict] | None:
    """A PNG of her window as the user sees it, and what she is doing (state, the clip playing,
    music, emotion); None while the server or the window is not there."""
    url = AVATAR_URL.rsplit("/", 1)[0] + "/snapshot"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            about = json.loads(response.headers.get("X-Face") or "{}")
            return response.read(), about if isinstance(about, dict) else {}
    except (OSError, ValueError):
        return None


def _openness(window) -> float:
    """How far the mouth opens (0..1) over one window of 16-bit samples."""
    return round(
        min(
            1.0,
            MOUTH_GAIN * math.sqrt(sum(s * s for s in window) / len(window)) / 32768,
        ),
        2,
    )


class Mouth:
    """Mouth openness 0..1 for each MOUTH_STEP of the audio, carrying the
    samples that do not fill a step over to the next chunk.

    A value must be worth exactly one step: the page plays one per step. A
    chunk rarely ends on a step, and counting its short last window as a whole
    one (piper streams 4096 bytes = 2048 samples, 2.32 steps at 22 kHz, so 3
    values, 120 ms, for 93 ms of audio) ran the mouth 1.29x longer than the
    voice: a 10 s reply left her mouth moving for 2.9 s after it stopped.
    """

    def __init__(self, sample_rate: int) -> None:
        self._step = max(1, int(sample_rate * MOUTH_STEP))
        self._pending = array("h")  # ponytail: assumes little-endian host

    def feed(self, pcm: bytes) -> list[float]:
        """The values for the steps this chunk completes."""
        self._pending.extend(array("h", pcm[: len(pcm) // 2 * 2]))
        values = []
        while len(self._pending) >= self._step:
            values.append(_openness(self._pending[: self._step]))
            del self._pending[: self._step]
        return values

    def flush(self) -> list[float]:
        """The last, short window: the audio ends between two steps."""
        if not self._pending:
            return []
        value = _openness(self._pending)
        self._pending = array("h")
        return [value]


def loudness(pcm: bytes, sample_rate: int) -> list[float]:
    """Mouth openness 0..1 for each MOUTH_STEP of a whole 16-bit mono PCM buffer."""
    mouth = Mouth(sample_rate)
    return [*mouth.feed(pcm), *mouth.flush()]


def with_mouth(audio, text: str = ""):
    """*audio* (a SpeechAudio or None) whose chunks also move the mouth and caption the line.

    Every value is one step of the audio, so the page, playing one value per
    step, ends the mouth with the voice.

    *text* is the sentence being spoken: it goes to the window as a subtitle when
    the player pulls the first chunk, and is cleared when the last one has been
    read. The clear therefore arrives a little before the ear reaches the end
    (chunks are read ahead of playback, as below), which is why the page holds a
    line for a minimum time rather than clearing it the instant the event lands.

    ponytail: chunks are read ahead of playback, so the mouth starts a little
    early; the page plays values at real speed, so it doesn't drift. Exact fix
    is a frame observer in zrb's PcmUtterance._fill.
    """
    if audio is None:
        return None
    chunks, mouth = audio.chunks, Mouth(audio.sample_rate)
    said = text.strip() if SUBTITLE else ""

    def moving():
        if said:
            send({"type": "subtitle", "text": said})
        for chunk in chunks:
            send({"type": "mouth", "values": mouth.feed(chunk), "step": MOUTH_STEP})
            yield chunk
        if tail := mouth.flush():
            send({"type": "mouth", "values": tail, "step": MOUTH_STEP})
        if said:
            send({"type": "subtitle", "clear": True})

    audio.chunks = moving()
    return audio


def _resumes_conversation(context) -> bool:
    """Whether this run carries earlier turns, so the face must not greet again.

    SessionStart fires on every run (each user turn, and each nested one), not once
    per conversation; the run passes its own "startup"/"resume" source, but the hook
    manager drops it, so the history in the event data is what tells them apart.
    """
    data = getattr(context, "event_data", None)
    return bool(isinstance(data, dict) and data.get("history"))


def face_hooks(hooks) -> None:
    """Register hooks that send idle/thinking/tool/attention, and zrb's emotions, to the face."""
    from zrb.llm.hook.interface import HookResult
    from zrb.llm.hook.types import HookEvent

    def to(state: str):
        async def hook(context):
            await asyncio.to_thread(send, {"type": "state", "value": state})
            if (
                context.event == HookEvent.SESSION_START
                and not _resumes_conversation(context)
                and await asyncio.to_thread(consents, "greeting")
            ):
                await asyncio.to_thread(send, {"type": "action", "value": "Greeting"})
            return HookResult()

        return hook

    for state, events in {
        "thinking": [
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.POST_TOOL_USE,
            HookEvent.POST_TOOL_USE_FAILURE,
        ],
        "tool": [HookEvent.PRE_TOOL_USE],
        "attention": [HookEvent.NOTIFICATION, HookEvent.PERMISSION_REQUEST],
        "idle": [HookEvent.STOP, HookEvent.STOP_FAILURE, HookEvent.SESSION_START],
    }.items():
        hooks.add_hook(to(state), events=events)

    # The approval wait has its own end. Without this, the "attention" state that
    # PermissionRequest sets stays until the tool finishes — PostToolUse fires at execution, not
    # approval — so she holds the asking-for-permission posture for the whole call. Older zrb has
    # no such event, and then nothing here changes.
    resolved = getattr(HookEvent, "PERMISSION_RESOLVED", None)
    if resolved is not None:

        async def answered(context):
            data = getattr(context, "event_data", None)
            approved = data.get("approved") if isinstance(data, dict) else None
            if approved is None:  # nobody answered: whatever happens next sets the state
                return HookResult()
            await asyncio.to_thread(
                send, {"type": "state", "value": "tool" if approved else "thinking"}
            )
            return HookResult()

        hooks.add_hook(answered, events=[resolved])

    # zrb's Emotion hook (ZRB_LLM_EMOTION_ENABLED): a VRM preset name, from the user's
    # prompt or zrb's reply. Older zrb has no such event.
    emotion = getattr(HookEvent, "EMOTION", None)
    if emotion is not None:

        async def feel(context):
            message = {
                "type": "emotion",
                "value": context.emotion,
                "source": context.emotion_source,
            }
            await asyncio.to_thread(send, message)
            return HookResult()

        hooks.add_hook(feel, events=[emotion])
