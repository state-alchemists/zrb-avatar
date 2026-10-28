// Mixamo FBX -> VRMA, for zrb_avatar/anim:  npm run fbx2vrma -- <in.fbx> <out.vrma> ["description"]
// The retargeting is three-vrm's own humanoidAnimation example (loadMixamoAnimation.js): every
// track turned into a rotation from the T-pose in world-aligned axes, which is what a VRMA whose
// rest pose is unrotated holds. The VRMA's skeleton is the FBX's own rest pose, in metres.
import fs from 'fs';
import * as THREE from 'three';
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js';

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
  const times = Float32Array.from({ length: Math.round(clip.duration * 30) + 1 }, (_, i) => Math.min(i / 30, clip.duration));
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

// Where she faces through the clip, played on the FBX's own rig: the normal of her shoulders
// and hips, as a yaw (radians, 0 is front), and how steadily (1: always the same way).
function facing(asset, clip, bone) {
  const mixer = new THREE.AnimationMixer(asset), action = mixer.clipAction(clip).play();
  const at = name => bone(name).getWorldPosition(new THREE.Vector3()), up = new THREE.Vector3(0, 1, 0), sum = new THREE.Vector2();
  let n = 0;
  for (let t = 0; t <= clip.duration; t += 0.1, n++) {
    action.time = t;
    mixer.update(0);
    asset.updateMatrixWorld(true);
    const across = at('LeftArm').sub(at('RightArm')).add(at('LeftUpLeg').sub(at('RightUpLeg')));
    const forward = across.cross(up);
    sum.add(new THREE.Vector2(forward.x, forward.z).normalize());
  }
  return { yaw: Math.atan2(sum.x, sum.y), steady: sum.length() / n };
}

// Mixamo's talking and idles often face someone beside her; she talks to whoever is in front of
// her. A clip that keeps to one direction (more than 10° off) is turned to face front; one that
// turns about (a spin, a dance) is left as it is.
function faceFront({ yaw, steady }, rotation, translation) {
  if (!rotation || steady < 0.9 || Math.abs(yaw) < Math.PI / 18) return;
  const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -yaw), q = new THREE.Quaternion(), p = new THREE.Vector3();
  for (let i = 0; i < rotation.values.length; i += 4) q.fromArray(rotation.values, i).premultiply(turn).toArray(rotation.values, i);
  for (let i = 0; translation && i < translation.values.length; i += 3) p.fromArray(translation.values, i).applyQuaternion(turn).toArray(translation.values, i);
  console.log(`turned ${Math.round(-yaw * 180 / Math.PI)}° to face front`);
}

function glb(nodes, tracks, description) {
  const views = [], accessors = [], chunks = [];
  let offset = 0;
  const accessor = (floats, type, minmax = {}) => {
    const bytes = Buffer.from(Float32Array.from(floats).buffer);
    views.push({ buffer: 0, byteOffset: offset, byteLength: bytes.length });
    chunks.push(bytes);
    offset += bytes.length;
    return accessors.push({ bufferView: views.length - 1, componentType: 5126, count: floats.length / { SCALAR: 1, VEC3: 3, VEC4: 4 }[type], type, ...minmax }) - 1;
  };
  const times = new Map(), samplers = [], channels = [];
  for (const t of tracks) {
    const key = t.times.join(',');
    if (!times.has(key)) times.set(key, accessor(t.times, 'SCALAR', { min: [t.times[0]], max: [t.times[t.times.length - 1]] }));
    samplers.push({ input: times.get(key), output: accessor(t.values, t.type), interpolation: 'LINEAR' });
    channels.push({ sampler: samplers.length - 1, target: { node: t.node, path: t.path } });
  }
  const bin = Buffer.concat(chunks);
  const humanBones = Object.fromEntries(nodes.map((n, i) => [n.name, { node: i }]));
  const json = {
    asset: { version: '2.0', generator: 'zrb-avatar fbx2vrma' },
    extensionsUsed: ['VRMC_vrm_animation'],
    extensions: { VRMC_vrm_animation: { specVersion: '1.0', humanoid: { humanBones } } },
    scene: 0, scenes: [{ nodes: [0] }],
    nodes: nodes.map(n => ({ name: n.name, translation: n.translation, ...(n.children ? { children: n.children } : {}) })),
    animations: [{ channels, samplers, ...(description ? { extras: { description } } : {}) }],
    buffers: [{ byteLength: bin.length }], bufferViews: views, accessors,
  };
  const pad = (b, fill) => Buffer.concat([b, Buffer.alloc((4 - b.length % 4) % 4, fill)]);
  const text = pad(Buffer.from(JSON.stringify(json)), 0x20), data = pad(bin, 0);
  const chunk = (b, type) => { const h = Buffer.alloc(8); h.writeUInt32LE(b.length, 0); h.write(type, 4); return Buffer.concat([h, b]); };
  const head = Buffer.alloc(12);
  head.write('glTF', 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(12 + 8 + text.length + 8 + data.length, 8);
  return Buffer.concat([head, chunk(text, 'JSON'), chunk(data, 'BIN\0')]);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const [input, output, description] = process.argv.slice(2);
  if (!input || !output) {
    console.error('usage: npm run fbx2vrma -- <in.fbx> <out.vrma> ["what it looks like"]');
    process.exit(2);
  }
  fs.writeFileSync(output, convert(fs.readFileSync(input), description));
  console.log('wrote', output);
}
