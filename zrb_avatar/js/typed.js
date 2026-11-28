// The page's text box: a line typed here is a message to zrb, and it arrives as one.
//
// A phone's keyboard is the only one in the room, so this is how a hand without a terminal types.
// The line goes to POST /text, the face server hands it to zrb in the order it was typed, and it
// becomes a turn the way a typed line in the terminal does — nothing about a typed message is
// different for having come from a page. One line is one message: the box holds one, Enter sends it,
// and the field is empty again for the next thought. It is a textarea, so a long thought wraps
// instead of scrolling out of sight; Shift+Enter is the newline it would otherwise never give.

// The server's own cap (typed.MAX_LINE_CHARS), kept here too so a paste is cut in the field rather
// than refused after it was sent.
const MAX_CHARS = 4000;

export function createTyped(form, input, { onError } = {}) {
  let sending = false;
  let error = '';

  const report = () => onError?.(error);
  const fail = message => {
    error = message;
    report();
  };

  async function send() {
    const text = input.value.trim().slice(0, MAX_CHARS);
    if (!text || sending) return;
    sending = true;
    input.value = ''; // cleared on the way out: the field is for the next thought, not this one
    try {
      const response = await fetch('/text', {
        method: 'POST',
        body: text,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      });
      if (!response.ok) {
        // 503 has a reason a person can act on: nothing is reading the box yet, so the line is not lost,
        // it is unsent -- and Enter sends it again once zrb is reading. A status number is not a sentence.
        throw new Error(response.status === 503
          ? 'nothing is reading the box yet'
          : `the server said ${response.status}`);
      }
      error = '';
    } catch (e) {
      // The line goes back into the field rather than into nothing: a message that vanished
      // silently is worse than one that says it did not arrive, and the words are still there.
      input.value = text;
      fail(`can't send: ${e.message || e}`);
    } finally {
      sending = false;
      report();
      input.focus({ preventScroll: true }); // kept open: a second thought follows the first
    }
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    send();
  });
  // A textarea does not submit on Enter — it would give a newline — so the send is said here or it is
  // never said at all. Shift+Enter keeps the newline, and a composition is left alone: the Enter that
  // ends a Japanese or Chinese input is not a press to send.
  input.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send();
  });

  return { send, get error() { return error; } };
}
