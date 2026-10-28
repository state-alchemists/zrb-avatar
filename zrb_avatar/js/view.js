// What the user is looking at: the part of her at the middle of the window, from which side, and
// how close. Read once the view settles after a key, so the views passed while orbiting don't count.
// `covers` is what the model's own <name>.json says her outfit covers, so a close look at a hoodie
// is not the same thing as a close look at bare skin.
import * as THREE from 'three';

const CLOSE = 0.6; // a close-up shows less than this much of her (head to feet) across the window
const BARE = new Set(['chest', 'hips', 'thighs']); // a close look here, uncovered, makes her blush
const FROM_BELOW = new Set(['hips', 'thighs']); // and so does these, seen from underneath
const SETTLE = 1500; // ms after the last key
const STEPS = 10; // a change of a tenth of her height across the window is a different view

// Call it at load, while she stands in her rest pose: that is how tall she is.
export function createView(vrm, camera, settled, covers = []) {
  const node = name => vrm.humanoid.getNormalizedBoneNode(name);
  const at = name => node(name)?.getWorldPosition(new THREE.Vector3());
  const mid = (a, b) => a.add(b).multiplyScalar(0.5);
  vrm.scene.updateMatrixWorld(true);
  const tall = at('head').y - Math.min(at('leftFoot').y, at('rightFoot').y);

  // Each part's middle as she stands now (neck, chest and upperChest are optional in VRM).
  function parts() {
    const chest = at('upperChest') ?? at('chest') ?? at('spine'), top = at('neck') ?? at('head');
    const found = [['face', node('head').localToWorld(new THREE.Vector3(0, 0.05 * tall, 0))],
      ['chest', mid(chest.clone(), top)], ['belly', mid(at('spine'), chest)],
      ['hips', mid(at('leftUpperLeg'), at('rightUpperLeg'))]];
    for (const s of ['left', 'right']) {
      found.push(['thighs', mid(at(`${s}UpperLeg`), at(`${s}LowerLeg`))],
        ['legs', mid(at(`${s}LowerLeg`), at(`${s}Foot`))], ['feet', at(`${s}Foot`)]);
    }
    return found;
  }

  const p = new THREE.Vector3(), to = new THREE.Vector3(), ahead = new THREE.Vector3(), q = new THREE.Quaternion();
  function read() {
    let part, centre, nearest = Infinity;
    for (const [name, point] of parts()) {
      p.copy(point).project(camera);
      const off = Math.hypot(p.x * camera.aspect, p.y);
      if (Math.abs(p.z) <= 1 && off < nearest) [part, centre, nearest] = [name, point, off];
    }
    if (!part) return null;
    const away = camera.position.distanceTo(centre);
    const shows = 2 * away * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * Math.min(1, camera.aspect) / tall;
    to.copy(camera.position).sub(centre).normalize();
    ahead.set(0, 0, 1).applyQuaternion(node('hips').getWorldQuaternion(q)); // where she faces
    const facing = (to.x * ahead.x + to.z * ahead.z) / Math.hypot(to.x, to.z);
    const side = to.y < -0.5 ? 'below' : to.y > 0.7 ? 'above'
      : facing > 0.5 ? 'front' : facing < -0.5 ? 'behind' : 'side';
    const close = shows < CLOSE;
    // Nothing to be shy about where the outfit covers her: a close look at a hoodie is a look at
    // a hoodie. Without a notes file nothing is covered, which is how this behaved before one.
    const covered = covers.includes(part);
    const shy = !covered && (close && BARE.has(part) || side === 'below' && FROM_BELOW.has(part));
    return { part, side, close, shy, shows: Math.round(shows * 100) / 100 };
  }

  let timer, last = '';
  return {
    read,
    // After a key: once the view settles, settled(view) if it shows something else than before.
    notice() {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const now = read(), key = now && `${now.part} ${now.side} ${now.close} ${Math.round(now.shows * STEPS)}`;
        if (now && key !== last) { last = key; settled(now); }
      }, SETTLE);
    },
    // Another model took her place: a view still settling would read this one's bones.
    dispose() { clearTimeout(timer); },
  };
}
