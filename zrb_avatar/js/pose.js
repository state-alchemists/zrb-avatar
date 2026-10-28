// Her body, moved by zrb itself (FacePose): each hand to a place on her body or in the air, or each
// joint to an angle (fingers, wrists, elbows, arms, shoulders, spine, head, legs, knees, ankles);
// on top of whatever clip plays, easing in, held for a few seconds, easing out. The arms reach by
// two-bone IK, so the places fit any model.
import * as THREE from 'three';

// Where her LEFT hand's wrist goes: [bone, offset from it in that bone's axes, where the elbow
// points (from the shoulder)], in metres for a body 1.35 m from head to feet; the right hand
// mirrors it. chest and hips cross to cover her: the forearm across her chest, the hand in front
// of her hips.
const HANDS = {
  chest: ['upperChest', [-0.07, 0.03, 0.14], [0.25, -0.45, 0]],
  belly: ['spine', [-0.02, 0.03, 0.15], [0.3, -0.4, 0]],
  hips: ['hips', [-0.06, -0.07, 0.15], [0.35, -0.15, -0.05]],
  chin: ['head', [0, -0.1, 0.09], [0.15, -0.5, 0.1]],
  cheek: ['head', [0.06, -0.08, 0.07], [0.25, -0.5, 0.1]],
  mouth: ['head', [0, -0.06, 0.12], [0.15, -0.5, 0.1]],
  head: ['head', [0.05, 0.13, 0.02], [0.4, 0.1, -0.1]],
  hip: ['hips', [0.17, 0.03, -0.02], [0.4, -0.1, -0.3]],
  forward: ['upperChest', [0.12, 0, 0.45], [0.3, -0.4, 0]],
  up: ['upperChest', [0.18, 0.55, 0.05], [0.4, 0, 0]],
  wave: ['upperChest', [0.4, 0.3, 0.1], [0.5, -0.4, 0]],
};
// Turns (degrees, x down, y to her left, z tilt to her left) of a bone, for the head and body.
const HEAD = {
  down: { head: [20, 0, 0] }, up: { head: [-15, 0, 0] }, left: { head: [0, 30, 0] }, right: { head: [0, -30, 0] },
  'tilt-left': { head: [0, 0, 15] }, 'tilt-right': { head: [0, 0, -15] }, away: { head: [18, -35, 8] },
};
const BODY = {
  'lean-forward': { spine: [10, 0, 0], chest: [6, 0, 0] },
  'lean-back': { spine: [-8, 0, 0], chest: [-4, 0, 0] },
  'stand-tall': { spine: [-3, 0, 0], chest: [-3, 0, 0], neck: [-3, 0, 0] },
  // shy: shoulders in, a little bent, knees together, one heel up
  hunch: { spine: [6, 0, 0], chest: [6, 0, 0], leftShoulder: [0, -12, 0], rightShoulder: [0, 12, 0],
    leftUpperLeg: [-4, 0, -4], rightUpperLeg: [-12, 0, 6], rightLowerLeg: [22, 0, 0], rightFoot: [-8, 0, 0] },
};
// How curled each finger is (thumb, index, middle, ring, little; 0 straight, 1 a fist).
const FINGERS = {
  open: [0, 0, 0, 0, 0], relaxed: [0.2, 0.25, 0.3, 0.35, 0.4], fist: [1, 1, 1, 1, 1],
  point: [1, 0, 1, 1, 1], peace: [1, 0, 0, 1, 1], 'thumbs-up': [0, 1, 1, 1, 1], ok: [0.6, 0.7, 0, 0, 0],
};
const FINGER_BONES = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
// Each joint's range, in degrees, as FacePose takes it (README, zrb_init.py).
const RANGE = {
  knee: [0, 150], ankle: [-40, 60], legForward: [-40, 120], legOut: [-20, 80],
  elbow: [0, 150], armRaise: [0, 180], armForward: [-60, 120], wristBend: [-70, 80], wristTwist: [-90, 90],
  shrug: [-15, 30], nod: [-40, 50], turn: [-70, 70], tilt: [-40, 40], spineBend: [-30, 60], spineTurn: [-45, 45], spineSide: [-30, 30],
};
export const POSES = { hands: Object.keys(HANDS), head: Object.keys(HEAD), body: Object.keys(BODY), fingers: Object.keys(FINGERS) };

