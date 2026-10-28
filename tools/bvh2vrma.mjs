// BVH -> VRMA: node bvh2vrma.mjs <in.bvh> <out.vrma> ["what it looks like"] [--bones]
//
// BVH stores a bone hierarchy, per-frame channel values, and usually nothing else: no
// bind pose, no mesh, no humanoid mapping. So this reads the file, names the bones it can
// recognise as humanoid, and writes their motion into a .vrma. Two assumptions it cannot
// check, both worth knowing:
//
//   - the first frame is taken as the rig's rest pose. A capture that starts in a T-pose
//     arrives right; one that starts in an A-pose or mid-stride arrives skewed by however
//     far that first frame leans, and no amount of code here can tell which it is.
//   - BVH files agree about motion and disagree about names. The table below covers the
//     spellings this tool has seen (Mixamo-style `LeftArm`, CMU-style `LShoulder`/`LElbow`,
//     lowercase `left_arm`). `--bones` prints what it made of the file; add a spelling
//     there if a rig of your own is not recognised.
import fs from 'fs';
import * as THREE from 'three';
import { BVHLoader } from 'three/examples/jsm/loaders/BVHLoader.js';
import { faceFront, glb, timeline } from './vrma-writer.mjs';

// humanoid bone -> the words a rig may use for it. Matching ignores case, dots and
// underscores, and tries each side's prefix and abbreviation around each word.
const CORE = {
  hips: ['hips', 'hip', 'pelvis', 'root'],
  spine: ['spine', 'lowerback', 'abdomen'],
  chest: ['chest', 'spine1', 'upperback'],
  upperChest: ['upperchest', 'spine2', 'spine3'],
  neck: ['neck', 'neck1'],
  head: ['head', 'skull'],
};
const LIMBS = {
  Shoulder: ['shoulder', 'clavicle'],
  UpperArm: ['upperarm', 'arm'],
  LowerArm: ['lowerarm', 'forearm', 'elbow'],
  Hand: ['hand', 'wrist'],
  UpperLeg: ['upperleg', 'upleg', 'hipjoint', 'thigh'],
  LowerLeg: ['lowerleg', 'leg', 'knee'],
  Foot: ['foot', 'ankle'],
  Toes: ['toes', 'toe', 'toebase'],
};
const FINGERS = { Thumb: 'thumb', Index: 'index', Middle: 'middle', Ring: 'ring', Little: 'pinky' };
const JOINTS = { Proximal: '1', Intermediate: '2', Distal: '3' };

const normal = name => name.toLowerCase().replace(/[^a-z0-9]/g, '');

function candidates() {
  const table = {};
  for (const [bone, words] of Object.entries(CORE)) table[bone] = [...words];
  for (const [side, short] of [['left', 'l'], ['right', 'r']]) {
    for (const [limb, words] of Object.entries(LIMBS)) {
      const names = [];
      for (const word of words) names.push(`${side}${word}`, `${short}${word}`, `${word}${side}`, `${word}${short}`);
      table[`${side}${limb}`] = names;
    }
    for (const [finger, word] of Object.entries(FINGERS)) {
      for (const [joint, number] of Object.entries(JOINTS)) {
        table[`${side}${finger}${joint}`] = [`${side}hand${word}${number}`, `${short}hand${word}${number}`, `${side}${word}${number}`];
      }
    }
  }
  return table;
}

// The bones of a parsed BVH, by their normalised name.
function byNormalName(skeleton) {
  const map = new Map();
  for (const bone of skeleton.bones) if (!map.has(normal(bone.name))) map.set(normal(bone.name), bone);
  return map;
}

function mapBones(skeleton) {
  const found = {}, available = byNormalName(skeleton);
  for (const [bone, names] of Object.entries(candidates())) {
    for (const name of names) {
      const hit = available.get(normal(name));
      if (hit && !Object.values(found).includes(hit)) {
        found[bone] = hit;
        break;
      }
    }
  }
  return found;
}

// Where she faces, from the bones that mapped to the arms and legs.
function facingOf(root, clip, bones) {
  const needed = ['leftUpperArm', 'rightUpperArm', 'leftUpperLeg', 'rightUpperLeg'];
  if (!needed.every(name => bones[name])) return null;
  const mixer = new THREE.AnimationMixer(root), action = mixer.clipAction(clip).play();
  const up = new THREE.Vector3(0, 1, 0), sum = new THREE.Vector2();
  const at = bone => bone.getWorldPosition(new THREE.Vector3());
  let n = 0;
  for (let t = 0; t <= clip.duration; t += 0.1, n++) {
    action.time = t;
    mixer.update(0);
    root.updateMatrixWorld(true);
    const across = at(bones.leftUpperArm).sub(at(bones.rightUpperArm)).add(at(bones.leftUpperLeg).sub(at(bones.rightUpperLeg)));
    const forward = across.cross(up);
    sum.add(new THREE.Vector2(forward.x, forward.z).normalize());
  }
  return { yaw: Math.atan2(sum.x, sum.y), steady: sum.length() / n };
}

