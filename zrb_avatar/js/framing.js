// The camera, and in the app the window size: she keeps her size on screen from every angle,
// and the window is just big enough for every clip's reach as seen from that angle.
import * as THREE from 'three';

const RIM = Array.from({ length: 32 }, (_, i) => i * Math.PI / 16); // directions around her
const MARGIN = 1.05;
const DEFAULT_VIEW = { yaw: 0, pitch: 0, zoom: 1, focus: 0 };

// Her reach: every point her body's bones reach, through every frame of every clip (actions
// included: a jump must fit too), as an upright prism: her footprint (how far she reaches from her
// rest-pose hips in each of 32 directions) from her lowest to her highest point, padded by how far
// the mesh (hair, a coat, fingertips) sticks out past those bones at rest. Only the ~55 humanoid
// bones of the normalized rig the clips drive, at 10 frames a second, and no physics: playing
// everything once this way takes well under a second, where every bone with the spring bones
// swinging took several, on every load and every model switch.
export function measureReach(vrm, mixer, clips) {
  const h = vrm.humanoid, root = h.normalizedHumanBonesRoot, p = new THREE.Vector3();
  const bones = Object.keys(h.humanBones).map(name => h.getNormalizedBoneNode(name)).filter(Boolean);
  vrm.update(0);
  vrm.scene.updateMatrixWorld(true);
  const axis = h.getNormalizedBoneNode('hips').getWorldPosition(new THREE.Vector3());
  vrm.scene.traverse(o => { if (o.isSkinnedMesh) o.computeBoundingBox(); }); // bounds in the current pose, not the bind pose
  const mesh = new THREE.Box3().setFromObject(vrm.scene), rest = new THREE.Box3();
  bones.forEach(b => rest.expandByPoint(p.setFromMatrixPosition(b.matrixWorld)));
  const pad = Math.max(0, ...rest.min.clone().sub(mesh.min).toArray(), ...mesh.max.clone().sub(rest.max).toArray());

  const reach = RIM.map(() => 0), cos = RIM.map(Math.cos), sin = RIM.map(Math.sin);
  let top = -Infinity, bottom = Infinity;
  const sample = () => {
    root.updateMatrixWorld(true);
    for (const b of bones) {
      p.setFromMatrixPosition(b.matrixWorld);
      const x = p.x - axis.x, z = p.z - axis.z;
      for (let i = 0; i < RIM.length; i++) reach[i] = Math.max(reach[i], x * cos[i] + z * sin[i]);
      top = Math.max(top, p.y);
      bottom = Math.min(bottom, p.y);
    }
  };
  if (!clips.length) sample(); // no animations: she stays in the rest pose (arms out, so not otherwise)
  for (const clip of clips) {
    mixer.stopAllAction();
    const action = mixer.clipAction(clip).reset().play();
    for (let t = 0; t <= clip.duration; t += 1 / 10) {
      action.time = t;
      mixer.update(0);
      sample();
    }
    mixer.uncacheAction(clip); // motion.js makes its own when the clip plays
  }
  mixer.stopAllAction();
  h.resetNormalizedPose();
  // Every clip also plays mirrored (clips.js): her reach to the left is her reach to the right.
  RIM.forEach((a, i) => { reach[i] = reach[(48 - i) % 32] = Math.max(reach[i], reach[(48 - i) % 32]); });
  // The footprint's corners, a little outside the true one (by under 1/cos(π/32), 0.5%).
  const rim = RIM.map((a, i) => new THREE.Vector3(axis.x + (reach[i] + pad) * cos[i], 0, axis.z + (reach[i] + pad) * sin[i]));
  return { x: axis.x, z: axis.z, rim, top: top + pad, bottom: bottom - pad, stature: mesh.max.y - mesh.min.y };
}

