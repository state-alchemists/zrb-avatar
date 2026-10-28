// Load a .vrma through the same library the avatar app animates with, and
// report what a retargeter actually sees. No browser, no WebGL: parsing needs
// neither, and this is the check a third-party viewer refuses to explain.
//
// Usage: node parse.mjs <dir-of-vrma> [file.vrma]
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMAnimationLoaderPlugin } from "@pixiv/three-vrm-animation";

const dir = process.argv[2];
const only = process.argv[3];
const files = readdirSync(dir)
  .filter((n) => n.endsWith(".vrma") && (!only || n === only))
  .sort();

function parse(path) {
  const buffer = readFileSync(path);
  return new Promise((resolve, reject) => {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
    loader.parse(
      buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
      "",
      resolve,
      reject
    );
  });
}

function finite(track, name) {
  for (const value of track.values) {
    if (!Number.isFinite(value)) throw new Error(`NaN in ${name}`);
  }
}

let ok = 0;
const problems = [];
for (const name of files) {
  try {
    const gltf = await parse(join(dir, name));
    const [animation] = gltf.userData.vrmAnimations ?? [];
    if (!animation) throw new Error("no vrmAnimation — no VRMC_vrm_animation found");
    const rotation = animation.humanoidTracks?.rotation ?? new Map();
    const translation = animation.humanoidTracks?.translation ?? new Map();
    for (const [bone, track] of rotation) finite(track, bone);
    for (const [bone, track] of translation) finite(track, "hips");

    const keys = [...rotation.values()][0]?.times.length ?? 0;
    const uneven = new Set([...rotation.values()].map((t) => t.times.length));
    console.log(
      `${name.padEnd(30)} ok  rot=${String(rotation.size).padStart(2)} ` +
        `trans=${translation.size}  ${animation.duration.toFixed(2)}s  ` +
        `${keys} keys${uneven.size > 1 ? ` (UNEQUAL ${[...uneven].join("/")})` : ""}`
    );
    ok++;
  } catch (error) {
    problems.push(`${name}: ${error.message}`);
    console.log(`${name.padEnd(30)} FAIL  ${error.message}`);
  }
}
console.log(`\n${ok}/${files.length} loaded by three-vrm-animation ${problems.length ? `— ${problems.length} failed` : ""}`);
