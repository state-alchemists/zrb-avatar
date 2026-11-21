// The page: loads the model and its clips, wires the server's events to her, and draws her. A
// new model (AvatarModel, `zrb-avatar model`) takes her place in this page: no reload, no download.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { VRMAnimationLoaderPlugin } from '@pixiv/three-vrm-animation';
import { loadAnimations, clipsFor, clipByName } from './clips.js';
import { createMotion } from './motion.js';
import { measureReach, createFraming } from './framing.js';
import { createExpression } from './expression.js';
import { createView } from './view.js';
import { createBlush } from './blush.js';
import { createPose } from './pose.js';
import { createDesktop } from './desktop.js';
import { createAudio } from './audio.js';
import { createCamera } from './camera.js';
import { createTyped } from './typed.js';

// state -> glow colour
const LOOK = { idle: '#33ccff88', thinking: '#a6f', tool: '#3f9', attention: '#f46', permission: '#f22', speaking: '#6ef' };
const params = new URLSearchParams(location.search);
// Frames drawn per second; her motion is smooth at 30, and drawing costs the most CPU and GPU.
const FPS = Number(params.get('fps')) || 30;
if (params.has('app')) document.documentElement.classList.add('app');
const statusEl = document.getElementById('status');
let frame = () => {}; // set once the model is loaded
let renderer; // made below; declared here so giveUp can reach it, even when making it threw

// What the page says when it cannot work: a GPU it cannot draw with, or a model it cannot load.
// "loading…" is the line's own text, and a page that never replaces it says nothing at all: a window
// black under a camera name looks like a page that is working, and leaves a reader holding nothing
// to report. Retried twice, then left saying so — a reload that fails again is a loop, not a
// recovery.
//
// Three counters, because the three failures recover differently. Failing to start proves the GPU is
// gone, so a load that succeeds clears it. A context lost under a page that was drawing is a page
// that already loaded, so clearing that count on the next load would let a driver resetting every
// minute reload for ever; it clears only once the page has held a frame for a minute. A model that
// would not load is cleared by the next one that does.
const BOOT_FAILURES = 'zrb-avatar-boot-failures';
const CONTEXT_FAILURES = 'zrb-avatar-context-failures';
const LOAD_FAILURES = 'zrb-avatar-load-failures';
const STABLE_MS = 60000; // a page that has drawn this long has a GPU again
const RETRY_MS = 3000; // how long the reason stays readable before the page tries again
// Set once the page has given up: the message is then the whole of what this page can say, and a
// state, camera or connection event arriving afterwards must not paint over it — a black window with
// "idle" under it looks like a page that is working.
let failure = null;
let loading = null; // the model on its way, if one is: while it is, the line is the load's
// The model that would not load, while the line is still saying so. A failed swap has already cleared
// `loading` by the time it says why, so without this the next state event writes "idle" over the
// reason, and a page that could not change her looks like one that did.
let loadError = null;
let events = null; // the server's, see connect(); declared here because giveUp is called before connect() runs
function setStatusLine(text, isError = false) {
  if (failure) return;
  if (text !== loadError) loadError = null; // another writer has taken the line
  statusEl.textContent = text;
  statusEl.classList.toggle('error', !!isError);
}
function reasonOf(error) { return error?.message || String(error); }
// The page gives up: nothing more will be drawn, and the line keeps the reason. *said* is the whole
// sentence, because what could not be done differs — a GPU that will not start, a model that will
// not load — and the reader needs that difference more than the reader needs tidiness.
function giveUp(said, failuresIn) {
  // Latched at once, not when the retries run out: from here the line is the failure, and a state,
  // camera or connection event arriving in the seconds before the reload must not paint over it —
  // a black window reading "idle" looks like a page that works.
  failure = said;
  frame = () => {}; // nothing to draw into
  renderer?.setAnimationLoop(null); // nor a loop to draw with: this is the one that kept rendering
  events?.close(); // nor events to replace the line with her state
  const failures = Number(sessionStorage.getItem(failuresIn) || '0');
  const retry = failures < 2;
  statusEl.textContent = `${said}${retry ? ' — reloading' : ' — reload the page to try again'}`;
  statusEl.classList.add('error');
  if (!retry) return;
  sessionStorage.setItem(failuresIn, String(failures + 1));
  setTimeout(() => location.reload(), RETRY_MS);
}