// view: yaw/pitch orbit the focus, a point on her axis `focus` metres above her middle; zoom
// scales her on screen. Remembered in this browser/app.
export function createFraming(camera, reach, resizeWindow) {
  const height = reach.top - reach.bottom, middle = (reach.top + reach.bottom) / 2;
  const distance = 3 * height; // fixed, so the perspective never changes; about the old 20° view
  camera.far = 2 * distance;
  const view = { ...DEFAULT_VIEW };
  try { Object.assign(view, JSON.parse(localStorage.getItem('view')) || {}); } catch {}
  const focus = new THREE.Vector3(), back = new THREE.Vector3(), right = new THREE.Vector3(),
    up = new THREE.Vector3(), o = new THREE.Vector3();

  // How far her reach spreads from the focus as seen from this view, on an image plane one unit
  // in front of the camera: half its width and half its height.
  function spread(v) {
    focus.set(reach.x, middle + v.focus, reach.z);
    back.set(Math.sin(v.yaw) * Math.cos(v.pitch), Math.sin(v.pitch), Math.cos(v.yaw) * Math.cos(v.pitch));
    right.set(Math.cos(v.yaw), 0, -Math.sin(v.yaw));
    up.crossVectors(back, right);
    let u = 0, w = 0;
    for (const y of [reach.top, reach.bottom]) for (const corner of reach.rim) {
      o.set(corner.x, y, corner.z).sub(focus);
      const depth = distance - o.dot(back);
      u = Math.max(u, Math.abs(o.dot(right)) / depth);
      w = Math.max(w, Math.abs(o.dot(up)) / depth);
    }
    return [u * MARGIN, w * MARGIN];
  }
  // Pixels per unit of that plane, the same from every side so orbiting never resizes her. In the
  // app she stands 45% of the screen tall at zoom 1, whatever her clips reach, and the window
  // follows her reach up to 0.8 of her height (and half the screen) wide and 85% tall: past that, a wandering dance is
  // cropped rather than the window taking over the screen.
  //
  // A browser has no window to grow, so it sizes her to her *body* rather than to the widest thing any
  // clip reaches. That prism is about four times her silhouette — an arm out in one dance, a jump in
  // another — and fitting the prism into a phone's screen made her a quarter of the height, which is
  // what "too small, I have to zoom in a lot" was. Fitting her body crops a wide pose instead, the
  // price the app already pays. A tall screen gets most of her; a wide one keeps room around her.
  const FILL = { tall: 0.8, wide: 0.55 };
  const bodyScale = () => innerHeight * (innerHeight > innerWidth ? FILL.tall : FILL.wide)
    / (reach.stature / distance * MARGIN);
  const widest = v => RIM.map(yaw => spread({ ...v, yaw }))
    .reduce(([u, w], [su, sw]) => [Math.max(u, su), Math.max(w, sw)]);
  const { availWidth, availHeight } = screen; // read once: the window's own size must never feed back
  const appScale = resizeWindow && availHeight * 0.45 / (reach.stature / distance * MARGIN);
  // The caption is the one thing on the screen that is not her, and it can only be as wide as the
  // window: too narrow a window and a sentence folds into a column that covers half of her. So the
  // window is never narrower than about thirty-four em of the caption's own 15 px font, which is a
  // line of forty characters rather than twenty.
  const CAPTION_WIDTH = 510;

  function frame() {
    const scale = (appScale || bodyScale()) * view.zoom;
    // The window must not follow the camera. It is resized around its middle, where she is, so a
    // window that grew as the camera turned slid her across the screen -- which is all "she moves
    // when I orbit" ever was. So its height is her reach from every angle, and its width is a
    // caption line rather than her reach: a sentence has to fit across, not fold down over her.
    const tall = widest(view)[1];
    spread(view); // last, as it sets focus and back for the camera
    if (resizeWindow) {
      // Her widest poses are cropped in that width rather than taking the window over, as before.
      const width = Math.round(Math.max(160, Math.min(availWidth * 0.5, CAPTION_WIDTH)));
      const height = Math.round(Math.max(160, Math.min(availHeight * 0.85, 2 * tall * scale)));
      if (Math.abs(width - innerWidth) > 1 || Math.abs(height - innerHeight) > 1) resizeWindow(width, height);
    }
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(innerHeight / 2 / scale));
    camera.aspect = innerWidth / innerHeight;
    camera.position.copy(focus).addScaledVector(back, distance);
    camera.up.copy(up); // hers, not the world's: over her head and under her feet stay smooth
    camera.lookAt(focus);
    camera.updateProjectionMatrix();
  }

  // Arrow keys orbit, Shift+↑/↓ move the focus up or down her body, + and - zoom, 0 resets.
  function onKey(e) {
    const step = e.shiftKey
      ? { ArrowUp: ['focus', 0.05 * height], ArrowDown: ['focus', -0.05 * height] }[e.key]
      : { ArrowLeft: ['yaw', -0.15], ArrowRight: ['yaw', 0.15], ArrowUp: ['pitch', 0.15], ArrowDown: ['pitch', -0.15] }[e.key];
    if (step) view[step[0]] += step[1];
    else if (e.key === '+' || e.key === '=') view.zoom *= 1.1;
    else if (e.key === '-') view.zoom /= 1.1;
    else if (e.key === '0') Object.assign(view, DEFAULT_VIEW);
    else return;
    settle();
  }
  // The mouse wheel zooms too, a step per notch.
  const onWheel = e => onKey({ key: e.deltaY < 0 ? '+' : '-' });

  // A phone has no arrow keys and no wheel, so one finger orbits her and two pinch her, in the same
  // steps the keys take: there is one view, and the two ways of moving it must not drift apart.
  const DRAG_RADIANS_PER_PIXEL = 0.006;
  // A finger drags far further than a key press -- 250 px is 1.5 rad, past the top of her head -- so
  // the pitch stops short of the poles. Yaw is still all the way round, as the keys leave it.
  const PITCH_LIMIT = 1.35;
  const fingers = new Map(); // where each finger is, by pointer id
  let apart = 0; // how far apart two fingers were at the last move (named apart: `spread` is her reach)

  function twoFingers() {
    const [a, b] = [...fingers.values()];
    return fingers.size === 2 ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
  }
  function onPointerDown(e) {
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    apart = twoFingers();
  }
  function onPointerMove(e) {
    const was = fingers.get(e.pointerId);
    if (!was) return false; // a pointer that did not start on her, a mouse moving over the page
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (fingers.size === 1) {
      view.yaw += (e.clientX - was.x) * DRAG_RADIANS_PER_PIXEL;
      view.pitch -= (e.clientY - was.y) * DRAG_RADIANS_PER_PIXEL;
      view.pitch = THREE.MathUtils.clamp(view.pitch, -PITCH_LIMIT, PITCH_LIMIT);
    } else if (fingers.size === 2) {
      const now = twoFingers();
      if (apart > 0 && now > 0) view.zoom *= now / apart;
      apart = now;
    } else {
      return false; // three fingers are not a gesture this page has
    }
    settle();
    return true; // the view moved: the caller owes it a notice, as every key press does
  }
  function onPointerUp(e) {
    fingers.delete(e.pointerId);
    apart = twoFingers(); // the finger left over must not count as a pinch
  }

  // After anything moved the view: angles wrapped all the way round, a size and a focus that are
  // allowed, remembered for the next window, and drawn.
  function settle() {
    for (const angle of ['yaw', 'pitch']) view[angle] = Math.atan2(Math.sin(view[angle]), Math.cos(view[angle]));
    view.zoom = THREE.MathUtils.clamp(view.zoom, 0.5, 8);
    view.focus = THREE.MathUtils.clamp(view.focus, -height / 2, height / 2);
    try { localStorage.setItem('view', JSON.stringify(view)); } catch {}
    frame();
  }
  return { frame, onKey, onWheel, onPointerDown, onPointerMove, onPointerUp };
}
