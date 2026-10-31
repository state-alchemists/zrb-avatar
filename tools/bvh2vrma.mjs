// BVH -> VRMA: node bvh2vrma.mjs <in.bvh> <out.vrma> ["what it looks like"] [--frames FROM TO] [--bones]
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

// 100STYLE (Holden, Saito, Komura) reuses the word "shoulder" for the wrong joint, so this rig
// is spelled out rather than guessed: its `RightCollar` is the clavicle, its `RightShoulder` is
// the upper arm, `RightElbow` the forearm, `RightWrist` the hand, and the legs repeat the joke
// with `RightHip`/`RightKnee`/`RightAnkle`. The generic matcher above would file RightShoulder
// as a clavicle and leave the upper arm unmapped, which renders as arms welded to the chest.
// The four-segment chest is collapsed onto VRM's three: `Chest3` carries the least and is the
// one skipped.
const STYLE100 = {
  hips: ['Hips'],
  spine: ['Chest'],
  chest: ['Chest2'],
  upperChest: ['Chest4'],
  neck: ['Neck'],
  head: ['Head'],
};
for (const [side, cap] of [['left', 'Left'], ['right', 'Right']]) {
  Object.assign(STYLE100, {
    [`${side}Shoulder`]: [`${cap}Collar`],
    [`${side}UpperArm`]: [`${cap}Shoulder`],
    [`${side}LowerArm`]: [`${cap}Elbow`],
    [`${side}Hand`]: [`${cap}Wrist`],
    [`${side}UpperLeg`]: [`${cap}Hip`],
    [`${side}LowerLeg`]: [`${cap}Knee`],
    [`${side}Foot`]: [`${cap}Ankle`],
    [`${side}Toes`]: [`${cap}Toe`],
  });
}

// A rig whose bones carry these exact names is 100STYLE's; the check is on three joints that no
// other spelling in the table produces together.
function isStyle100(skeleton) {
  const names = new Set(skeleton.bones.map(bone => bone.name));
  return ['Hips', 'Chest2', 'RightCollar', 'RightHip'].every(name => names.has(name));
}

// The clip between two times, times shifted to start at zero. BVH files from a mocap collection
// hold the whole capture of one variant, and the collection tells you in a CSV which frames of
// it are the clean loop -- so the cut has to happen here, before anything is sampled.
function between(clip, t0, t1) {
  const tracks = [];
  for (const track of clip.tracks) {
    const size = track.getValueSize(), times = [], values = [];
    track.times.forEach((time, i) => {
      if (time < t0 - 1e-6 || time > t1 + 1e-6) return;
      times.push(time - t0);
      for (let k = 0; k < size; k++) values.push(track.values[i * size + k]);
    });
    if (times.length < 2) continue;
    tracks.push(new track.constructor(track.name, times, values));
  }
  if (!tracks.length) throw new Error('the frame range holds no keyframes');
  return new THREE.AnimationClip(clip.name, t1 - t0, tracks);
}

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
  const table = isStyle100(skeleton) ? STYLE100 : candidates();
  for (const [bone, names] of Object.entries(table)) {
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

export function convert(bvh, description = '', verbose = false, range = null) {
  const parsed = new BVHLoader().parse(bvh.toString('utf8'));
  const { skeleton } = parsed;
  const clip = range ? between(parsed.clip, range[0], range[1]) : parsed.clip;
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
  // BVHLoader leaves every joint where its OFFSET puts it and never applies the first frame, so
  // what it hands back is the rig's rest pose -- and the hips sit at the root offset, which is
  // zero. The scale cannot be read off the hips' height for that reason; a thigh is forty-odd
  // centimetres in every capture in the wild and 0.4 m in a metre one, which survives.
  const thigh = bones.leftUpperLeg && bones.leftLowerLeg
    ? bones.leftUpperLeg.getWorldPosition(new THREE.Vector3())
      .distanceTo(bones.leftLowerLeg.getWorldPosition(new THREE.Vector3()))
    : 0;
  const unit = thigh > 10 ? 0.01 : 1;
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
  // The hips are the exception: their offset is the root's, which is zero, while a body needs a
  // height to stand at. Take it from the capture's first frame rather than from the skeleton.
  const hipsTrack = clip.tracks.find(t => t.name === `${bones.hips.name}.position`);
  if (hipsTrack) nodes[index.hips].translation = [0, hipsTrack.values[1] * unit, 0];

  // The tracks, each turned into a rotation from the rest pose in world-aligned axes: the
  // same retarget the FBX path uses, so both arrive in one convention.
  const times = timeline(clip.duration);
  const restInverse = new THREE.Quaternion(), parentRest = new THREE.Quaternion(), q = new THREE.Quaternion();
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
      // The hips carry the capture's whole trajectory, and in a 100STYLE file that is a couple
      // of hundred centimetres into the scene: written as it stands, she arrives that far from
      // where the window is looking. What the clip needs is the motion from where it starts --
      // the sway, the step, the drop -- so the first sample becomes the origin **horizontally**.
      //
      // Not vertically. This track is absolute, not a delta: whatever it says on a frame is where
      // the hips are. Re-basing the up component to zero therefore declares her hips to be on the
      // floor, and every clip came out a metre underground -- with her legs through it. The whole
      // 100STYLE set was converted that way, and it could not be seen in any render, because every
      // camera in the harness was placed relative to her. The up component is kept as the capture
      // gave it; `floor.py` then puts the result on the library's floor.
      const start = [values[0], values[1], values[2]];
      const up = 1;
      tracks.push({
        node: index[node.name], path: 'translation', times, type: 'VEC3',
        values: Float32Array.from(values, (v, i) =>
          i % 3 === up ? v * unit : (v - start[i % 3]) * unit),
      });
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
  const rest = args.filter(a => a !== '--bones');
  const frameAt = rest.indexOf('--frames');
  const range = frameAt === -1 ? null : [Number(rest[frameAt + 1]), Number(rest[frameAt + 2])];
  const positional = frameAt === -1 ? rest : [...rest.slice(0, frameAt), ...rest.slice(frameAt + 3)];
  const [input, output, description] = positional;
  if (!input) {
    console.error('usage: node bvh2vrma.mjs <in.bvh> <out.vrma> ["what it looks like"] [--frames FROM TO] [--bones]');
    process.exit(2);
  }
  const source = fs.readFileSync(input);
  let seconds = null;
  if (range) {
    const frameTime = Number(/Frame Time:\s*([\d.]+)/.exec(source.toString('utf8'))?.[1]);
    if (!Number.isFinite(frameTime)) throw new Error('no Frame Time in this BVH: frames cannot be located');
    seconds = [range[0] * frameTime, range[1] * frameTime];
    console.log(`frames ${range[0]}..${range[1]} = ${seconds[1] - seconds[0]}s at ${(1 / frameTime).toFixed(1)} fps`);
  }
  const result = convert(source, description, verbose, seconds);
  if (!output) {
    console.log('nothing written: pass an output path to write the .vrma');
    process.exit(verbose ? 0 : 2);
  }
  fs.writeFileSync(output, result);
  console.log('wrote', output);
}
