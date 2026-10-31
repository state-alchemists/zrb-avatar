// Where are the feet, in the pelvis' own frame, at frame 0 and through the clip?
// Prints side / forward / up per foot, which is what an authored base pose has to correct.
import * as THREE from 'three';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin } from '@pixiv/three-vrm';
import {
  VRMAnimationLoaderPlugin,
  createVRMAnimationClip,
} from '@pixiv/three-vrm-animation';

const [models, ...rest] = process.argv.slice(2);
const drift = rest.includes('--drift');
const clips = rest.filter((arg) => arg !== '--drift');

async function parse(path, register) {
  const loader = new GLTFLoader();
  register?.(loader);
  const buffer = readFileSync(path);
  return loader.parseAsync(
    buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
    ''
  );
}

const chosen = join(models, readdirSync(models).filter((n) => n.endsWith('.vrm')).sort()[0]);
const gltf = await parse(chosen, (l) => l.register((p) => new VRMLoaderPlugin(p)));
const vrm = gltf.userData.vrm;
const mixer = new THREE.AnimationMixer(vrm.scene);
const bone = (name) => vrm.humanoid.humanBones[name]?.node;
const pos = (name) => {
  const n = bone(name);
  if (!n) return null;
  const v = new THREE.Vector3();
  n.getWorldPosition(v);
  return v;
};

function sample() {
  vrm.humanoid.update();
  vrm.scene.updateMatrixWorld(true);
  const hips = pos('hips'), lu = pos('leftUpperLeg'), ru = pos('rightUpperLeg');
  const lf = pos('leftFoot'), rf = pos('rightFoot');
  if (!hips || !lu || !ru || !lf || !rf) return null;
  const left = lu.clone().sub(ru).setY(0).normalize();
  const up = new THREE.Vector3(0, 1, 0);
  const forward = new THREE.Vector3().crossVectors(left, up).normalize();
  const inBody = (v) => {
    const d = v.clone().sub(hips);
    return [d.dot(left), d.dot(forward), d.dot(up)].map((x) => (x * 100).toFixed(1));
  };
  const worldOf = (v) => {
    const d = v.clone().sub(hips);
    return [(d.x * 100).toFixed(1), (d.z * 100).toFixed(1)];   // sideways, fore/aft, fixed axes
  };
  return {
    lf: inBody(lf), rf: inBody(rf),
    // the same two points in world axes: the pelvis frame is built from the thighs, so it turns
    // when they do — and a stance solver that reads it would be moving the ruler
    lw: worldOf(lf), rw: worldOf(rf),
    separation: lf.distanceTo(rf) * 100,
    horizontal: Math.hypot(lf.x - rf.x, lf.z - rf.z) * 100,
  };
}

const cm = (v) => v.toFixed(1).padStart(6);
console.log(`model: ${chosen.split('/').pop()}   (side, forward, up) in cm, pelvis frame`);
for (const path of clips) {
  const file = await parse(path, (l) => l.register((p) => new VRMAnimationLoaderPlugin(p)));
  const animation = file.userData.vrmAnimations?.[0];
  if (!animation) {
    console.log(`${path}: refused`);
    continue;
  }
  const clip = createVRMAnimationClip(animation, vrm);
  mixer.stopAllAction();
  mixer.clipAction(clip).reset().play();
  mixer.setTime(0);
  const s = sample();
  console.log(
    `${path.split('/').pop().padEnd(22)} L foot ${s.lf.join(',')}   R foot ${s.rf.join(',')}   ` +
      `apart ${cm(s.separation)} cm (horizontal ${cm(s.horizontal)})   ` +
      `world L ${s.lw.join(',')} R ${s.rw.join(',')}`
  );
  if (drift) {
    // a stance correction is one constant rotation per thigh, so a clip whose feet travel of
    // their own accord would be corrected into a slide: this is how far they travel
    const times = [0.05, 0.3, 0.55, 0.8, 0.98].map((f) => f * clip.duration);
    let first = null;
    const travel = { L: 0, R: 0 }, apart = [];
    for (const t of times) {
      mixer.setTime(t);
      const frame = sample();
      const L = frame.lf.map(Number), R = frame.rf.map(Number);
      if (!first) first = [L, R];
      travel.L = Math.max(travel.L, Math.hypot(L[0] - first[0][0], L[1] - first[0][1]));
      travel.R = Math.max(travel.R, Math.hypot(R[0] - first[1][0], R[1] - first[1][1]));
      apart.push(frame.separation);
    }
    console.log(
      `  across the clip: feet travel L ${travel.L.toFixed(1)} cm, R ${travel.R.toFixed(1)} cm; ` +
        `apart ${Math.min(...apart).toFixed(1)}..${Math.max(...apart).toFixed(1)} cm`
    );
    mixer.setTime(0);
  }
}