try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
} catch (error) {
  // Coming back from sleep can leave the GPU process gone, and the throw lands where nothing listens.
  giveUp(`can't draw her: ${error?.message || 'no WebGL context'}`, BOOT_FAILURES);
  throw error;
}
document.body.prepend(renderer.domElement);
// A context is lost when the GPU goes away under a page that was already drawing: the page keeps
// running, the canvas goes black, and nothing on the status line changes. It cannot be revived in
// place, so the whole recovery is a reload — the model, the camera names and her mood are rebuilt
// on the way up. Without preventDefault the browser will not let a context come back at all.
renderer.domElement.addEventListener('webglcontextlost', event => {
  event.preventDefault();
  giveUp("can't draw her: the graphics context was lost", CONTEXT_FAILURES);
});
const desktop = createDesktop(renderer, window.faceApp); // the app's click-through and dragging
const scene = new THREE.Scene();
const light = new THREE.DirectionalLight(0xffffff, Math.PI);
light.position.set(0, 1, 1);
scene.add(light);
const camera = new THREE.PerspectiveCamera(20, 1, 0.1, 20);

function resize() {
  // again on every resize: page zoom and moving to another display change devicePixelRatio, and a
  // stale one draws her blurry, stretched. Sharper than 2x is invisible at this size.
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  frame();
}
addEventListener('resize', resize);
resize();

const loader = new GLTFLoader();
loader.register(parser => new VRMLoaderPlugin(parser));
loader.register(parser => new VRMAnimationLoaderPlugin(parser));
const animations = loadAnimations(loader); // once, for every model this page shows

