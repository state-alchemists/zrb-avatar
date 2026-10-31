// Animations: anim/<pool>/<name>.vrma, from every animation folder, as clips for this model.
// three-vrm-animation retargets them onto any VRM, so no per-model pose code is needed.
import * as THREE from 'three';
import { createVRMAnimationClip } from '@pixiv/three-vrm-animation';

// three-vrm-animation reads only a VRMC_vrm_animation whose specVersion is "1.0" or
// "1.0-draft", and drops anything else -- no clip, a line in the console nobody reads. Several
// exporters leave the field out altogether. Those files are in metres, like "1.0", so stamping
// the version is all they need; the bin chunk, where the tracks live, is copied untouched.
const SPEC_VERSIONS = ['1.0', '1.0-draft'];
export function stampedAnimation(buffer) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546C67) return buffer; // not a glb
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4E4F534A) return buffer; // first chunk is not the json
  const json = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)));
  const animation = json.extensions?.VRMC_vrm_animation;
  if (!animation || SPEC_VERSIONS.includes(animation.specVersion)) return buffer;
  if (animation.specVersion) {
    // "0" is VRM 0.x: centimetres and its own retargeting, which is a different job from this.
    console.warn('animation: specVersion', animation.specVersion, 'is not 1.0, so this clip is skipped');
    return buffer;
  }
  animation.specVersion = '1.0'; // left out by the exporter; these positions are in metres, like 1.0
  if (!(json.extensionsUsed ??= []).includes('VRMC_vrm_animation')) json.extensionsUsed.push('VRMC_vrm_animation');
  const text = new TextEncoder().encode(JSON.stringify(json));
  const padded = (text.length + 3) & ~3; // the json chunk pads to four bytes with spaces
  const bin = bytes.subarray(20 + jsonLength);
  const out = new Uint8Array(20 + padded + bin.length);
  out.set(bytes.subarray(0, 12)); // magic and version
  const head = new DataView(out.buffer);
  head.setUint32(8, out.length, true);
  head.setUint32(12, padded, true);
  head.setUint32(16, 0x4E4F534A, true);
  out.set(text, 20);
  out.fill(0x20, 20 + text.length, 20 + padded);
  out.set(bin, 20 + padded);
  return out.buffer;
}

// The clip starting where she stands: some are recorded off to one side (idle/Idle stands 17 cm
// to her right the whole time), and between such a clip, a centred one and its own mirror she
// slid across the floor. The hips keep every step of their own, from her spot.
export function centred(clip, vrm) {
  const hips = vrm.humanoid.getNormalizedBoneNode('hips')?.name;
  const track = clip.tracks.find(t => t.name === `${hips}.position`);
  if (!track) return clip;
  const [x, , z] = track.values;
  for (let i = 0; i < track.values.length; i += 3) { track.values[i] -= x; track.values[i + 2] -= z; }
  return clip;
}

// The clip in a mirror: left and right bones (and blinkLeft/Right, lookLeft/Right) trade tracks,
// every turn goes the other way, and the hips move the other way. Exact, as the tracks drive the
// normalized bones, whose rest pose is unrotated; free but for the tracks' memory.
export function mirrored(clip, vrm) {
  const other = new Map(); // normalized bone node name -> its other side's
  for (const bone of Object.keys(vrm.humanoid.humanBones)) {
    const side = bone.replace(/^(left|right)/, s => s === 'left' ? 'right' : 'left');
    const a = vrm.humanoid.getNormalizedBoneNode(bone)?.name, b = vrm.humanoid.getNormalizedBoneNode(side)?.name;
    if (a && b) other.set(a, b);
  }
  return new THREE.AnimationClip(`${clip.name}-mirror`, clip.duration, clip.tracks.map(track => {
    const t = track.clone(), dot = t.name.lastIndexOf('.'), target = t.name.slice(0, dot), property = t.name.slice(dot + 1);
    t.name = `${other.get(target) ?? target.replace(/(Left|Right)$/, s => s === 'Left' ? 'Right' : 'Left')}.${property}`;
    if (property === 'quaternion') for (let i = 0; i < t.values.length; i += 4) { t.values[i + 1] *= -1; t.values[i + 2] *= -1; }
    if (property === 'position') for (let i = 0; i < t.values.length; i += 3) t.values[i] *= -1;
    return t;
  }));
}

