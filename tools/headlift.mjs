// Lift a captured head so she looks at you: node headlift.mjs <in.vrma> <out.vrma> <degrees> [--head-only]
//
// 100STYLE was captured with the actor carrying his head about 28 degrees forward, in the source
// itself (measured with headpitch.mjs), so every clip from it arrives looking at the floor. That
// is the capture, not a conversion fault -- and it is a constant, which means it can be taken out
// with a constant instead of by re-animating anything: one extra rotation on the neck, and the
// same on the head when the whole chain has to come up together.
//
// Positive degrees pitch the head up. The rotation is written into the existing keys of the neck
// and head channels, so the clip keeps its own timing, its own count of keys, and its length.
import fs from 'fs';

const [input, output, rawDegrees, flag] = process.argv.slice(2);
if (!input || !output || rawDegrees === undefined) {
  console.error('usage: node headlift.mjs <in.vrma> <out.vrma> <degrees> [--head-only]');
  process.exit(2);
}
const degrees = Number(rawDegrees);
const bones = flag === '--head-only' ? ['head'] : ['neck', 'head'];

const glb = fs.readFileSync(input);
const jsonLength = glb.readUInt32LE(12);
const doc = JSON.parse(glb.slice(20, 20 + jsonLength).toString('utf8'));
const binStart = 20 + jsonLength + 8;

const humanoid = doc.extensions?.VRMC_vrm_animation?.humanoid?.humanBones ?? {};
const nodesOf = {};
for (const [bone, entry] of Object.entries(humanoid)) nodesOf[entry.node] = bone;

// A half-turn about the bone's local X: for the humanoid neck and head that is the bend forward
// and back, which is the axis `author-vrma.py` drives with `{"neck": {"x": ...}}`.
const half = (degrees * Math.PI) / 180 / 2;
const q = [Math.sin(half), 0, 0, Math.cos(half)];
const times = (a, b) =>
  [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
   a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
   a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
   a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];

let touched = 0;
for (const channel of doc.animations[0].channels) {
  const bone = nodesOf[channel.target.node];
  if (!bones.includes(bone) || channel.target.path !== 'rotation') continue;
  const accessor = doc.accessors[doc.animations[0].samplers[channel.sampler].output];
  const view = doc.bufferViews[accessor.bufferView];
  const at = binStart + (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
  for (let i = 0; i < accessor.count; i++) {
    const k = at + i * 16;
    const current = [glb.readFloatLE(k), glb.readFloatLE(k + 4), glb.readFloatLE(k + 8), glb.readFloatLE(k + 12)];
    const lifted = times(q, current);
    for (let c = 0; c < 4; c++) glb.writeFloatLE(lifted[c], k + c * 4);
  }
  touched += 1;
}
if (!touched) throw new Error(`no rotation channel for ${bones.join('/')} in this clip`);
fs.writeFileSync(output, glb);
console.log(`wrote ${output}: ${degrees}° on ${bones.join(' + ')} (${touched} channels)`);