// Her model is megabytes, and a phone across a room spends a minute on it: a page that says nothing
// for that minute cannot be told from one that has given up, which is the whole reason the line
// counts while it can. No `cache: 'no-store'` here -- a reload of an 18 MB model should come off the
// browser's own cache, not off the link.
const MB = 1024 * 1024;
const inMB = bytes => (bytes / MB).toFixed(1);
const STALL_MS = 15000; // no bytes for this long is a stall, not a slow line
async function fetchBytes(url, say) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText || 'refused'}`);
  const total = Number(res.headers.get('Content-Length')) || 0;
  if (!res.body) return res.arrayBuffer(); // no streaming here: one piece, however long it takes
  const pieces = [], reader = res.body.getReader();
  let got = 0, timer, timedOut = false;
  // A download that stops moving does not end by itself: the fetch sits there, no error reaches the
  // page, and the line keeps a number that will never change. That is the one failure a reader cannot
  // tell from a slow link — measured on a phone, 2026-11-21: the size arrived, not one byte did, and
  // the page waited. So the wire is timed here rather than trusted to the browser.
  //
  // The timer cancels the reader and *throws after the loop*, rather than racing a rejection against
  // `read()`: a cancel makes `read()` resolve `done`, which settles a race first and hands the parse
  // a truncated file — the reader then blames the file for what the link did.
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      timedOut = true;
      reader.cancel().catch(() => {}); // this is what wakes the pending read below
    }, STALL_MS);
  };
  say(0, total); // the size the moment it is known: "0.0 of 18.0" is a page that has begun
  arm();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      arm(); // every piece that arrives is the link saying it is still there
      if (done) break;
      pieces.push(value);
      say(got += value.byteLength, total);
    }
  } finally {
    clearTimeout(timer);
  }
  if (timedOut) throw new Error(`the download stalled at ${inMB(got)}${total ? ` of ${inMB(total)}` : ''} MB`);
  // One buffer, in the order the pieces came, which is the order a body is delivered in.
  const bytes = new Uint8Array(got);
  let at = 0;
  for (const piece of pieces) { bytes.set(piece, at); at += piece.byteLength; }
  return bytes.buffer;
}

// <name>.vrm, downloaded and parsed, the line naming which of the two it is doing. A failure is
// thrown with its reason rather than swallowed: a reader needs to know whether the file never
// arrived, or arrived and could not be drawn, because only one of those is about the network.
async function loadVRM(name) {
  const bytes = await fetchBytes(`${encodeURIComponent(name)}.vrm`,
    (got, total) => setStatusLine(`loading ${name}: ${inMB(got)}${total ? ` of ${inMB(total)}` : ''} MB`));
  setStatusLine(`loading ${name}: drawing her…`);
  const vrm = (await loader.parseAsync(bytes, '')).userData.vrm;
  if (!vrm) throw new Error('it is not a VRM');
  return vrm;
}

// Her notes, beside her model: {"covers": ["chest", ...]} says what her outfit covers. A model
// without one (a 404) covers nothing, which is what this page assumed before.
async function loadCovers(name) {
  const notes = await fetch(`${encodeURIComponent(name)}.json`)
    .then(r => (r.ok ? r.json() : null)).catch(() => null);
  return Array.isArray(notes?.covers) ? notes.covers.filter(c => typeof c === 'string') : [];
}

// This page's URL with params as they are now; ?app stays ?app, not ?app=.
function withParams() { return `?${params}`.replace(/=(?=&|$)/g, ''); }

// Her mood outlives any one model: the state zrb is in, music, whether she is being ogled, and
// what she agreed to do on her own (see the autonomy event, and GET /autonomy).
let state = 'idle', music = false, shy = false;
let autonomy = { dance: false, blush: true, emotion: true, greeting: true }; // the server's defaults: only dance starts off, so a talk video does not make her dance
let her = null; // the model on screen and everything bound to it, see show()
// Which view this page is up to. The server takes the newer of two events rather than the one that
// happened to arrive last, and `run` says a reloaded page outranks everything the last one said.
const viewRun = Math.random().toString(36).slice(2);
let viewSeq = 0;
// This page's own name, which is the run above: a reload is a new page with a new run, and the
// server keeps each page's camera names by it, so a photo is asked of the page that has the camera
// rather than of every page in the room.
const pageId = viewRun;
const followState = () => her?.motion.want(state === 'idle' && music && autonomy.dance ? 'dance' : state);
function setState(value) {
  if (value === 'idle' && (state === 'thinking' || state === 'tool')) her?.expression.smileSoon(); // done: a smile
  state = LOOK[value] ? value : 'idle';
  document.documentElement.style.setProperty('--glow', LOOK[state]);
  if (!loading && !loadError) setStatusLine(state); // a load in flight, or one that failed, owns the line
  followState();
}

// Her ears and mouth on this page: a microphone zrb hears, and a speaker her voice comes out of. Kept
// outside `her` because the audio outlives any one model and has to survive the swap, and opened by
// nothing on load — a browser refuses a microphone and a sound before a gesture, and a refusal reads
// as a broken page rather than as a browser doing its job. The key below is the gesture.
const micKey = document.getElementById('mic'); // the hint's own `m`, which says what it will do next
const cameraKey = document.getElementById('camera'); // and its `c`, which says what it already did
const audio = createAudio({
  // Said on the status line, where a model that will not load says its piece: a microphone the
  // browser refused has to look like a refusal, not like a switch that does nothing. And the key in
  // the hint changes with it — `m talk and listen`, then `m stop` — because a press that leaves
  // nothing looking different is a page that seems not to have heard the press.
  onState: ({ mic, error }) => {
    setStatusLine(error || state, !!error);
    if (micKey) micKey.textContent = error ? 'refused' : mic ? 'stop' : 'talk and listen';
  },
});
window.audio = audio; // for a transcript page and for devtools, as window.face is

// The camera in this page, which is the phone's: zrb asks for one photo at a time (see camera.js).
// The names are published on the way up, without asking anything, so a page allowed a camera in an
// earlier visit is a camera zrb can name again after a reload.
const pageCamera = createCamera({
  page: pageId, // the name its report carries, so zrb can ask this page and not the other one
  onState: ({ names, error }) => {
    // The load is saying something more useful than a camera's name, and it says it on this line: a
    // name painted over the number that is still counting is what made a working page and a broken
    // one look the same. A refusal is not suppressed — that is news nobody else has.
    if (!error && loading) return;
    setStatusLine(error || (names.length ? `camera: ${names.join(', ')}` : state), !!error);
    // And the hint says what the press did. `c` gives the camera back on purpose — it is a grant, not
    // a stream, and a photo is taken only when zrb asks for one — so a label that still reads "allow
    // this camera" after it was allowed is the page hiding the half of the answer a hand cares about.
    if (cameraKey) {
      cameraKey.textContent = error ? 'refused'
        : names.length ? `${names.length} camera${names.length > 1 ? 's' : ''} ready`
        : 'allow this camera';
    }
  },
});
pageCamera.publish();
window.pageCamera = pageCamera;

// The text box: a phone's keyboard is the only one in the room, so a message typed here has to reach
// zrb the way a typed one in the terminal does. A line that will not send says so on the status line
// and goes back into the field (see typed.js) rather than disappearing.
const typedForm = document.getElementById('say');
const typedInput = document.getElementById('typed');
const typed = typedForm && typedInput ? createTyped(typedForm, typedInput, {
  onError: error => setStatusLine(error || state, !!error),
}) : null;
window.typed = typed;

// Loads <name>.vrm and puts her on screen, in place of whoever was there. A newer show() wins over
// an older one still loading, which frees the model it loaded for nothing. A load that cannot be
// made throws with its reason — the fetch, the parse, or a file that is not a VRM — because the
// caller has a line to put it on.
let showing = 0;
// The line belongs to the load until the load is done: the camera publishes its names on the way up
// and a state event can paint "idle" at any moment, and either one over a number that is still
// counting reads as a page that is working. Those two writers check `loading`; this is what holds it.
async function show(name) {
  const ticket = ++showing;
  loading = name;
  try {
    return await showModel(name, ticket);
  } finally {
    if (ticket === showing) loading = null;
  }
}
async function showModel(name, ticket) {
  const [vrm, covers] = await Promise.all([loadVRM(name), loadCovers(name)]);
  const stale = () => { VRMUtils.deepDispose(vrm.scene); return null; };
  if (ticket !== showing) return stale();
  VRMUtils.rotateVRM0(vrm);
  VRMUtils.removeUnnecessaryVertices(vrm.scene); // less memory and skinning work, same look
  VRMUtils.removeUnnecessaryJoints(vrm.scene);
  // These measure her as she stands now, in her rest pose, so before any clip moves her.
  const blush = createBlush(vrm), pose = createPose(vrm);
  const view = createView(vrm, camera, seen => {
    if (seen.shy && !shy && autonomy.blush) her?.expression.show('surprised'); // at once; zrb's reply may follow
    shy = seen.shy;
    her?.blush.set(shy && autonomy.blush);
    // For zrb (ZRB_AVATAR_TALK_FIRST=view): she may say something about it. Sent without awaiting the
    // last one, so each carries its own number: the server drops one that arrives after a newer.
    fetch('event', { method: 'POST', body: JSON.stringify({ type: 'view', ...seen, run: viewRun, seq: ++viewSeq }) }).catch(() => {});
  }, covers);
  const mixer = new THREE.AnimationMixer(vrm.scene);
  setStatusLine('loading her animations…'); // a stall here is a different stall from the one above
  const { clips, clipName } = clipsFor(await animations, vrm);
  if (ticket !== showing) return stale();
  const unmirrored = Object.values(clips).flatMap(Object.values).filter(c => !clipName.get(c).endsWith('-mirror'));
  const framing = createFraming(camera, measureReach(vrm, mixer, unmirrored),
    window.faceApp?.resize); // the desktop app's preload lets the page size its window

  // The swap: the old model out (its gaze target too), the new one in, her mood carried over.
  if (her) {
    her.view.dispose();
    scene.remove(her.vrm.scene);
    her.expression.dispose();
    her.mixer.stopAllAction();
    VRMUtils.deepDispose(her.vrm.scene);
  }
  scene.add(vrm.scene);
  her = { name, vrm, mixer, clips, clipName, blush, pose, view, framing,
    motion: createMotion(mixer, clips), expression: createExpression(vrm, camera, scene) };
  blush.set(shy && autonomy.blush);
  window.face = { vrm, scene, renderer, camera, mixer, clips, THREE }; // for inspecting from devtools
  frame = framing.frame;
  frame();
  params.set('model', name);
  history.replaceState(null, '', withParams());
  setState(state);
  // She is on screen: a start that worked is not a start that failed, and a model that loaded is not
  // a model that would not. The context count outlives this load on purpose — a page that reloaded
  // after a loss has to earn its clean slate by drawing.
  sessionStorage.removeItem(BOOT_FAILURES);
  sessionStorage.removeItem(LOAD_FAILURES);
  setTimeout(() => sessionStorage.removeItem(CONTEXT_FAILURES), STABLE_MS);
  connect(name);
  return her;
}

// What this window asks for (?model=<name>, from `zrb-avatar <name>`) outranks the remembered
// choice: an explicit argument is an instruction for this launch, the file is only a default.
// Then the model zrb last chose (AvatarModel, `zrb-avatar model`); else the first one in the folder.
const asked = params.get('model');
const chosen = await fetch('model').then(r => r.json()).then(m => m.chosen).catch(() => null);
const model = asked || chosen || (await fetch('models').then(r => r.json()).catch(() => []))[0];
try {
  if (!model) throw new Error('no .vrm in the models folder');
  await show(model);
} catch (error) {  // say so, even in the app, instead of showing nothing
  giveUp(`can't load ${model ? `${model}.vrm` : 'her'}: ${reasonOf(error)}`, LOAD_FAILURES);
  throw error; // nothing below works without her: a listener on a model that is not there is a throw a frame
}
// Typing a key is not pressing it: the text box takes its own keys, so a message with an `m` in it
// does not also start the microphone, and one with a `c` does not ask for the camera. Registered
// first, and in the capture phase, so it runs before the listeners below rather than after them.
addEventListener('keydown', e => {
  const target = e.target;
  if (target instanceof Element && target.closest('input, textarea')) e.stopImmediatePropagation();
}, true);
addEventListener('keydown', e => { her.framing.onKey(e); her.view.notice(); });
// M is the microphone, and with it her voice out of the speaker: one gesture is what a browser asks
// for before either, and speaking to her is also how you come to hear her answer. Off turns off both:
// a microphone that has stopped sending while her stream runs and her graph stays open is not off --
// it is a page holding this device's audio session to play silence into.
addEventListener('keydown', e => {
  if (e.key !== 'm' || e.repeat) return;
  if (audio.listening) return audio.stop();
  audio.startEars();
  audio.startMic();
});
// And both are let go when the page goes away, for the same reason: a graph and a request that only
// the page can end do not end themselves.
addEventListener('pagehide', () => audio.stop());
// C is the camera: the same one gesture a browser asks for, so the names it was keeping private
// become names zrb can ask for (`/photo phone-back`), and the status line says what they are.
addEventListener('keydown', e => {
  if (e.key !== 'c' || e.repeat) return;
  pageCamera.ask();
});
// A tap is the press a phone has instead of a key, so the two buttons in the hint bar carry the key
// they stand for and go through these same listeners. The tap is the gesture a browser asks for
// before a microphone or a camera — which is why nothing here needs a keyboard to be present.
for (const button of document.querySelectorAll('#keys [data-key]')) {
  button.addEventListener('click', () => dispatchEvent(new KeyboardEvent('keydown', { key: button.dataset.key })));
}
addEventListener('wheel', e => { e.preventDefault(); her.framing.onWheel(e); her.view.notice(); }, { passive: false }); // not the page's own zoom
// A phone has a finger instead of a wheel. A drag orbits her and a pinch resizes her, starting on the
// canvas alone: the keys and the box are elements above it, so a tap there is still a tap and never a
// drag. The canvas refuses the browser's own gestures (touch-action, in face.html), or the drag would
// scroll or zoom the page under her instead.
const canvasEl = renderer.domElement;
canvasEl.addEventListener('pointerdown', e => { her.framing.onPointerDown(e); her.view.notice(); });
addEventListener('pointermove', e => her.framing.onPointerMove(e)); // on the window: a finger that slides off her keeps the drag
addEventListener('pointerup', e => her.framing.onPointerUp(e));
addEventListener('pointercancel', e => her.framing.onPointerUp(e));

