"""Audio between a page and whatever this machine runs: four routes, a reader for each direction.

Once a page is in use it is the only microphone and the only speaker, so this module is the
hand-over and nothing more. It knows sample rates, how much to hold and that a slow reader loses
the past rather than the present; it knows nothing about speech, transcription or a model.

Two directions, named from the page's side, as the board's plan names them:

* **in** — `POST /audio` takes the page's microphone, `GET /audio/in` hands it to zrb's dictation.
  One format, 16 kHz mono 16-bit, because that is what speech-to-text wants and what the page can
  be asked to make. A request in any other format is refused rather than transcribed as noise.
* **out** — `POST /say` takes what zrb's speech player made, `GET /audio/out` hands it to the page.
  The rate is the backend's own, so it is declared with each sentence and read out of the stream's
  headers. A sentence at a rate the open stream is not at opens a new stream; the page connects
  again and reads the new rate. Raw PCM has nowhere to put a rate mid-stream, and pretending
  otherwise would play a sentence at the wrong pitch instead of one frame late.

Each direction has one reader, and a new one replaces the last (`start_reading`). Both queues are
bounded, and a full one drops its oldest block. A page that stalls, or a zrb whose reader has gone,
must not grow this process a second at a time; what is lost is audio already too old to be spoken
over.
"""

import queue
import threading

# What dictation wants: 16-bit mono at 16 kHz is what every speech-to-text backend zrb reaches
# takes, and a page resampling to it is one line of AudioWorklet.
INBOUND_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1

# The rate a page assumes when nothing has been said yet on the machine it joined. Gemini renders
# at 24 kHz and most Pipecat services follow their voice; this is only the header a page reads
# before the first sentence, so it has to be a rate zrb might well use next.
DEFAULT_OUTBOUND_RATE = 24000

# Sample rates speech can have. Outside them is a broken or hostile header, and the page would be
# asked to configure an AudioContext that cannot exist (zrb holds its own to these bounds).
MIN_RATE = 1000
MAX_RATE = 384000

# How much audio a queue holds before it starts dropping. Two seconds is past every reader's
# latency by an order of magnitude, so a full queue is a reader that has stopped.
KEEP_SECONDS = 2.0

# One frame, for the silence a quiet queue is answered with: 20 ms is what a page's ring buffer
# already works in, and silence is inaudible at any size.
FRAME_MILLISECONDS = 20

# How long a reader waits on its queue before writing that silence. It doubles as the notice that
# the far end has gone: raw PCM has no comment syntax, so nothing else can be sent to find out.
PING_SECONDS = 15.0

# The most one measured request body may be. A chunked body is a microphone and has no limit; a
# measured one is a fallback for a browser that will not stream, and 32 seconds of this format is
# already more than a fallback frame should be.
MAX_BODY_BYTES = 1_000_000


class Channel:
    """One direction of audio: blocks in, blocks out, and a cap that drops the oldest."""

    def __init__(self, keep_seconds: float = KEEP_SECONDS) -> None:
        # Counted in blocks rather than bytes: a block is whatever a reader last wrote, and every
        # writer here writes far less than a second of audio at a time.
        self._blocks: "queue.Queue[bytes]" = queue.Queue(
            maxsize=max(1, int(keep_seconds))
        )

    def put(self, pcm: bytes) -> None:
        """Add *pcm*, dropping the oldest block to make room when full.

        Dropping is the point: a reader that cannot keep up must not be handed a backlog it will
        spend the next sentence working through. What goes is audio from before now.
        """
        while True:
            try:
                self._blocks.put_nowait(pcm)
                return
            except queue.Full:
                try:
                    self._blocks.get_nowait()
                except queue.Empty:  # another thread drained it first; the put can be retried
                    pass

    def take(self, timeout: float) -> bytes | None:
        """The next block, waiting up to *timeout*; None when nothing came."""
        try:
            return self._blocks.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        """Throw away what is waiting, so the next reader starts at now."""
        while True:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                return

    def drain(self) -> list[bytes]:
        """Every block waiting, oldest first, leaving the channel empty."""
        blocks = []
        while True:
            try:
                blocks.append(self._blocks.get_nowait())
            except queue.Empty:
                return blocks


