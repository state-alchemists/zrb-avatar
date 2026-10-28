# tools — getting motion into a `.vrma`

Everything here writes the same thing: a GLB carrying one animation on VRM humanoid bone
names, with no mesh, one 30 fps time line per clip, and the `min`/`max` a consumer reads a
clip's length from. Point any of them at an output folder and the files drop straight into
an animation folder (`~/.zrb/avatar/anim/<pool>/<name>.vrma`).

| Tool | Takes | Needs |
|---|---|---|
| `gltf2vrma.py` | a rigged `.gltf` (+ its `.bin`), `.glb`, or `.vrm` holding animations | python 3, nothing else |
| `fbx2vrma.mjs` | a Mixamo `.fbx` | `npm install` here (three) |
| `bvh2vrma.mjs` | a `.bvh` capture | `npm install` here (three) |
| `vrma-info.mjs` | any `.vrma` — says what a loader makes of it | `npm install` here (three + three-vrm-animation) |

## gltf2vrma.py

```bash
python3 gltf2vrma.py SOURCE OUT_DIR [--rig auto|rigify|mixamo|vrm|vrm-meta] [--tpose NAME] [--list] [CLIP ...]
```

`--rig auto` reads the file's own bones: `DEF-…` is Blender/Rigify, `mixamorig…` is Mixamo,
humanoid names map to themselves, and a `.vrm` is read through its own humanoid map (VRoid
names its bones `J_Bip_C_Hips` and keeps the mapping in the file, so that is the only way).
`--list` prints every bone with what it maps to — the first thing to run on a rig of your
own. `--tpose NAME` names the clip whose pose is the rig's T-pose; without one the rest pose
is assumed to be a T-pose, which is right for Mixamo and wrong for anything A-posed.

## fbx2vrma.mjs and bvh2vrma.mjs

```bash
node fbx2vrma.mjs in.fbx out.vrma "what it looks like"
node bvh2vrma.mjs in.bvh out.vrma "what it looks like" [--bones]
```

Both retarget the same way (three-vrm's `loadMixamoAnimation` approach): each bone's motion
is turned into a rotation from the rest pose in world-aligned axes, so a clip recorded on
another skeleton arrives in the same convention. `--bones` prints what the BVH's bone names
were taken to mean; a rig the table does not know is refused with that list rather than
silently half-converted.

Both guess the unit: a hips height over 10 counts as centimetres. A capture whose first
frame is not a T-pose arrives skewed by however far that frame leans — see the note at the
top of `bvh2vrma.mjs`.

## What a clip needs to look right

- **The source's rest pose must be a T-pose,** or you must pass the clip that is one
  (`--tpose`). A `.vrma` is applied against the destination model's T-pose; motion copied
  from an A-posed rig arrives rotated by the difference.
- **No scale, and translation on the hips only.** The spec forbids the rest, so a converter
  that keeps them is writing something a loader will reject or ignore.
- **Every channel needs the same keyframes.** A clip is one timeline to a consumer; the
  tools here resample each channel onto one 30 fps grid (rotations slerped) rather than
  trusting a source that keys each bone only when it changes.
- **A node must not carry `mesh` or `skin`.** An animation file has no meshes array, so a
  dangling `skin: 0` copied from a character glTF makes `GLTFLoader` throw outright.

`vrma-info.mjs <file.vrma>` answers "will this load, and with what in it" without a
browser: bones, tracks, keyframes, duration, and whether the loader complained.

## Licences

These tools move motion; they do not grant rights to it. A `.vrma` made from a Mixamo FBX
carries Mixamo's terms, from a paid capture its author's, and from a cc0 pack cc0. Record
where each clip came from before shipping it — `../PROVENANCE.md` shows the shape of that
record.
