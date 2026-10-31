// Does a clip raise a hand? node _hands.mjs <model.vrm> <clip.vrma>...
//
// Samples the clip and prints, per hand, the highest it gets above the hips and when -- the
// question being whether a captured "waving" style actually waves, or only stands in a pose that
// its author called waving. A hand at shoulder height is about +45 cm above the hips on this
// model; below that it is hanging or gesturing at waist level.
import * as THREE from 'three';
import { readFileSync } from 'node:fs';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin } from '@pixiv/three-vrm';
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation';

const [model, ...clips] = process.argv.slice(2);
const read = (path) => {
  const buffer = readFileSync(path);
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
};
const load = async (path, register) => {
  const loader = new GLTFLoader();
  register(loader);
  return loader.parseAsync(read(path), '');
};

const vrm = (await load(model, (l) => l.register((p) => new VRMLoaderPlugin(p)))).userData.vrm;
const mixer = new THREE.AnimationMixer(vrm.scene);
const bone = (name) => vrm.humanoid.humanBones[name]?.node;
const height = (name, base) => {
  const v = new THREE.Vector3();
  bone(name).getWorldPosition(v);
  return (v.y - base.y) * 100;
};

for (const path of clips) {
  const animation = (await load(path, (l) => l.register((p) => new VRMAnimationLoaderPlugin(p))))
    .userData.vrmAnimations?.[0];
  if (!animation) {
    console.log(`${path}: refused`);
    continue;
  }
  const clip = createVRMAnimationClip(animation, vrm);
  mixer.stopAllAction();
  mixer.clipAction(clip).reset().play();
  const best = { leftHand: [-Infinity, 0], rightHand: [-Infinity, 0] };
  const steps = 60;
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * clip.duration * 0.999;
    mixer.setTime(t);
    vrm.humanoid.update();
    vrm.scene.updateMatrixWorld(true);
    const hips = new THREE.Vector3();
    bone('hips').getWorldPosition(hips);
    for (const hand of ['leftHand', 'rightHand']) {
      const h = height(hand, hips);
      if (h > best[hand][0]) best[hand] = [h, t];
    }
  }
  const cm = (v) => v.toFixed(0).padStart(4);
  console.log(
    `${path.split('/').pop().padEnd(22)} ${clip.duration.toFixed(1)}s  ` +
      `L peak ${cm(best.leftHand[0])} cm at ${best.leftHand[1].toFixed(1)}s   ` +
      `R peak ${cm(best.rightHand[0])} cm at ${best.rightHand[1].toFixed(1)}s`
  );
}
