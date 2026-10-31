// The BVH pose and the converted clip, side by side: world positions relative to the hips, cm.
import * as THREE from 'three';
import { readFileSync } from 'node:fs';
import { BVHLoader } from 'three/examples/jsm/loaders/BVHLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin } from '@pixiv/three-vrm';
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation';

const [bvhPath, fromFrame, toFrame, seconds, vrmaPath, model] = process.argv.slice(2);
const bvh = new BVHLoader().parse(readFileSync(bvhPath, 'utf8'));
const root = bvh.skeleton.bones[0];
const cut = THREE.AnimationUtils.subclip(bvh.clip, 'cut', Number(fromFrame), Number(toFrame), 60);
const m1 = new THREE.AnimationMixer(root);
m1.clipAction(cut).play();
m1.setTime(Number(seconds));
root.updateMatrixWorld(true);
const find = (name) => bvh.skeleton.bones.find((b) => b.name === name);
const at = (bone, base) => {
  const v = new THREE.Vector3();
  bone.getWorldPosition(v);
  return v.sub(base).toArray().map((x) => +(x * 1).toFixed(1)); // BVH is already cm
};
const hipsB = new THREE.Vector3();
find('Hips').getWorldPosition(hipsB);
const bvhPose = {};
for (const [label, name] of [['head', 'Head'], ['lw', 'LeftWrist'], ['rw', 'RightWrist'], ['lf', 'LeftAnkle'], ['rf', 'RightAnkle']])
  bvhPose[label] = at(find(name), hipsB);
const upOf = (bone) => {
  const q = new THREE.Quaternion();
  bone.getWorldQuaternion(q);
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
  return up.toArray().map((x) => +x.toFixed(3));
};
const bvhUp = upOf(find('Head'));
console.log('BVH  (cm from hips):', JSON.stringify(bvhPose), ' head up', JSON.stringify(bvhUp));

const read = (path) => {
  const buffer = readFileSync(path);
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
};
const loader = new GLTFLoader();
loader.register((p) => new VRMLoaderPlugin(p));
const g = await loader.parseAsync(read(model), '');
const vrm = g.userData.vrm;
const loader2 = new GLTFLoader();
loader2.register((p) => new VRMAnimationLoaderPlugin(p));
const anim = (await loader2.parseAsync(read(vrmaPath), '')).userData.vrmAnimations[0];
const clip = createVRMAnimationClip(anim, vrm);
const m2 = new THREE.AnimationMixer(vrm.scene);
m2.clipAction(clip).play();
m2.setTime(Number(seconds));
vrm.humanoid.update();
vrm.scene.updateMatrixWorld(true);
const bone = (n) => vrm.humanoid.humanBones[n].node;
const hips = new THREE.Vector3(); bone('hips').getWorldPosition(hips);
const pose = {};
for (const [label, name] of [['head', 'head'], ['lw', 'leftHand'], ['rw', 'rightHand'], ['lf', 'leftFoot'], ['rf', 'rightFoot']]) {
  const v = new THREE.Vector3(); bone(name).getWorldPosition(v);
  pose[label] = v.sub(hips).toArray().map((x) => +(x * 100).toFixed(1));
}
const headNode = bone('head');
const qv = new THREE.Quaternion();
headNode.getWorldQuaternion(qv);
const vrmUp = new THREE.Vector3(0, 1, 0).applyQuaternion(qv).toArray().map((x) => +x.toFixed(3));
console.log('VRM  (cm from hips):', JSON.stringify(pose), ' head up', JSON.stringify(vrmUp),
  ' angle to BVH', (new THREE.Vector3(...bvhUp).angleTo(new THREE.Vector3(...vrmUp)) * 180 / Math.PI).toFixed(1) + '°');
for (const k of Object.keys(pose)) {
  const a = new THREE.Vector3(...bvhPose[k]), b = new THREE.Vector3(...pose[k]);
  console.log(`  ${k.padEnd(5)} |bvh| ${a.length().toFixed(1).padStart(6)}  |vrm| ${b.length().toFixed(1).padStart(6)}  ` +
    `angle-between ${(a.angleTo(b) * 180 / Math.PI).toFixed(1)}°`);
}