const deg = THREE.MathUtils.degToRad;
const clamp = (value, [lo, hi]) => THREE.MathUtils.clamp(Number(value) || 0, lo, hi);
const nums = (value, n) => Array.isArray(value) && value.length === n && value.every(Number.isFinite) ? value : null;

// The joints a pose holds at an angle: bone -> local rotation, in her normalized rig (rest pose
// unrotated: x across to her left, y up, z forward; the right side mirrors the left).
function jointAngles(p) {
  const out = {}, e = new THREE.Euler();
  const set = (bone, x, y, z) => { out[bone] = new THREE.Quaternion().setFromEuler(e.set(deg(x), deg(y), deg(z))); };
  for (const [side, s] of [['left', 1], ['right', -1]]) {
    const leg = nums(p[`${side}Leg`], 2);
    if (leg) set(`${side}UpperLeg`, -clamp(leg[0], RANGE.legForward), 0, s * clamp(leg[1], RANGE.legOut));
    if (p[`${side}Knee`] != null) set(`${side}LowerLeg`, clamp(p[`${side}Knee`], RANGE.knee), 0, 0);
    if (p[`${side}Ankle`] != null) set(`${side}Foot`, clamp(p[`${side}Ankle`], RANGE.ankle), 0, 0);
    if (p[`${side}Shoulder`] != null) set(`${side}Shoulder`, 0, 0, s * clamp(p[`${side}Shoulder`], RANGE.shrug));
    if (p[`${side}Elbow`] != null) set(`${side}LowerArm`, 0, -s * clamp(p[`${side}Elbow`], RANGE.elbow), 0);
    const wrist = nums(p[`${side}Wrist`], 2);
    if (wrist) set(`${side}Hand`, s * clamp(wrist[1], RANGE.wristTwist), 0, -s * clamp(wrist[0], RANGE.wristBend));
    const curls = FINGERS[p[`${side}Fingers`]] ?? nums(p[`${side}Fingers`], 5);
    curls?.forEach((c, i) => {
      c = THREE.MathUtils.clamp(c, 0, 1);
      if (i === 0) { // the thumb folds across the palm
        set(`${side}ThumbMetacarpal`, 0, -s * 25 * c, -s * 20 * c);
        set(`${side}ThumbProximal`, 0, -s * 35 * c, 0);
        set(`${side}ThumbDistal`, 0, -s * 40 * c, 0);
      } else {
        for (const [part, most] of [['Proximal', 75], ['Intermediate', 95], ['Distal', 60]]) set(`${side}${FINGER_BONES[i]}${part}`, 0, 0, -s * most * c);
      }
    });
  }
  const head = nums(p.head, 3);
  if (head) set('head', clamp(head[0], RANGE.nod), clamp(head[1], RANGE.turn), clamp(head[2], RANGE.tilt));
  const spine = nums(p.spine, 3);
  if (spine) set('spine', clamp(spine[0], RANGE.spineBend), clamp(spine[1], RANGE.spineTurn), clamp(spine[2], RANGE.spineSide));
  return out;
}