// What the user sees right now, for zrb's AvatarLook and `zrb-avatar snapshot`: this frame, from this
// camera, at most 768 px, on a dark backdrop where the app is see-through.
function snapshot(id) {
  renderer.render(scene, camera);
  const src = renderer.domElement, fit = Math.min(1, 768 / Math.max(src.width, src.height));
  const shot = Object.assign(document.createElement('canvas'), { width: Math.round(src.width * fit), height: Math.round(src.height * fit) });
  const g = shot.getContext('2d');
  g.fillStyle = '#1b1d24';
  g.fillRect(0, 0, shot.width, shot.height);
  g.drawImage(src, 0, 0, shot.width, shot.height);
  const about = { model: her.name, state, music, playing: her.clipName.get(her.motion.clip) ?? null,
    emotion: her.expression.emotion, speaking: her.expression.speaking, view: her.view.read() };
  fetch(`snapshot?id=${encodeURIComponent(id)}`, { method: 'POST', body: JSON.stringify({ image: shot.toDataURL('image/png'), about }) })
    .catch(() => {});
}

// The sentence she is saying, under her: the client sends {"text"} as the voice starts and
// {"clear"} as the last chunk is read — which is a little ahead of the ear, because chunks are
// read ahead of playback. A clear therefore does not end the line: it says the client is done
// sending, and the line goes when her mouth stops, which is the one clock on the page that runs
// with the voice rather than with the sentence's length. A line also has a minimum time, so a
// two-word one does not blink out, and a long stop, so a clear that never arrives (a crashed
// turn, a zrb killed mid-sentence) cannot leave it up for ever.
const subtitleEl = document.getElementById('subtitle');
let subtitleHold = 0, subtitleTimer = 0, statusText = '', sentenceUp = false, clearPending = false;
// The window may hang past an edge of the display — only her anchor is kept on it (app/main.js) —
// and a line centred on the window would lose its first words out there. Centre it on the part of
// the window that is on the screen instead, measured from the window's own place on the display.
function placeSubtitle() {
  const availLeft = screen.availLeft ?? 0;
  const availTop = screen.availTop ?? 0;
  const availWidth = screen.availWidth || innerWidth;
  const availHeight = screen.availHeight || innerHeight;
  const left = Math.max(0, availLeft - screenX);
  const right = Math.max(0, screenX + innerWidth - (availLeft + availWidth));
  const below = Math.max(0, screenY + innerHeight - (availTop + availHeight));
  const visible = Math.max(1, innerWidth - left - right);
  subtitleEl.style.left = `${Math.round(left + visible / 2)}px`;
  subtitleEl.style.maxWidth = `min(${Math.round(visible * 0.92)}px, 40em)`;
  subtitleEl.style.bottom = `${30 + Math.round(below)}px`;
}
addEventListener('resize', placeSubtitle);
function hideSubtitle() {
  clearTimeout(subtitleTimer);
  subtitleTimer = 0;
  sentenceUp = false;
  clearPending = false;
  if (statusText) return showStatus();  // the state's line takes the space rather than leaving it blank
  subtitleEl.classList.remove('on', 'status');
  subtitleEl.textContent = '';
}
// The mouth queue has drained: the voice has stopped. A clear that arrived while she was still
// speaking — the usual case, since the client reads ahead — is applied here instead of on the
// guess, which used to cut a slow sentence off a second or more before its last word.
function speechEnded() {
  if (!clearPending || her?.expression.speaking) return;
  clearPending = false;
  const left = subtitleHold - Date.now();  // the rest of the minimum time, not zero
  if (left > 200) subtitleTimer = setTimeout(hideSubtitle, left);
  else hideSubtitle();
}
// A state is not a sentence: it has no hold and no expiry, and it stays up until the state moves.
function showStatus() {
  clearTimeout(subtitleTimer);
  subtitleTimer = 0;
  subtitleEl.textContent = statusText;
  placeSubtitle();
  subtitleEl.classList.add('on', 'status');
}
function setStatus(text) {
  statusText = typeof text === 'string' ? text.trim() : '';
  if (sentenceUp) return;  // what she is saying wins until it goes
  if (statusText) showStatus(); else hideSubtitle();
}
function showSubtitle(msg) {
  if (msg.clear) {
    clearPending = true;
    return speechEnded();
  }
  const text = typeof msg.text === 'string' ? msg.text.trim() : '';
  if (!text) return hideSubtitle();
  subtitleEl.textContent = text;
  sentenceUp = true;
  clearPending = false;
  subtitleEl.classList.remove('status');
  if (typeof msg.size === 'number' && msg.size > 0) subtitleEl.style.setProperty('--subtitle-size', `${msg.size}px`);
  placeSubtitle();
  subtitleEl.classList.add('on');
  // Readable for about a second and a half plus a twentieth of a second a letter — the floor under
  // a short line, not its lifetime: the line ends with the voice, see speechEnded above.
  const hold = Math.max(1600, text.length * 55);
  subtitleHold = Date.now() + hold;
  clearTimeout(subtitleTimer);
  subtitleTimer = setTimeout(hideSubtitle, hold + 12000);
}

