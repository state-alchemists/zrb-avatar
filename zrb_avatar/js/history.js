// The conversation on the page: a sheet that rises out of the foot, above the text box, so the newest line
// lands where the thumb already is and the model's face stays clear.
//
// Two things feed it and neither is zrb's history file. The server keeps the turns it was told about and
// hands the whole list to a page that connects late (`history`); after that each turn arrives as it happens
// (`message`). This file only decides how they look, so nothing here knows how zrb stores a session.
//
// Read-only on purpose: the box below is the one way in, and a second input inside the sheet would be a
// second thing to keep in step with zrb.

const section = document.getElementById('history');
const handle = document.getElementById('history-handle');
const log = document.getElementById('history-log');
const newer = document.getElementById('history-new');

let open = false;

// How close to the end counts as reading the end. A turn that lands while the user is further up is not
// worth taking the page out of their hands for: the sheet stays where it is and offers the way down.
const NEAR_THE_END = 48;

const turnCount = () => log.childElementCount;
const atTheEnd = () => log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_THE_END;

function toTheEnd() {
  log.scrollTop = log.scrollHeight;
  newer.hidden = true;
}

// A picture in a reply is fetched the moment it is painted, and that request tells whatever host it names
// that this page is open -- the viewer's address with it. So nothing here is fetched from a stranger: an
// image that is not already in the message (`data:`) becomes a link the reader may open on purpose, and a
// link carries no referrer either.
const ABSOLUTE = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

function reachNothingRemote(container) {
  for (const image of container.querySelectorAll('img')) {
    const source = image.getAttribute('src') || '';
    if (source.startsWith('data:')) {
      image.setAttribute('referrerpolicy', 'no-referrer');
    } else if (ABSOLUTE.test(source)) {
      const link = document.createElement('a');
      link.href = source;
      link.rel = 'noopener noreferrer';
      link.textContent = image.getAttribute('alt') || 'a picture, not loaded';
      image.replaceWith(link);
    }
  }
  for (const link of container.querySelectorAll('a')) {
    link.rel = 'noopener noreferrer';
  }
}

// Markdown, rendered by the pair zrb's own page uses. A renderer that did not load is not a page with no
// conversation: the words stand as they were written rather than vanishing.
function render(text) {
  const marked = globalThis.marked;
  const purify = globalThis.DOMPurify;
  if (marked && purify) {
    const clean = document.createElement('template');
    clean.innerHTML = purify.sanitize(
      marked.parse(text, { gfm: true, breaks: true, async: false })
    );
    reachNothingRemote(clean.content);
    return clean.innerHTML;
  }
  const escaped = text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  return `<p>${escaped}</p>`;
}

function turn(role, text) {
  const element = document.createElement('div');
  element.className = `turn ${role === 'user' ? 'you' : 'her'}`;
  const words = document.createElement('div');
  words.className = 'words';
  words.innerHTML = render(text); // sanitized above: these are the model's words and may hold anything
  element.append(words);
  return element;
}

function paintPill() {
  const turns = turnCount();
  handle.hidden = turns === 0;
  handle.textContent = open ? '⌄ close' : `▲ ${turns} ${turns === 1 ? 'message' : 'messages'}`;
}

function show(value) {
  open = value;
  section.classList.toggle('open', open);
  handle.setAttribute('aria-expanded', String(open));
  paintPill();
  if (open) toTheEnd();
}

export function toggleHistory() {
  show(!open);
}

// What was said before this page was opened: handed over once, and the sheet is then complete back to the
// start of the session rather than beginning at the moment of connection.
export function showHistory(messages) {
  log.replaceChildren();
  messages.forEach(message => log.append(turn(message.role, String(message.text ?? ''))));
  paintPill();
  if (open) toTheEnd();
}

export function addTurn(message) {
  const wasAtTheEnd = !open || atTheEnd();
  log.append(turn(message.role, String(message.text ?? '')));
  paintPill();
  if (!open) return;
  if (wasAtTheEnd) toTheEnd();
  else newer.hidden = false;
}

handle.addEventListener('click', () => show(!open));
newer.addEventListener('click', toTheEnd);
log.addEventListener('scroll', () => { if (atTheEnd()) newer.hidden = true; }, { passive: true });
// A key for the page that has one: `h` is the only letter left that is not already a gesture. The box's
// own keys never arrive here -- js/main.js stops them in the capture phase.
addEventListener('keydown', e => {
  if (e.key !== 'h' || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.documentElement.classList.contains('app')) return; // the window has no sheet
  toggleHistory();
});
