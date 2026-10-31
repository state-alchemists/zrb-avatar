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
| `headlift.mjs` | a `.vrma` whose head is carried too far forward, and how many degrees to lift it | nothing |
| `restance.py` | a `.vrma` standing wider than it should | `npm install` here (three + three-vrm-animation) |
| `floor.py` | one or more `.vrma`, and the height the library stands at | `npm install` here (three + three-vrm-animation) |
| `vrma-info.mjs` | any `.vrma` — says what a loader makes of it | `npm install` here (three + three-vrm-animation) |
| `author-vrma.py` | a base pose, and the names of the ambient clips to write | python 3, nothing else |
| `author-actions.py` | the same base pose, and the names of the gestures to write | python 3, nothing else |

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
node bvh2vrma.mjs in.bvh out.vrma "what it looks like" [--frames FROM TO] [--bones]
```

Both retarget the same way (three-vrm's `loadMixamoAnimation` approach): each bone's motion
is turned into a rotation from the rest pose in world-aligned axes, so a clip recorded on
another skeleton arrives in the same convention. `--bones` prints what the BVH's bone names
were taken to mean; a rig the table does not know is refused with that list rather than
silently half-converted.

A mocap collection usually ships one file per movement that still holds the leading and
trailing T-poses, and a CSV saying which frames are the clean take — `--frames FROM TO` cuts
to it, and shifts the cut to start at zero. Without it the whole file becomes the clip.

Both guess the unit from a **thigh's length** (BVHLoader leaves the bones at their rest
offsets, so the hips sit at zero and their height says nothing): forty-odd centimetres is a
centimetre capture, 0.4 a metre one. The hips carry a capture's whole trajectory, so only
their motion from the clip's first frame is written — the alternative is a body standing two
metres from where the window is looking. A capture whose first frame is not a T-pose arrives
skewed by however far that frame leans — see the note at the top of `bvh2vrma.mjs`.

100STYLE's rig is spelled out in that file (`STYLE100`) instead of matched by name, because
it uses "shoulder" for the upper arm and "hip" for the thigh.

## headlift.mjs, restance.py, floor.py, and the probes

```bash
node headlift.mjs in.vrma out.vrma -12 [--head-only]   # degrees; positive bows the head
python3 restance.py in.vrma out.vrma [--width 22.3] [--drift]
python3 floor.py --target 8.3 in.vrma [in.vrma ...]     # --report to measure only
```

All three apply one constant to a captured clip rather than re-animating it: `headlift` turns the
neck and head so a capture that was recorded looking down looks at you, `restance` turns the
thighs so a capture that stands too wide stands under her, lifting the hips by what the knees took
up, and `floor.py` moves the hips straight up or down so her feet reach the height the rest of the
library stands at. None is a fix for a pose that is wrong in motion — a constant correction turns
travelling feet into sliding feet, which is what `restance --drift` prints.

`floor.py` is the one that is run over the whole library, because height is the one thing every
source disagrees about. Two of them disagree about the axis as well: `gltf2vrma.py` puts the
body's height in **z** and `bvh2vrma.mjs` in **y**, so the tool reads the axis off each file's
hips node instead of assuming one, applies the shift, measures again, and repeats until nothing is
left. Point it at a folder of candidates and everything in there stands where the shipped clips
stand.

The `_*.mjs` files answer one question each and print it, for judging a clip before shipping
it: `_feet.mjs` where the feet are, how far they travel, and — at the end of each clip's block —
the world height the lowest foot reaches, which is the number `floor.py` reads. `_hands.mjs` says
how high a hand gets and when (a "waving" clip whose hands peak at chest height is not waving),
`_crosscheck.mjs` whether a converted clip stands where its source stands, `headpitch.mjs` how far
forward the head is carried in the source itself.

## author-vrma.py and author-actions.py

```bash
python3 author-vrma.py  <base-vrma-dir> <out-dir> [clip ...]     # IdleBreath, IdleShift, TalkingSoft
python3 author-actions.py <base-vrma-dir> <out-dir> [clip ...]   # Wave, Greeting, Nod, ThumbsUp, ...
```

The other two tools move motion that was captured; these two write motion that was not. Each
takes the pose a real clip already holds — `<base-vrma-dir>/idle/Idle.vrma`, frame 0 — and moves
it by a small explicit amount, so every joint keeps the rotation it had and only the named bones
change. That is why an authored idle still looks like a body and a synthesised one does not.

- `author-vrma.py` writes **loops**: breathing, an eye-line that drifts, a weight shift. Every
  motion is a whole number of cycles across the clip, and the build refuses to write a first and
  last frame that differ — a loop with a seam pops once per cycle, forever.
- `author-actions.py` writes **beats**: `(0.30, {bone: {axis: degrees}})`, interpolated with
  smoothstep so a gesture starts and stops with weight. Naming a channel again is how a hold is
  spelled; *not* naming it eases it back to the base pose, which is why every hand shape is named
  on every beat.

Both print every file they write, and the shipped clips reproduce byte for byte from the
Quaternius conversion — see `../PROVENANCE.md`. Poses are solved, not guessed: a sweep of arm
angles is written out and measured with `_frame.mjs` (where the wrist, elbow and palm land) before
a number goes into a docstring.

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
