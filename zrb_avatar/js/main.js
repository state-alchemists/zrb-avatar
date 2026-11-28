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
  // A load that failed keeps the line until a load works: the camera publishes its names on the way up
  // and a state event arrives at any moment, and either one would erase the reason while the old model
  // is still the one on screen. A later failure is the newest thing known, and takes the line.
  if (loadError && !isError) return;
  if (isError) loadError = text;
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
  giveUp(`can't draw the avatar: ${error?.message || 'no WebGL context'}`, BOOT_FAILURES);
  throw error;
}
document.body.prepend(renderer.domElement);
// A context is lost when the GPU goes away under a page that was already drawing: the page keeps
// running, the canvas goes black, and nothing on the status line changes. It cannot be revived in
// place, so the whole recovery is a reload — the model, the camera names and her mood are rebuilt
// on the way up. Without preventDefault the browser will not let a context come back at all.
renderer.domElement.addEventListener('webglcontextlost', event => {
  event.preventDefault();
  giveUp("can't draw the avatar: the graphics context was lost", CONTEXT_FAILURES);
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
  renderer.setSize(innerWidth, freeHeight());
  frame();
}
// The canvas is the part of the page the controls leave. Running it the whole height of a phone put the
// text box over her boots, and the box is fixed at the foot with nothing she is drawn into going under
// it: face.html measures that stack into --bottom-h, so the number is read here rather than guessed.
// How tall the canvas is: the whole window, with the row and the box floating over her boots rather
// than below the canvas. On Android the fixed stack sits at the visual bottom while `innerHeight`
// reports the taller layout viewport, so measuring "up to the stack" drew her into a canvas taller
// than the room she had -- a fault a desktop browser and Chrome's emulation both hide.
//
// A phone's keyboard is the other half of the same thing: it takes a third of the height, and a canvas
// that followed it drew her a third smaller for as long as the box was open. A page used to remember the
// tallest window seen at a width so that could not happen -- but no resize can then be told from the
// keyboard's, and a browser window made shorter kept a canvas of the old height, with her drawn below the
// bottom of the screen. The review of PR #34 found that twice, and found the remembered height still
// guessing wrong with the box in focus on 2026-11-28. So nothing is remembered: the canvas follows the
// window on both surfaces, she is a third smaller while an Android keyboard takes a third of the height
// and back when it closes, and on iOS she does not move at all, where the keyboard leaves innerHeight be.
function freeHeight() {
  return Math.max(120, innerHeight);
}
const viewport = () => [innerWidth, freeHeight()];
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
const STALL_TRIES = 6; // and each stall asks for the bytes that did not arrive, not the file again

// A phone that dims its screen stops the download with it — measured here on 2026-11-21: six of
// eighteen megabytes and then nothing, with no error the page could see. The screen is kept on while
// a model is on its way, and the tries below are what happens when it goes off anyway.
let wakeLock = null, keepsAwake = false;
async function askAwake() {
  if (!keepsAwake || document.visibilityState !== 'visible' || !navigator.wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
  } catch {
    wakeLock = null; // a browser that will not hold it is a browser whose page still loads
  }
}
// The system takes the lock away the moment the page is hidden, so it is asked for again on return.
async function wakeUp() {
  keepsAwake = true;
  addEventListener('visibilitychange', askAwake);
  await askAwake();
}
function sleepAgain() {
  keepsAwake = false;
  removeEventListener('visibilitychange', askAwake);
  const held = wakeLock;
  wakeLock = null;
  Promise.resolve(held?.release()).catch(() => {});
}
// The bytes of `url`, asking for the rest each time the link goes quiet. A stall is not an error:
// the fetch has not failed, it has stopped moving, and what already arrived is worth keeping.
async function fetchBytes(url, say) {
  const whole = { pieces: [], got: 0, total: 0 };
  for (let tries = 0; tries < STALL_TRIES; tries++) {
    if (await readAttempt(url, whole, say)) return joined(whole);
  }
  throw new Error(`the download stalled at ${inMB(whole.got)}${whole.total ? ` of ${inMB(whole.total)}` : ''} MB`);
}
// One go at the rest of the file, from what `whole` already holds. True when the body ended.
async function readAttempt(url, whole, say) {
  const from = whole.got;
  const res = await fetch(url, from ? { headers: { Range: `bytes=${from}-` } } : undefined);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText || 'refused'}`);
  // A server that cannot resume answers with the whole file, so this attempt starts over rather than
  // appending the beginning of the file to the end of the part already held.
  if (from && res.status !== 206) {
    whole.pieces = [];
    whole.got = 0;
  }
  const length = Number(res.headers.get('Content-Length')) || 0;
  whole.total = from && res.status === 206 ? from + length : length;
  say(whole.got, whole.total); // the size the moment it is known: "0.0 of 18.0" is a page that has begun
  if (!res.body) return onePiece(res, whole, say); // no streaming here: one piece, however long it takes
  return drain(res, whole, say);
}
// The body, piece by piece, ending when it ends or when it goes quiet.
//
// A download that stops moving does not end by itself: the fetch sits there, no error reaches the
// page, and the line keeps a number that will never change — measured on a phone, 2026-11-21: the
// size arrived, not one byte did, and the page waited. So the wire is timed here rather than trusted
// to the browser. The timer cancels the reader and the answer is read after the loop, rather than
// racing a rejection against `read()`: a cancel makes `read()` resolve `done`, which settles a race
// first and hands the parse a truncated file — the reader then blames the file for what the link did.
async function drain(res, whole, say) {
  const reader = res.body.getReader();
  let timer, quiet = false;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      quiet = true;
      reader.cancel().catch(() => {}); // this is what wakes the pending read below
    }, STALL_MS);
  };
  arm();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      arm(); // every piece that arrives is the link saying it is still there
      whole.pieces.push(value);
      say(whole.got += value.byteLength, whole.total);
    }
  } finally {
    clearTimeout(timer);
  }
  return !quiet && (!whole.total || whole.got >= whole.total);
}
async function onePiece(res, whole, say) {
  const piece = new Uint8Array(await res.arrayBuffer());
  whole.pieces.push(piece);
  say(whole.got += piece.byteLength, whole.total);
  return !whole.total || whole.got >= whole.total;
}
// One buffer, in the order the pieces came, which is the order a body is delivered in.
function joined({ pieces, got }) {
  const bytes = new Uint8Array(got);
  let at = 0;
  for (const piece of pieces) { bytes.set(piece, at); at += piece.byteLength; }
  return bytes.buffer;
}

// <name>.vrm, downloaded and parsed, the line naming which of the two it is doing. A failure is
// thrown with its reason rather than swallowed: a reader needs to know whether the file never
// arrived, or arrived and could not be drawn, because only one of those is about the network.
async function loadVRM(name) {
  await wakeUp(); // the screen goes off and the download goes with it; see wakeUp above
  try {
    const bytes = await fetchBytes(`${encodeURIComponent(name)}.vrm`,
      (got, total) => setStatusLine(`loading ${name}: ${inMB(got)}${total ? ` of ${inMB(total)}` : ''} MB`));
    setStatusLine(`loading ${name}: drawing…`);
    const vrm = (await loader.parseAsync(bytes, '')).userData.vrm;
    if (!vrm) throw new Error('it is not a VRM');
    return vrm;
  } finally {
    sleepAgain();
  }
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
  if (!loading && !loadError) { setStatusLine(state); sayState(); } // a load in flight, or one that failed, owns the line
  followState();
}

// Her ears and mouth on this page: a microphone zrb hears, and a speaker her voice comes out of. Kept
// outside `her` because the audio outlives any one model and has to survive the swap, and opened by
// nothing on load — a browser refuses a microphone and a sound before a gesture, and a refusal reads
// as a broken page rather than as a browser doing its job. The buttons below are the gesture.
const talkButton = document.getElementById('talk'); // the microphone without a keyboard, held open by a press
const talkLabel = document.getElementById('talk-label'); // its word, which the icon sits beside
const voiceButton = document.getElementById('voice'); // her voice, without a keyboard to press `m` for
const voiceLabel = document.getElementById('voice-label');
const voiceIcon = document.querySelector('#voice .icon'); // a speaker with a bar through it while off
const lensButton = document.getElementById('lens'); // the camera, for a hand with no `c` to press
const lensLabel = document.getElementById('lens-label');
const stateLabel = document.getElementById('state'); // what she is doing, in the window's own row
const controlsRow = document.getElementById('controls'); // that row, placed beside her feet
const audio = createAudio({
  // Said on the status line, where a model that will not load says its piece: a microphone the
  // browser refused has to look like a refusal, not like a switch that does nothing. And the key in
  // the hint changes with it — `m talk and listen`, then `m stop` — because a press that leaves
  // nothing looking different is a page that seems not to have heard the press.
  onState: ({ mic, ears, error }) => {
    setStatusLine(error || state, !!error);
    // A refusal names the direction it came from, and only that button wears it: a microphone the
    // browser refused must not read as a refused speaker, or the other way round.
    const micError = error?.startsWith('microphone') ? error : '';
    const earsError = error?.startsWith('speaker') ? error : '';
    // And each button says what its own press did: a press that leaves nothing looking different is a
    // page that seems not to have heard the press.
    if (talkButton) {
      talkButton.classList.toggle('on', !!mic);
      talkLabel.textContent = micError ? 'refused' : mic ? 'listening…' : 'hold to talk';
    }
    if (voiceButton) {
      voiceButton.classList.toggle('on', !!ears);
      voiceIcon.textContent = ears ? '🔊' : '🔇'; // the icon says it before the word does
      voiceLabel.textContent = earsError ? 'refused' : ears ? 'voice on' : 'voice off';
    }
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
    // And the window's own button says it in one word, on the row: a press that leaves it reading the
    // same thing is a press that looks like nothing happened. On is being askable -- a second press
    // takes the names back and it reads "camera off" again.
    if (lensButton) {
      lensLabel.textContent = error ? 'refused' : names.length ? 'camera on' : 'camera off';
      lensButton.classList.toggle('on', !error && names.length > 0);
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
    const shown = await showModel(name, ticket);
    // A load that worked replaces the reason a load did not, and the line goes back to her state.
    if (ticket === showing) {
      loadError = null;
      setStatusLine(state);
    }
    return shown;
  } catch (error) {
    // A loss only speaks for the load it came from: a newer load that has already put someone on
    // screen must not be described by the failure of the one it replaced.
    if (ticket === showing) {
      loadError = `can't load ${name}.vrm: ${reasonOf(error)}`; // and setState leaves it alone
      setStatusLine(loadError, true);
    }
    throw error;
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
  setStatusLine('loading the animations…'); // a stall here is a different stall from the one above
  const { clips, clipName } = clipsFor(await animations, vrm);
  if (ticket !== showing) return stale();
  const unmirrored = Object.values(clips).flatMap(Object.values).filter(c => !clipName.get(c).endsWith('-mirror'));
  const framing = createFraming(camera, measureReach(vrm, mixer, unmirrored),
    window.faceApp?.resize, // the desktop app's preload lets the page size its window
    viewport); // and the canvas is what she is fitted to: the window less the foot of the page

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
  giveUp(`can't load ${model ? `${model}.vrm` : 'the avatar'}: ${reasonOf(error)}`, LOAD_FAILURES);
  throw error; // nothing below works without her: a listener on a model that is not there is a throw a frame
}
// Typing a key is not pressing it: the text box takes its own keys, so a message with an `m` in it
// does not also start the microphone, and one with a `c` does not ask for the camera. Registered
// first, and in the capture phase, so it runs before the listeners below rather than after them.
//
// Enter is the exception, and it is the box's own key: a textarea does not submit a form on Enter, so the
// only thing that can send a typed line is the box's own handler -- and stopping the key here killed that
// handler, leaving a phone that types a message and sends nothing (review of PR #34, 2026-11-28).
addEventListener('keydown', e => {
  const target = e.target;
  if (!(target instanceof Element) || !target.closest('input, textarea')) return;
  if (e.key === 'Enter') return; // Enter sends, and Shift+Enter is the box's own newline
  e.stopImmediatePropagation();
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
// Hold to talk, for the hand that has no keyboard: a press is the gesture a browser wants, and letting
// go closes the microphone, so zrb hears only what was said on purpose. Her voice is the button beside
// it, not this one: a mute that the microphone quietly undid would be no mute at all.
if (talkButton) {
  // Whether the microphone that is open belongs to this press: `m` or the voice button may have opened
  // one that outlives it, and a window that lost the cursor mid-hold must not close a microphone nobody
  // let go of. So a press records whether it opened one, and only that press closes it.
  let holding = false, owned = false;
  const hold = () => {
    holding = true;
    owned = !audio.listening;
    audio.startMic();
  };
  const letGo = () => {
    if (!holding) return;
    holding = false;
    // `stopMic` cancels a start that is still on its way as well as one that is running: releasing the
    // button while the browser is still asking for the microphone used to leave that start to finish,
    // and the microphone stayed open with the button up. The press owns the start, so it ends it.
    if (owned) audio.stopMic();
    owned = false;
  };
  talkButton.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    talkButton.setPointerCapture(e.pointerId); // the release is heard even off the button
    hold();
  });
  talkButton.addEventListener('pointerup', letGo);
  talkButton.addEventListener('pointercancel', letGo);
  addEventListener('blur', letGo); // a press that lost the pointer stops what it opened, and nothing else
}
// And an app that passes clicks through must take the ones that land on a control: without this the
// talk button is a button the window sends to whatever is behind it.
desktop.watch(document.querySelectorAll('#controls button, #keys textarea'));
// C is the camera: the same one gesture a browser asks for, so the names it was keeping private
// become names zrb can ask for (`/photo phone-back`), and the status line says what they are.
addEventListener('keydown', e => {
  if (e.key !== 'c' || e.repeat) return;
  pageCamera.ask();
});
// And the same press without a keyboard, on the row the window keeps in sight: a window has no key to
// press, and a camera nobody can allow is a camera zrb never gets to ask for. A second press takes the
// names back, so this page stops being a camera zrb can ask for -- the key above only ever asks.
if (lensButton) {
  lensButton.addEventListener('click', () =>
    (pageCamera.names.length ? pageCamera.release() : pageCamera.ask()));
}
// Her voice has a button of its own, beside the microphone's rather than inside it: the press is the
// gesture a browser wants before any sound at all, and from then on her voice stays on until it is
// turned off. It does not follow the microphone, so "listening, and she is silent" is a state you can
// see and choose — a mute the next press quietly undid would be no mute.
if (voiceButton) {
  voiceButton.addEventListener('click', () => (audio.hearing ? audio.stopEars() : audio.startEars()));
}
// A tap on her is the page's, and the canvas refuses the browser's own gestures; the row of buttons is
// the gesture a browser asks for before a microphone, a sound or a camera.
addEventListener('wheel', e => { e.preventDefault(); her.framing.onWheel(e); her.view.notice(); }, { passive: false }); // not the page's own zoom
// A phone has a finger instead of a wheel. A drag orbits her and a pinch resizes her, starting on the
// canvas alone: the keys and the box are elements above it, so a tap there is still a tap and never a
// drag. The canvas refuses the browser's own gestures (touch-action, in face.html), or the drag would
// scroll or zoom the page under her instead.
//
// A move that changed the view is noted the way a key press is, and for the same reason: zrb is told
// the view once it settles, so a drag that is still going must push that moment back rather than let it
// pass at the angle the finger happened to be at a second and a half in.
const canvasEl = renderer.domElement;
// In the app a press on her is the window's: it moves her (desktop.js). The camera must not take the
// same press as a finger — both did, so dragging her across the screen orbited her as well, and at the
// screen's edge turned her with the cursor.
canvasEl.addEventListener('pointerdown', e => {
  if (desktop.takes(e)) return;
  her.framing.onPointerDown(e); her.view.notice();
});
addEventListener('pointermove', e => { // on the window: a finger that slides off her keeps the drag
  if (her.framing.onPointerMove(e)) her.view.notice();
});
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
// The part of the window the display actually shows: the window may hang past an edge of the screen,
// and anything placed on the part that is not there cannot be seen or pressed.
function shownRect() {
  const availLeft = screen.availLeft ?? 0, availTop = screen.availTop ?? 0;
  const availWidth = screen.availWidth || innerWidth, availHeight = screen.availHeight || innerHeight;
  return {
    left: Math.max(0, availLeft - screenX),
    top: Math.max(0, availTop - screenY),
    right: innerWidth - Math.max(0, screenX + innerWidth - (availLeft + availWidth)),
    bottom: innerHeight - Math.max(0, screenY + innerHeight - (availTop + availHeight)),
  };
}
function placeSubtitle() {
  const shown = shownRect();
  const visible = Math.max(1, shown.right - shown.left);
  subtitleEl.style.left = `${Math.round(shown.left + visible / 2)}px`;
  subtitleEl.style.maxWidth = `min(${Math.round(visible * 0.92)}px, 40em)`;
  // `shown.bottom` counts down from the top of the window; a CSS `bottom` counts up from its bottom
  // edge, so the inset is what is left. Read the other way the line went a window's height above the
  // top and was never seen at all -- the fault that hid the caption on 2026-11-28.
  //
  // In the window the row of buttons is placed beside the model's feet, and the line belongs under it
  // rather than over it (Go, 2026-11-28): the framing keeps air below the feet, and that air is where a
  // caption reads best. Where there is not enough of it, the line goes above the row, which is where it
  // was. On a page there is no such row: the line clears the foot stack instead.
  const inset = Math.max(0, innerHeight - shown.bottom);
  const foot = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bottom-h')) || 0;
  const row = controlsRow?.classList.contains('placed') ? controlsRow.getBoundingClientRect() : null;
  const height = subtitleEl.offsetHeight || 0;
  const underRow = row ? innerHeight - row.bottom - CONTROLS_GAP - height : -Infinity;
  const aboveRow = row ? Math.max(0, innerHeight - row.top) + 10 : 0; // its place before, kept as a fallback
  const aboveFoot = Math.max(30 + inset, foot + 12, aboveRow);
  // Four pixels of the line's own, so a caption never sits on the window's edge.
  subtitleEl.style.bottom = `${Math.round(Math.max(4, underRow >= 4 ? underRow : aboveFoot))}px`;
}
addEventListener('resize', placeSubtitle);
// And the window's row of controls is placed against her, not against the window: a see-through window
// has no corner of its own, and the part of it that is on the screen is not always the part she is on.
// Her stance is what is measured -- hips and both feet -- because a gesture swings her hands further
// than a row of buttons should follow, and the prism the framing fits reaches further still.
//
// The row is put down once and left there. It moves again only when she has moved further than a
// finger's width from where it was, or when the other side of her has clearly more room: a row that
// follows every sway is a row that slides under the pointer trying to press it.
const STANCE_BONES = ['hips', 'leftFoot', 'rightFoot'];
const CONTROLS_GAP = 8; // px between her and the row, and between the row and an edge of the screen
const DRIFT = 24; // px she may move before the row is put down again
const CROSS_ROOM = 64; // px more room the other side needs before the row crosses her
const PLACE_MS = 250; // she moves when the camera does, and a button does not need every frame
const herPoint = new THREE.Vector3();
let placedAt = 0;
let placed = null; // where the row was last put down, and on which side of her
function stance() {
  const h = her?.vrm.humanoid;
  if (!h) return null;
  const width = innerWidth, height = freeHeight();
  let left = Infinity, right = -Infinity, bottom = -Infinity;
  for (const name of STANCE_BONES) {
    const bone = h.getNormalizedBoneNode(name);
    if (!bone) continue;
    bone.getWorldPosition(herPoint).project(camera);
    const x = (herPoint.x + 1) / 2 * width, y = (1 - herPoint.y) / 2 * height;
    left = Math.min(left, x); right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  return Number.isFinite(left) ? { left, right, bottom } : null;
}
// The side of her with room, kept unless the other side is clearly roomier: an orbit that takes her
// past the middle of the window must not send the buttons hopping side to side.
function sideFor(room) {
  if (!placed) return room.right >= room.left ? 'right' : 'left';
  if (placed.side === 'right' && room.left > room.right + CROSS_ROOM) return 'left';
  if (placed.side === 'left' && room.right > room.left + CROSS_ROOM) return 'right';
  return placed.side;
}
function placeControls(force) {
  if (!controlsRow || !document.documentElement.classList.contains('app')) return;
  const now = Date.now();
  if (!force && now - placedAt < PLACE_MS) return;
  placedAt = now;
  const body = stance();
  if (!body) return; // still loading: the stylesheet's place holds until she is there
  const shown = shownRect();
  const rowWidth = controlsRow.offsetWidth, rowHeight = controlsRow.offsetHeight;
  const side = sideFor({ left: body.left - shown.left, right: shown.right - body.right });
  const x = side === 'right'
    ? Math.min(body.right + CONTROLS_GAP, shown.right - rowWidth - CONTROLS_GAP)
    : Math.max(body.left - CONTROLS_GAP - rowWidth, shown.left + CONTROLS_GAP);
  const y = Math.min(
    Math.max(body.bottom - rowHeight, shown.top + CONTROLS_GAP),
    shown.bottom - rowHeight - CONTROLS_GAP,
  );
  const settled = placed && placed.side === side
    && Math.abs(x - placed.x) <= DRIFT && Math.abs(y - placed.y) <= DRIFT;
  if (settled && !force) return; // close enough to where it is: leaving it still is the whole point
  placed = { x, y, side };
  controlsRow.style.left = `${Math.round(x)}px`;
  controlsRow.style.top = `${Math.round(y)}px`;
  controlsRow.classList.add('placed'); // from here the row is where this puts it, not in the flow
}
addEventListener('resize', () => placeControls(true));
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
// What the window's row says, written from one place: the status sentence while one is up, otherwise the
// state word, and nothing when she is idle -- a row that reads "idle" between turns is a label rather than
// news. A state event used to write only the page's line, which a window hides, so the window's row stayed
// empty through "thinking" and "working" and changed only when a status happened to arrive (the review of
// PR #34, 2026-11-28). A page hides the row and keeps its line, so this is free there.
function sayState() {
  if (failure || loadError) return; // the line says the failure, and a state event must not paint over it
  const said = statusText || (state === 'idle' ? '' : state);
  if (!stateLabel || stateLabel.textContent === said) return;
  stateLabel.textContent = said; // the window's row says it, beside her, always in sight
  placeControls(true); // the row is wider or narrower now, and may have to move to fit
}
function setStatus(text) {
  statusText = typeof text === 'string' ? text.trim() : '';
  sayState();
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
        show(msg.value).catch(() => {});  // show() has already said why, if it is still the load that counts
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
  placeControls(); // beside her feet, which move when the camera or the zoom does
  expression.undo();
  pose.undo();
  dt = 0;
});