// Every animation in the folders, fetched and parsed once: [{pool, name, animation}]. Pools nest:
// actions/greetings/hi plays as the action "greetings/hi", and idle/calm/sway loops with the
// idles as "calm/sway" (its folder a group of its own, see motion.js).
export async function loadAnimations(loader) {
  const pools = await fetch('anims').then(r => r.json()).catch(() => ({}));
  const found = await Promise.all(Object.entries(pools).flatMap(([pool, names]) => names.map(async name => {
    const url = `anim/${pool.split('/').map(encodeURIComponent).join('/')}/${encodeURIComponent(name)}.vrma`;
    try {
      const gltf = await loader.parseAsync(stampedAnimation(await fetch(url).then(r => r.arrayBuffer())), '');
      // No vrmAnimations means the loader refused the file; say which, instead of failing on [0].
      const animation = gltf.userData.vrmAnimations?.[0];
      if (animation) return { pool, name, animation };
      console.warn('animation', pool, name, 'is not a VRM animation the loader reads');
    } catch (e) { console.warn('animation', pool, name, e); }
    return null;
  })));
  return found.filter(Boolean);
}

// The animations as clips for this model: pool -> name -> clip, and clip -> "pool/name" to say
// what is playing. Each clip also comes as "<name>-mirror", in its group, so loops vary twice as
// much without playing any one more often. Retargeting is quick (all of them in tens of ms), so a
// new model needs no new download.
export function clipsFor(animations, vrm) {
  const clips = {}, clipName = new Map();
  for (const { pool, name, animation } of animations) {
    const [top, ...sub] = pool.split('/'), key = [...sub, name].join('/');
    const clip = (clips[top] ??= {})[key] = centred(createVRMAnimationClip(animation, vrm), vrm);
    clipName.set(clip, `${pool}/${name}`);
    clipName.set(clips[top][`${key}-mirror`] = mirrored(clip, vrm), `${pool}/${name}-mirror`);
  }
  return { clips, clipName };
}

// One of a pool's clips, at random, mirrors included: a folder can hold several of something --
// a greeting, a cover -- and the moment asks for the folder rather than for one file, so a folder
// of three greetings greets differently each time. `prefix` narrows it to a subfolder, which is
// how `cover/chest` asks for the covers made for her chest.
function anyOf(pool, prefix = '') {
  const keys = Object.keys(pool).filter(k => !prefix || k === prefix || k.startsWith(`${prefix}/`));
  return keys.length ? pool[keys[Math.floor(Math.random() * keys.length)]] : null;
}

// A clip to play once: any pool's by its full path ("thinking/idea", "actions/greetings/hi"),
// which wins; else a pool by that name ("greeting", "cover/chest"), where the clips of a moment
// live; else an action by its short name ("wave", "greetings/hi"). The server's playable() resolves
// names the same way, so actions/thinking/idea never hides thinking/idea. Half the time it is the
// mirror one: a wave with either hand. Last, `actions/Greeting`: what the README told you to call a
// greeting before there was a folder for it, so one dropped there still plays.
export function clipByName(clips, value) {
  const path = String(value), [top, ...rest] = path.split('/');
  const found = (pool, key) => Object.hasOwn(clips, pool) && Object.hasOwn(clips[pool], key)
    ? (Math.random() < 0.5 && clips[pool][`${key}-mirror`]) || clips[pool][key] : null;
  const pool = Object.hasOwn(clips, top) ? anyOf(clips[top], rest.join('/')) : null;
  return (rest.length ? found(top, rest.join('/')) : null) ?? pool ?? found('actions', path)
    ?? found('actions', path.charAt(0).toUpperCase() + path.slice(1));
}
