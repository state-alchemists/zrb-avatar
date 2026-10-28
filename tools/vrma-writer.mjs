// What the .vrma writers share: one time line per clip, the GLB container, and the
// "turn her to face front" pass a captured clip usually needs.
//
// A .vrma is a GLB holding no mesh: nodes for the skeleton, one animation, and the
// VRMC_vrm_animation extension mapping node indices to humanoid bone names. Two rules
// are easy to get wrong and both are enforced here:
//   - every channel of a clip carries the same keyframes (one time line), because a
//     consumer plays a clip as a single timeline;
//   - every animation sampler input accessor carries min/max, which glTF requires and
//     which is where a clip's length is read from.
import * as THREE from 'three';

export const FPS = 30;

// One time line for every track: FBX and BVH keep only the keys a curve needs, while a
// .vrma consumer expects each channel of a clip to carry the same count.
export function timeline(duration, fps = FPS) {
  const count = Math.max(1, Math.round(duration * fps));
  return Float32Array.from({ length: count + 1 }, (_, i) => Math.min(i / fps, duration));
}

// The GLB: nodes, tracks ({node, path, times, values, type}), and an optional description.
export function glb(nodes, tracks, description) {
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
    asset: { version: '2.0', generator: 'zrb-avatar tools' },
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

// Where she faces through the clip, played on the source's own rig: the normal of her
// shoulders and hips, as a yaw (radians, 0 is front), and how steadily (1: always the
// same way).
export function facing(asset, clip, bone) {
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

// A captured clip often faces someone beside her; she talks to whoever is in front of her.
// One that keeps to a single direction (more than 10° off) is turned to face front; one
// that turns about (a spin, a dance) is left as it is.
export function faceFront({ yaw, steady }, rotation, translation) {
  if (!rotation || steady < 0.9 || Math.abs(yaw) < Math.PI / 18) return;
  const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -yaw), q = new THREE.Quaternion(), p = new THREE.Vector3();
  for (let i = 0; i < rotation.values.length; i += 4) q.fromArray(rotation.values, i).premultiply(turn).toArray(rotation.values, i);
  for (let i = 0; translation && i < translation.values.length; i += 3) p.fromArray(translation.values, i).applyQuaternion(turn).toArray(translation.values, i);
  console.log(`turned ${Math.round(-yaw * 180 / Math.PI)}° to face front`);
}
