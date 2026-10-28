// Mixamo FBX -> VRMA: node fbx2vrma.mjs <in.fbx> <out.vrma> ["what it looks like"]
//
// The retargeting is three-vrm's own humanoidAnimation example (loadMixamoAnimation.js): every
// track turned into a rotation from the T-pose in world-aligned axes, which is what a VRMA whose
// rest pose is unrotated holds. The VRMA's skeleton is the FBX's own rest pose, in metres.
//
// Mixamo's rest pose is a T-pose, so nothing is offset here. A clip of another rig goes through
// gltf2vrma.py (Rigify, or a glTF/GLB that already speaks humanoid), or bvh2vrma.mjs for BVH.
import fs from 'fs';
import * as THREE from 'three';
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js';
import { faceFront, facing, glb, timeline } from './vrma-writer.mjs';

const RIG = {
  Hips: 'hips', Spine: 'spine', Spine1: 'chest', Spine2: 'upperChest', Neck: 'neck', Head: 'head',
};
for (const [m, v] of [['Left', 'left'], ['Right', 'right']]) {
  Object.assign(RIG, {
    [`${m}Shoulder`]: `${v}Shoulder`, [`${m}Arm`]: `${v}UpperArm`, [`${m}ForeArm`]: `${v}LowerArm`, [`${m}Hand`]: `${v}Hand`,
    [`${m}UpLeg`]: `${v}UpperLeg`, [`${m}Leg`]: `${v}LowerLeg`, [`${m}Foot`]: `${v}Foot`, [`${m}ToeBase`]: `${v}Toes`,
    [`${m}HandThumb1`]: `${v}ThumbMetacarpal`, [`${m}HandThumb2`]: `${v}ThumbProximal`, [`${m}HandThumb3`]: `${v}ThumbDistal`,
  });
  for (const [f, g] of [['Index', 'Index'], ['Middle', 'Middle'], ['Ring', 'Ring'], ['Pinky', 'Little']]) {
    Object.assign(RIG, { [`${m}Hand${f}1`]: `${v}${g}Proximal`, [`${m}Hand${f}2`]: `${v}${g}Intermediate`, [`${m}Hand${f}3`]: `${v}${g}Distal` });
  }
}

export function convert(fbx, description = '') {
  const asset = new FBXLoader().parse(fbx.buffer.slice(fbx.byteOffset, fbx.byteOffset + fbx.byteLength), '');
  const clip = THREE.AnimationClip.findByName(asset.animations, 'mixamo.com') ?? asset.animations.find(c => c.tracks.length);
  if (!clip) throw new Error('no animation in this FBX');
  asset.updateMatrixWorld(true);
  const bone = name => asset.getObjectByName(`mixamorig${name}`);
  const hips = bone('Hips');
  if (!hips) throw new Error('no mixamorigHips: not a Mixamo rig');
  const unit = hips.getWorldPosition(new THREE.Vector3()).y > 10 ? 0.01 : 1; // Mixamo is in centimetres

  // The skeleton: each humanoid bone under its nearest humanoid ancestor, offset in metres.
  const nodes = [], index = {};
  for (const [m, v] of Object.entries(RIG)) {
    if (bone(m)) index[v] = nodes.push({ name: v, mixamo: bone(m) }) - 1;
  }
  const world = o => o.getWorldPosition(new THREE.Vector3()).multiplyScalar(unit);
  for (const n of nodes) {
    let up = n.mixamo.parent;
    while (up && !(up.name.replace('mixamorig', '') in RIG && index[RIG[up.name.replace('mixamorig', '')]] !== undefined)) up = up.parent;
    const parent = up ? index[RIG[up.name.replace('mixamorig', '')]] : null;
    n.translation = world(n.mixamo).sub(parent == null ? new THREE.Vector3() : world(nodes[parent].mixamo)).toArray();
    if (parent != null) (nodes[parent].children ??= []).push(nodes.indexOf(n));
  }

  // The tracks, retargeted as in the example.
  const restInverse = new THREE.Quaternion(), parentRest = new THREE.Quaternion(), q = new THREE.Quaternion();
  const hipsScale = world(hips).y / hips.position.y; // the hips track is local units; ours rests at world(hips).y
  const tracks = [];
  // One time line for every track, 30 frames a second: FBX tracks keep only the keys they need.
  const times = timeline(clip.duration);
  for (const track of clip.tracks) {
    const [name, property] = track.name.split('.'), human = RIG[name.replace('mixamorig', '')];
    if (human === undefined || index[human] === undefined) continue;
    const at = track.createInterpolant(), size = track.getValueSize(), values = new Float32Array(times.length * size);
    times.forEach((t, i) => values.set(at.evaluate(t), i * size));
    if (property === 'quaternion') {
      const rig = asset.getObjectByName(name);
      rig.getWorldQuaternion(restInverse).invert();
      rig.parent.getWorldQuaternion(parentRest);
      for (let i = 0; i < values.length; i += 4) q.fromArray(values, i).premultiply(parentRest).multiply(restInverse).toArray(values, i);
      tracks.push({ node: index[human], path: 'rotation', times, values, type: 'VEC4' });
    } else if (property === 'position' && human === 'hips') {
      tracks.push({ node: index[human], path: 'translation', times, values: values.map(v => v * hipsScale), type: 'VEC3' });
    }
  }
  faceFront(facing(asset, clip, bone), tracks.find(t => t.node === index.hips && t.path === 'rotation'),
    tracks.find(t => t.node === index.hips && t.path === 'translation'));
  return glb(nodes, tracks, description);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const [input, output, description] = process.argv.slice(2);
  if (!input || !output) {
    console.error('usage: node fbx2vrma.mjs <in.fbx> <out.vrma> ["what it looks like"]');
    process.exit(2);
  }
  fs.writeFileSync(output, convert(fs.readFileSync(input), description));
  console.log('wrote', output);
}
