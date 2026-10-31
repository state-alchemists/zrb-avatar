// How far forward is the head carried, in the source itself? node headpitch.mjs <in.bvh> FROM
//   TO <seconds>
//
// The world up-axis and forward pitch of the hips, top of the chest, neck and head of a BVH at
// one instant. BVH joints carry no rest rotation, so their local axes are world axes and this
// is the capture's own posture with nothing converted in between.
import * as THREE from 'three';
import { readFileSync } from 'node:fs';
import { BVHLoader } from 'three/examples/jsm/loaders/BVHLoader.js';
const [path, from, to, seconds] = process.argv.slice(2);
const bvh = new BVHLoader().parse(readFileSync(path, 'utf8'));
const root = bvh.skeleton.bones[0];
const cut = THREE.AnimationUtils.subclip(bvh.clip, 'c', Number(from), Number(to), 60);
const m = new THREE.AnimationMixer(root); m.clipAction(cut).play(); m.setTime(Number(seconds));
root.updateMatrixWorld(true);
for (const name of ['Hips', 'Chest4', 'Neck', 'Head']) {
  const b = bvh.skeleton.bones.find(x => x.name === name);
  const q = new THREE.Quaternion(); b.getWorldQuaternion(q);
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
  const fwd = new THREE.Vector3(0, 0, 1).applyQuaternion(q);
  const pitch = Math.asin(THREE.MathUtils.clamp(fwd.y, -1, 1)) * 180 / Math.PI;
  console.log(`${name.padEnd(8)} up ${up.toArray().map(v => v.toFixed(2)).join(',')}  forward-pitch ${pitch.toFixed(1)}°`);
}