export function convert(bvh, description = '', verbose = false) {
  const { skeleton, clip } = new BVHLoader().parse(bvh.toString('utf8'));
  if (!clip || !clip.tracks.length) throw new Error('no animation in this BVH');
  const root = skeleton.bones[0];
  root.updateMatrixWorld(true);
  const bones = mapBones(skeleton);
  const mapped = Object.keys(bones);
  if (verbose) {
    for (const bone of skeleton.bones) {
      const human = mapped.find(name => bones[name] === bone);
      console.log(`  ${bone.name.padEnd(24)} ${human ?? '-'}`);
    }
  }
  if (!bones.hips) throw new Error('no bone maps to hips: run with --bones to see the names');
  if (mapped.length < 8) {
    throw new Error(`only ${mapped.length} humanoid bones mapped (${mapped.join(', ')}): run with --bones`);
  }

  // The skeleton: each humanoid bone under its nearest humanoid ancestor, offset in metres.
  const hipsWorld = bones.hips.getWorldPosition(new THREE.Vector3());
  const unit = hipsWorld.y > 10 ? 0.01 : 1; // centimetres, as most captures are
  const nodes = [], index = {};
  for (const name of mapped) index[name] = nodes.push({ name, bone: bones[name] }) - 1;
  const world = bone => bone.getWorldPosition(new THREE.Vector3()).multiplyScalar(unit);
  for (const node of nodes) {
    let up = node.bone.parent, parent = null;
    while (up && parent === null) {
      const human = mapped.find(name => bones[name] === up);
      if (human !== undefined) parent = index[human];
      up = up.parent;
    }
    node.translation = world(node.bone).sub(parent === null ? new THREE.Vector3() : world(nodes[parent].bone)).toArray();
    if (parent !== null) (nodes[parent].children ??= []).push(nodes.indexOf(node));
  }

  // The tracks, each turned into a rotation from the rest pose in world-aligned axes: the
  // same retarget the FBX path uses, so both arrive in one convention.
  const times = timeline(clip.duration);
  const restInverse = new THREE.Quaternion(), parentRest = new THREE.Quaternion(), q = new THREE.Quaternion();
  const hipsScale = world(bones.hips).y / bones.hips.position.y;
  const tracks = [];
  for (const track of clip.tracks) {
    const [name, property] = track.name.split('.');
    const node = nodes.find(n => n.bone.name === name);
    if (!node || (property !== 'quaternion' && property !== 'position')) continue;
    if (property === 'position' && node.name !== 'hips') continue; // the spec allows hips only
    const at = track.createInterpolant(), size = track.getValueSize(), values = new Float32Array(times.length * size);
    times.forEach((t, i) => values.set(at.evaluate(t), i * size));
    if (property === 'quaternion') {
      node.bone.getWorldQuaternion(restInverse).invert();
      // The root bone has no parent (BVHLoader leaves the skeleton unparented), and its
      // parent transform is identity, not an error.
      if (node.bone.parent) node.bone.parent.getWorldQuaternion(parentRest);
      else parentRest.identity();
      for (let i = 0; i < values.length; i += 4) q.fromArray(values, i).premultiply(parentRest).multiply(restInverse).toArray(values, i);
      tracks.push({ node: index[node.name], path: 'rotation', times, values, type: 'VEC4' });
    } else {
      tracks.push({ node: index[node.name], path: 'translation', times, values: values.map(v => v * hipsScale), type: 'VEC3' });
    }
  }
  const rotation = tracks.find(t => t.node === index.hips && t.path === 'rotation');
  const translation = tracks.find(t => t.node === index.hips && t.path === 'translation');
  const facing = facingOf(root, clip, bones);
  if (facing) faceFront(facing, rotation, translation);
  return glb(nodes, tracks, description);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const args = process.argv.slice(2);
  const verbose = args.includes('--bones');
  const [input, output, description] = args.filter(a => a !== '--bones');
  if (!input) {
    console.error('usage: node bvh2vrma.mjs <in.bvh> <out.vrma> ["what it looks like"] [--bones]');
    process.exit(2);
  }
  const result = convert(fs.readFileSync(input), description, verbose);
  if (!output) {
    console.log('nothing written: pass an output path to write the .vrma');
    process.exit(verbose ? 0 : 2);
  }
  fs.writeFileSync(output, result);
  console.log('wrote', output);
}
