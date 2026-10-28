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
  // cropped rather than the window taking over the screen. In a browser (or an app without
  // resizeWindow) she fits the window from whichever side is widest.
  const fit = (width, height, [u, w]) => Math.min(width / u, height / w) / 2;
  const widest = v => RIM.map(yaw => spread({ ...v, yaw }))
    .reduce(([u, w], [su, sw]) => [Math.max(u, su), Math.max(w, sw)]);
  const { availWidth, availHeight } = screen; // read once: the window's own size must never feed back
  const appScale = resizeWindow && availHeight * 0.45 / (reach.stature / distance * MARGIN);

  function frame() {
    const scale = (appScale || fit(innerWidth, innerHeight, widest(view))) * view.zoom;
    const [u, w] = spread(view); // last, as it sets focus and back for the camera
    if (resizeWindow) {
      // and no wider than 0.8 of her height on screen: arms stretched out fit, a clip that walks
      // sideways (a dance, the filing cabinet) is cropped for that moment rather than widening it
      const width = Math.round(Math.max(160, Math.min(availWidth * 0.5, 0.8 * reach.stature / distance * scale, 2 * u * scale)));
      const height = Math.round(Math.max(160, Math.min(availHeight * 0.85, 2 * w * scale)));
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
    for (const angle of ['yaw', 'pitch']) view[angle] = Math.atan2(Math.sin(view[angle]), Math.cos(view[angle])); // all the way round
    view.zoom = THREE.MathUtils.clamp(view.zoom, 0.5, 8);
    view.focus = THREE.MathUtils.clamp(view.focus, -height / 2, height / 2);
    try { localStorage.setItem('view', JSON.stringify(view)); } catch {}
    frame();
  }
  // The mouse wheel zooms too, a step per notch.
  const onWheel = e => onKey({ key: e.deltaY < 0 ? '+' : '-' });
  return { frame, onKey, onWheel };
}