// A look she answered by covering herself (see the reflex in server.py): the clip made for what
// the look was at, else one that covers all of her, played once and held at its last frame until
// the look stops. Neither folder has anything in it until someone puts a clip there, and then she
// stays in the idle she was already in -- a cover is hers to own, not something to fake.
function coverWith(pool) {
  if (!her) return;
  if (!pool) return her.motion.release();
  const clip = clipByName(her.clips, pool) ?? (pool === 'cover/all' ? null : clipByName(her.clips, 'cover/all'));
  if (clip) her.motion.act(clip, true); else her.motion.release();
}

function onEvent({ data }) {
  const msg = JSON.parse(data);
  if (msg.type === 'state') {
    if (msg.value === 'thinking' && state !== 'tool') her.expression.hush(); // new prompt cuts speech
    setState(msg.value);
    // A client too old to send {"type":"status"} would leave its last line up for ever. A turn
    // ends in idle, so the caption goes with it even when no status message says so.
    if (msg.value === 'idle') setStatus('');
  } else if (msg.type === 'mouth') {
    her.expression.speak(msg.values, msg.step);
  } else if (msg.type === 'subtitle') {
    showSubtitle(msg);
  } else if (msg.type === 'status') {
    setStatus(msg.text);  // what she is doing, up whenever no sentence is
  } else if (msg.type === 'action') {
    // Resolved once: a name that is a pool picks one of its clips at random, and asking twice
    // could play a different clip than the one the check approved.
    const clip = clipByName(her.clips, msg.value);
    if (clip) her.motion.act(clip);
  } else if (msg.type === 'model' && typeof msg.value === 'string' && msg.value !== her.name) {
    // Another avatar: in her place, once we know it is there (a bad name changes nothing). A name
    // that is there and will not load says so and keeps her: this page is still working.
    fetch('models').then(r => r.json())
      .then(names => {
        if (!names.includes(msg.value)) return;
        show(msg.value).catch(error => {
          loadError = `can't load ${msg.value}.vrm: ${reasonOf(error)}`; // and setState leaves it alone
          setStatusLine(loadError, true);
        });
      })
      .catch(() => {});
  } else if (msg.type === 'pose') {
    msg.release ? her.pose.release() : her.pose.set(msg);
  } else if (msg.type === 'cover') {
    coverWith(msg.pool);
  } else if (msg.type === 'blink') {
    her.expression.blinkNow(msg.eyes, msg.seconds); // one she chose: a wink, or a slow one
  } else if (msg.type === 'music') {
    music = !!msg.value;
    followState();
  } else if (msg.type === 'snapshot') {
    snapshot(msg.id);
  } else if (msg.type === 'frame') {
    // One photo from this page's camera, by the name zrb asked for: the phone in your hand, on its
    // way to /photo. Answered by id, so a refusal reaches the ask that wanted it.
    pageCamera.answer(msg.id, msg.device);
  } else if (msg.type === 'autonomy') {
    // What she agreed to. Applied at once: a switch she just turned off should not wait for the
    // next clip, or for the reply that follows this one.
    autonomy = { ...autonomy, ...msg.value };
    her?.blush.set(shy && autonomy.blush);
    followState();
  } else if (msg.type === 'emotion' && msg.source !== 'user' && autonomy.emotion) {
    // Hers only: she answers the user's tone in her reply, which carries its own emotion.
    her.expression.show(msg.value, msg.strength);
    if (her.clips.actions?.[msg.value] && !her.motion.acting) her.motion.act(clipByName(her.clips, msg.value));
  }
}
// The server's events, telling it which model we show (so a new model reconnects). Losing it
// means face.py restarted: reload to pick up new models, animations and code.
function connect(name) {
  events?.close();
  let lost = false;
  events = new EventSource(`events?model=${encodeURIComponent(name)}&page=${encodeURIComponent(pageId)}`);
  events.onmessage = onEvent;
  events.onerror = () => { lost = true; setStatusLine('disconnected'); };
  events.onopen = () => { if (lost) location.reload(); };
}

const clock = new THREE.Clock();
let dt = 0;
renderer.setAnimationLoop(() => {
  dt += clock.getDelta();
  if (dt < 0.9 / FPS) return; // the display may refresh faster; 0.9 so jitter never skips a frame
  const { vrm, mixer, expression, pose, blush } = her;
  if (expression.speaking && state === 'idle') setState('speaking');
  if (!expression.speaking && state === 'speaking') setState('idle');
  if (!expression.speaking) speechEnded(); // the voice just stopped: a pending clear takes effect here
  expression.pose(dt, state, () => { mixer.update(dt); pose.apply(dt); });
  vrm.update(dt);
  blush.update(dt);
  renderer.render(scene, camera);
  desktop.drawn();
  expression.undo();
  pose.undo();
  dt = 0;
});
