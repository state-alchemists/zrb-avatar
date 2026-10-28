// The page: loads the model and its clips, wires the server's events to her, and draws her. A
// new model (FaceModel, `zrb-avatar model`) takes her place in this page: no reload, no download.
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

// state -> glow colour
const LOOK = { idle: '#33ccff88', thinking: '#a6f', tool: '#3f9', attention: '#f46', speaking: '#6ef' };
const params = new URLSearchParams(location.search);
// Frames drawn per second; her motion is smooth at 30, and drawing costs the most CPU and GPU.
const FPS = Number(params.get('fps')) || 30;
if (params.has('app')) document.documentElement.classList.add('app');
const statusEl = document.getElementById('status');

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
document.body.prepend(renderer.domElement);
const desktop = createDesktop(renderer, window.faceApp); // the app's click-through and dragging
const scene = new THREE.Scene();
const light = new THREE.DirectionalLight(0xffffff, Math.PI);
light.position.set(0, 1, 1);
scene.add(light);
const camera = new THREE.PerspectiveCamera(20, 1, 0.1, 20);

let frame = () => {}; // set once the model is loaded
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

// This page's URL with params as they are now; ?app stays ?app, not ?app=.
function withParams() { return `?${params}`.replace(/=(?=&|$)/g, ''); }

// Her mood outlives any one model: the state zrb is in, music, whether she is being ogled, and
// what she agreed to do on her own (see the autonomy event, and GET /autonomy).
let state = 'idle', music = false, shy = false;
let autonomy = { dance: false, blush: true, emotion: true, greeting: true }; // the server's defaults: only dance starts off, so a talk video does not make her dance
let her = null; // the model on screen and everything bound to it, see show()
let events = null; // the server's, see connect()
// Which view this page is up to. The server takes the newer of two events rather than the one that
// happened to arrive last, and `run` says a reloaded page outranks everything the last one said.
const viewRun = Math.random().toString(36).slice(2);
let viewSeq = 0;
const followState = () => her?.motion.want(state === 'idle' && music && autonomy.dance ? 'dance' : state);
function setState(value) {
  if (value === 'idle' && (state === 'thinking' || state === 'tool')) her?.expression.smileSoon(); // done: a smile
  state = LOOK[value] ? value : 'idle';
  document.documentElement.style.setProperty('--glow', LOOK[state]);
  statusEl.textContent = state;
  followState();
}

// Loads <name>.vrm and puts her on screen, in place of whoever was there. Null if it can't be
// loaded (the one on screen stays); a newer show() wins over an older one still loading, which
// frees the model it loaded for nothing.
let showing = 0;
async function show(name) {
  const ticket = ++showing;
  const [vrm, covers] = await Promise.all([
    loader.loadAsync(`${encodeURIComponent(name)}.vrm`).then(g => g.userData.vrm).catch(() => null),
    // Her notes, beside her model: {"covers": ["chest", ...]} says what her outfit covers. A
    // model without one (a 404) covers nothing, which is what this page assumed before.
    fetch(`${encodeURIComponent(name)}.json`).then(r => (r.ok ? r.json() : null))
      .then(notes => Array.isArray(notes?.covers) ? notes.covers.filter(c => typeof c === 'string') : [])
      .catch(() => []),
  ]);
  const stale = () => { VRMUtils.deepDispose(vrm.scene); return null; };
  if (!vrm) return null;
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
  connect(name);
  return her;
}

// What this window asks for (?model=<name>, from `zrb-avatar <name>`) outranks the remembered
// choice: an explicit argument is an instruction for this launch, the file is only a default.
// Then the model zrb last chose (FaceModel, `zrb-avatar model`); else the first one in the folder.
const asked = params.get('model');
const chosen = await fetch('model').then(r => r.json()).then(m => m.chosen).catch(() => null);
const model = asked || chosen || (await fetch('models').then(r => r.json()).catch(() => []))[0];
if (!model || !await show(model)) {  // say so, even in the app, instead of showing nothing
  statusEl.textContent = model ? `can't load ${model}.vrm from the models folder` : 'no .vrm in the models folder';
  statusEl.classList.add('error');
  setTimeout(() => location.reload(), 5000);  // try again: the file or the server may be fixed meanwhile
  throw new Error(statusEl.textContent);
}
addEventListener('keydown', e => { her.framing.onKey(e); her.view.notice(); });
addEventListener('wheel', e => { e.preventDefault(); her.framing.onWheel(e); her.view.notice(); }, { passive: false }); // not the page's own zoom

// What the user sees right now, for zrb's FaceLook and `zrb-avatar snapshot`: this frame, from this
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

function onEvent({ data }) {
  const msg = JSON.parse(data);
  if (msg.type === 'state') {
    if (msg.value === 'thinking' && state !== 'tool') her.expression.hush(); // new prompt cuts speech
    setState(msg.value);
  } else if (msg.type === 'mouth') {
    her.expression.speak(msg.values, msg.step);
  } else if (msg.type === 'action' && clipByName(her.clips, msg.value)) {
    her.motion.act(clipByName(her.clips, msg.value));
  } else if (msg.type === 'model' && typeof msg.value === 'string' && msg.value !== her.name) {
    // Another avatar: in her place, once we know it is there (a bad name changes nothing).
    fetch('models').then(r => r.json()).then(names => { if (names.includes(msg.value)) show(msg.value); }).catch(() => {});
  } else if (msg.type === 'pose') {
    msg.release ? her.pose.release() : her.pose.set(msg);
  } else if (msg.type === 'music') {
    music = !!msg.value;
    followState();
  } else if (msg.type === 'snapshot') {
    snapshot(msg.id);
  } else if (msg.type === 'autonomy') {
    // What she agreed to. Applied at once: a switch she just turned off should not wait for the
    // next clip, or for the reply that follows this one.
    autonomy = { ...autonomy, ...msg.value };
    her?.blush.set(shy && autonomy.blush);
    followState();
  } else if (msg.type === 'emotion' && msg.source !== 'user' && autonomy.emotion) {
    // Hers only: she answers the user's tone in her reply, which carries its own emotion.
    her.expression.show(msg.value);
    if (her.clips.actions?.[msg.value] && !her.motion.acting) her.motion.act(clipByName(her.clips, msg.value));
  }
}
// The server's events, telling it which model we show (so a new model reconnects). Losing it
// means face.py restarted: reload to pick up new models, animations and code.
function connect(name) {
  events?.close();
  let lost = false;
  events = new EventSource(`events?model=${encodeURIComponent(name)}`);
  events.onmessage = onEvent;
  events.onerror = () => { lost = true; statusEl.textContent = 'disconnected'; };
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
  expression.pose(dt, state, () => { mixer.update(dt); pose.apply(dt); });
  vrm.update(dt);
  blush.update(dt);
  renderer.render(scene, camera);
  desktop.drawn();
  expression.undo();
  pose.undo();
  dt = 0;
});
