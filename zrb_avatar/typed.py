"""Lines typed on the page, on their way to zrb as the user's own words.

A page that holds a microphone also holds a text box: the phone in your hand is the only keyboard
in the room, and what is typed there has to arrive as a turn the way a spoken one does.
`POST /text` takes one line, `GET /text/in` hands the lines on in the order they were typed.

The single-reader rule is the microphone's rule, for the same reason: two readers would take
different lines of the same message and neither would read it whole. The reader that has gone is
replaced rather than left holding the next line, and what was already typed goes to the reader that
replaced it -- a page that reloaded mid-question asks again at once.

One line is one message. A newline inside a line would be read as the end of it, so a paste that
carries one is joined with spaces: the frame is the line, and a message that quietly became two
turns is worse than a paragraph that became one.

A line is only taken while something is reading: the route counts the reader that holds it, and
`POST /text` refuses rather than queueing into a channel nobody drains. A line past `MAX_WAITING` is
refused the same way -- a page that is told its message arrived has lost something it cannot know.
"""

from __future__ import annotations

import collections
import threading

# The longest line a page may send, counted in characters rather than bytes so a line of accents
# counts as the line it is. Past this it is a file, and a file belongs in an attachment.
MAX_LINE_CHARS = 4000
# The most one measured request body may be: four bytes a character is the widest UTF-8 has.
MAX_BODY_BYTES = 4 * MAX_LINE_CHARS
# How long the route may go quiet before it writes a blank line. A proxy that ends an idle response
# would otherwise close the typing while the page is still open, and a route that ends is a reader
# that reconnects into the same silence. A blank line is not a line: neither side counts it.
KEEPALIVE_SECONDS = 15
# How many lines may wait for a reader that is not taking them. Past this the chat is not there,
# and a page typing into nothing is a backlog nobody will read.
MAX_WAITING = 32

_lock = threading.Lock()
# Whether a reader holds the route right now. A page that types into nothing has to be told so rather
# than watch its line join a queue nobody will drain.
_reader = False


class ChannelFull(RuntimeError):
    """No reader is taking lines fast enough: ``MAX_WAITING`` of them are already waiting."""


class Typed:
    """The lines waiting for one reader, and whether its response should end.

    One condition guards both, so ending a channel wakes the read that is waiting on it: a reader
    that has been replaced ends its response at once rather than at the next keepalive, and a page
    that reloaded never leaves a thread asleep on a socket nobody will write to.
    """

    def __init__(self) -> None:
        self._lines: "collections.deque[str]" = collections.deque()
        self._changed = threading.Condition()
        self._ended = False

    @property
    def ended(self) -> bool:
        """Whether a newer reader has taken this one's place."""
        with self._changed:
            return self._ended

    def put(self, line: str) -> None:
        """*line*, for the reader holding this. A full channel refuses it rather than dropping it:
        a page that is told its line arrived and had it dropped has lost something it cannot know."""
        with self._changed:
            if len(self._lines) >= MAX_WAITING:
                raise ChannelFull(f"{MAX_WAITING} lines are already waiting for a reader")
            self._lines.append(line)
            self._changed.notify_all()

    def take(self, timeout: float) -> str:
        """The next line, or ``""`` when nothing arrived in *timeout* or the channel is over."""
        with self._changed:
            if not self._lines and not self._ended:
                self._changed.wait(timeout)
            return self._lines.popleft() if self._lines else ""

    def drain(self) -> "list[str]":
        """Every line waiting, oldest first, leaving this empty."""
        with self._changed:
            waiting = list(self._lines)
            self._lines.clear()
            return waiting

    def end(self) -> None:
        """Tell the reader holding this that its response is over, and wake it if it is waiting."""
        with self._changed:
            self._ended = True
            self._changed.notify_all()


_typed = Typed()


def put_line(line: str) -> bool:
    """One line typed on the page. False when it was nothing but space.

    Newlines are joined with spaces before the line is framed: one line is one message, and a
    message that arrived as two turns is worse than a paragraph that arrived as one. Raises
    ``ChannelFull`` when the lines already waiting fill the channel.
    """
    text = " ".join(line.split("\n")).strip()[:MAX_LINE_CHARS]
    if not text:
        return False
    with _lock:  # held across the put: a hand-over must not lose this line
        _typed.put(text)
    return True


def start_reading() -> Typed:
    """Become the only reader, replacing whoever was.

    The reader is handed a fresh channel and the one it replaced is ended, so a page that reloaded
    leaves no thread behind writing lines to a socket nobody is reading. What was waiting moves to
    the new channel: a question typed while zrb was between readers is still a question.
    """
    global _typed, _reader
    with _lock:
        previous, _typed = _typed, Typed()
        for line in previous.drain():
            _typed.put(line)
        previous.end()
        _reader = True
        return _typed


def stop_reading(channel: Typed) -> None:
    """The reader holding *channel* has gone.

    Only the channel that is current counts: a reader replaced a moment ago must not turn off the
    flag the one that replaced it set.
    """
    global _reader
    with _lock:
        if channel is _typed:
            _reader = False


def reader_attached() -> bool:
    """Whether a reader holds the route, and so whether a typed line has anywhere to go."""
    with _lock:
        return _reader