_channels: dict[str, Channel] = {"in": Channel(), "out": Channel()}
_channels_lock = threading.Lock()
# The rate the last sentence was made at, and the lock that makes reading and writing it one step.
# None until zrb's speech has said something on this run.
_outbound_format: dict[str, int | None] = {"rate": None}


def start_reading(direction: str) -> Channel:
    """Become the only reader of *direction* (``"in"`` or ``"out"``), replacing whoever was.

    Two readers would halve the audio between them and neither would transcribe a sentence — and a
    page that reloaded would otherwise leave a thread behind that takes the next block and writes it
    to a socket nobody is reading. The reader is handed a fresh channel, and writers always reach
    whichever channel is current, so a reader that was replaced has nothing left to take and ends on
    its own timeout.
    """
    with _channels_lock:
        return _swap_channel(direction)


def start_reading_outbound() -> tuple["Channel", int]:
    """Become the only reader of her speech: the channel, and the rate it is being made at.

    The two come out together because the page learns its rate from the response's headers. A
    sentence at another rate rotates the channel under `put_outbound`, so a reader that read the
    rate and took the channel in two steps could name one rate over the blocks of the other -- and
    the page would play the sentence at the wrong pitch. One lock, so the pair always describes the
    same stream; a rotation after this returns ends this stream rather than changing its rate.
    """
    with _channels_lock:
        channel = _swap_channel("out")
        return channel, _outbound_format["rate"] or DEFAULT_OUTBOUND_RATE


def _swap_channel(direction: str) -> "Channel":
    """A fresh channel in place of the one there; call with `_channels_lock` held.

    What was waiting is carried over rather than dropped: a page whose stream just ended re-asks at
    once, and the sentence already made for it must not go with the reader that left. It is one
    hand-over inside one lock, so nothing can be written into the channel being replaced between
    reading it and swapping it.
    """
    previous = _channels[direction]
    channel = Channel()
    for block in previous.drain():
        channel.put(block)
    _channels[direction] = channel
    return channel


def put_inbound(pcm: bytes) -> None:
    """One block of the page's microphone, for zrb's dictation to read."""
    if not pcm:
        return
    with _channels_lock:  # held across the put: a hand-over must not lose this block
        _channels["in"].put(pcm)


def take_inbound(timeout: float) -> bytes | None:
    """The next block of the page's microphone, or None when nothing came in *timeout*."""
    return _current_channel("in").take(timeout)


def put_outbound(pcm: bytes, rate: int) -> None:
    """One block of what zrb made, and the rate it was made at, for the page to play.

    A new rate opens a new stream. The page learns its rate from the response's headers, so a
    sentence at another rate cannot go into the stream already open: that stream ends, the page asks
    again, and reads the rate with it. Rotating here rather than as the stream writes is what keeps
    the first block of the new sentence — a writer that noticed the change on its way out would have
    to throw that block away, or play it at the old rate's pitch.
    """
    with _channels_lock:  # held across the put, as `put_inbound`'s is
        if _outbound_format["rate"] not in (None, rate):
            # A new stream, and the old backlog goes with the old rate: those blocks would play at
            # this sentence's pitch if they were carried over.
            _channels["out"] = Channel()
        _outbound_format["rate"] = rate
        if pcm:
            _channels["out"].put(pcm)


def take_outbound(timeout: float) -> bytes | None:
    """The next block of zrb's speech, or None when nothing came in *timeout*."""
    return _current_channel("out").take(timeout)


def outbound_rate() -> int | None:
    """The rate the last sentence was made at, or None if nothing has been said yet."""
    with _channels_lock:
        return _outbound_format["rate"]


def _current_channel(direction: str) -> Channel:
    with _channels_lock:
        return _channels[direction]


def silence(rate: int) -> bytes:
    """One frame of silence at *rate*, as 16-bit mono samples.

    The ping a quiet queue is answered with: nothing is heard, and a page that has closed its tab
    is found by the write failing rather than never being looked for.
    """
    return b"\x00" * (rate * SAMPLE_WIDTH * CHANNELS * FRAME_MILLISECONDS // 1000)


def is_supported_rate(rate: int) -> bool:
    """Whether *rate* is a sample rate audio can be at."""
    return MIN_RATE <= rate <= MAX_RATE