// Call it at load, while she stands in her rest pose: her height and arm lengths.
export function createPose(vrm) {
  const h = vrm.humanoid, node = name => h.getNormalizedBoneNode(name);
  const at = name => node(name).getWorldPosition(new THREE.Vector3());
  vrm.scene.updateMatrixWorld(true);
  const k = (at('head').y - Math.min(at('leftFoot').y, at('rightFoot').y)) / 1.35;
  const arm = Object.fromEntries(['left', 'right'].map(s =>
    [s, [at(`${s}UpperArm`).distanceTo(at(`${s}LowerArm`)), at(`${s}LowerArm`).distanceTo(at(`${s}Hand`))]]));
  const frame = name => node(name) ?? (name === 'upperChest' ? node('chest') ?? node('spine') : null);
  const ankle = Math.min(at('leftFoot').y, at('rightFoot').y); // above the floor (y 0) at rest

  let wanted = {}, angles = {}, until = 0, t = 0, w = 0;
  const saved = [], q = new THREE.Quaternion(), r = new THREE.Quaternion(), e = new THREE.Euler();
  const S = new THREE.Vector3(), T = new THREE.Vector3(), P = new THREE.Vector3(), E = new THREE.Vector3(), v = new THREE.Vector3();
  const keep = n => saved.push([n, n.quaternion.clone(), n.position.clone()]);
  const restDir = x => v.set(x, 0, 0).applyQuaternion(h.normalizedHumanBonesRoot.getWorldQuaternion(r)).clone();

  // Turns a bone by `degrees` on top of the clip, as much as the pose has eased in.
  function turn(name, degrees) {
    const n = node(name);
    if (!n) return;
    keep(n);
    q.setFromEuler(e.set(...degrees.map(deg))).slerp(r.identity(), 1 - w);
    n.quaternion.multiply(q);
  }
  // Holds a bone at a local rotation, eased in from the clip's.
  function hold(name, target) {
    const n = node(name);
    if (!n) return;
    keep(n);
    n.quaternion.slerp(target, w);
  }
  // With her legs moved, her hips go down (or up) until her lowest point is on the floor: her
  // soles, toes, or her bottom when she sits; so a bent knee is a crouch, not a lifted foot.
  function ground() {
    const hips = node('hips');
    keep(hips);
    h.normalizedHumanBonesRoot.updateWorldMatrix(true, true);
    const y = name => node(name)?.getWorldPosition(v).y ?? Infinity;
    const low = Math.min(y('leftFoot') - ankle, y('rightFoot') - ankle, y('leftToes') - ankle / 2, y('rightToes') - ankle / 2,
      Math.min(y('leftUpperLeg'), y('rightUpperLeg')) - 0.09 * k);
    // in world metres: the rig above the hips may be scaled
    hips.position.copy(hips.parent.worldToLocal(hips.getWorldPosition(v).add(T.set(0, -low * w, 0))));
    h.normalizedHumanBonesRoot.updateWorldMatrix(true, true);
  }
  // Sets a bone so that it points along `dir` (world), from its rest direction, eased in.
  function aim(n, rest, dir) {
    keep(n);
    const world = q.setFromUnitVectors(rest, dir);
    n.quaternion.slerp(n.parent.getWorldQuaternion(r).invert().multiply(world), w);
    n.updateMatrixWorld(true);
  }
  function reach(side, place) {
    const [boneName, offset, pole] = HANDS[place], x = side === 'left' ? 1 : -1, [L1, L2] = arm[side];
    const up = node(`${side}UpperArm`), low = node(`${side}LowerArm`), hand = node(`${side}Hand`);
    const sway = place === 'wave' ? Math.sin(t * 8) * 0.08 : 0;
    frame(boneName).localToWorld(T.set((offset[0] + sway) * x * k, offset[1] * k, offset[2] * k));
    up.getWorldPosition(S);
    const body = frame('upperChest').getWorldQuaternion(r);
    P.set(pole[0] * x, pole[1], pole[2]).applyQuaternion(body).add(S);
    const dir = T.clone().sub(S), dist = Math.min(dir.length(), (L1 + L2) * 0.999);
    dir.normalize();
    const bend = P.sub(S).addScaledVector(dir, -P.dot(dir)).normalize();
    const a = Math.acos(THREE.MathUtils.clamp((L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist), -1, 1));
    E.copy(S).addScaledVector(dir, L1 * Math.cos(a)).addScaledVector(bend, L1 * Math.sin(a));
    const rest = restDir(x);
    aim(up, rest, E.clone().sub(S).normalize());
    aim(low, rest, T.clone().sub(low.getWorldPosition(E)).normalize());
    if (!angles[`${side}Hand`]) { keep(hand); hand.quaternion.slerp(r.identity(), w); } // the wrist straight
  }
  // The upper arm pointing `raise` degrees up from hanging (90: out to her side, 180: overhead),
  // swung `forward` degrees toward the front.
  // Twisted so the elbow bends the way an elbow does: the hand toward the front of her face.
  function point(side, [raise, forward]) {
    const x = side === 'left' ? 1 : -1, a = deg(clamp(raise, RANGE.armRaise)), b = deg(clamp(forward, RANGE.armForward));
    const root = h.normalizedHumanBonesRoot.getWorldQuaternion(new THREE.Quaternion());
    const dir = new THREE.Vector3(x * Math.sin(a) * Math.cos(b), -Math.cos(a), Math.sin(a) * Math.sin(b)).applyQuaternion(root);
    const face = new THREE.Vector3(-x * 0.1, 0.25, 0.3).applyQuaternion(root);
    const bend = face.addScaledVector(dir, -face.dot(dir));
    if (bend.lengthSq() < 1e-6) bend.set(0, 0, 1).applyQuaternion(root).addScaledVector(dir, -dir.z);
    // the upper arm's own axes: x along the arm (her left arm's +x points out of it), z the bend
    const X = dir.clone().multiplyScalar(x), Z = bend.normalize(), Y = Z.clone().cross(X);
    const world = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(X, Y, Z));
    const up = node(`${side}UpperArm`);
    keep(up);
    up.quaternion.slerp(up.parent.getWorldQuaternion(r).invert().multiply(world), w);
    up.updateMatrixWorld(true);
  }

  return {
    // Places and presets by name (leftHand, rightHand, head, body), joint angles (see jointAngles),
    // seconds; whatever is not named stays the clip's.
    set(p) {
      wanted = { leftHand: HANDS[p.leftHand] && p.leftHand, rightHand: HANDS[p.rightHand] && p.rightHand,
        head: HEAD[p.head] && p.head, body: BODY[p.body] && p.body,
        leftArm: nums(p.leftArm, 2), rightArm: nums(p.rightArm, 2) };
      angles = jointAngles(p);
      // `hold` keeps it until another pose or release(): her body is hers to keep, not only to
      // borrow. A timed pose lets go on its own, which is what every pose did before this.
      until = p.hold ? Infinity : t + THREE.MathUtils.clamp(Number(p.seconds) || 5, 1, 30);
    },
    // Let go: eases out from wherever it was, and the clip has her body again.
    release() { until = 0; },
    // After the clip has posed her, before vrm.update; undo() after rendering.
    apply(dt) {
      t += dt;
      w += ((t < until ? 1 : 0) - w) * Math.min(1, dt * 5);
      if (w < 0.001) return;
      for (const [bone, degrees] of Object.entries({ ...BODY[wanted.body], ...HEAD[wanted.head] })) turn(bone, degrees);
      const arms = /^(left|right)(UpperArm|LowerArm)$/; // after the arms are placed, below
      for (const [bone, target] of Object.entries(angles)) if (!arms.test(bone) && !/Hand$|Thumb|Index|Middle|Ring|Little/.test(bone)) hold(bone, target);
      if (wanted.body === 'hunch' || Object.keys(angles).some(b => /Leg|Foot/.test(b))) ground();
      h.normalizedHumanBonesRoot.updateWorldMatrix(true, true);
      for (const side of ['left', 'right']) {
        const place = wanted[`${side}Hand`];
        if (place) reach(side, place);
        else {
          if (wanted[`${side}Arm`]) point(side, wanted[`${side}Arm`]);
          if (angles[`${side}LowerArm`]) hold(`${side}LowerArm`, angles[`${side}LowerArm`]);
        }
      }
      for (const [bone, target] of Object.entries(angles)) if (/Hand$|Thumb|Index|Middle|Ring|Little/.test(bone)) hold(bone, target);
    },
    undo() { while (saved.length) { const [n, quat, pos] = saved.pop(); n.quaternion.copy(quat); n.position.copy(pos); } },
  };
}
