# PROVENANCE — clips for zrb-avatar

## What is in `out/`

46 `.vrma` files converted from **Quaternius Universal Animation Library**
(free Standard edition), which is published under **CC0 1.0 Universal**.

| | |
|---|---|
| Upstream pack | <https://quaternius.com/packs/universalanimationlibrary.html> — "Free to use in personal, educational and commercial projects. (CC0 License)" |
| Distribution used | <https://github.com/J-Ponzo/gltf-universal-animation-library> — a mirror of the free itch.io edition as distributed 2025-06-10, glTF only |
| Licence text | `quaternius-ual/LICENSE` — the full CC0 1.0 Universal legal code, verbatim |
| Source file | `quaternius-ual/glTF/AnimationLibrary_Godot_Standard.gltf` + `.bin` (46 clips, one file) |
| Converted | 2026-10-28, by `gltf2vrma.py` in this folder |
| Attribution required | none — CC0 asks for none, and this file records the source anyway |

## What the conversion changes, and what it does not

`gltf2vrma.py` renames the rig onto VRM humanoid bone names, drops every channel
the VRM Animation spec does not allow (scale everywhere, translation outside the
hips), and writes one GLB per clip with the `VRMC_vrm_animation` extension.

It does change the timing in two ways, both forced by what a consumer requires:

- **One time line per clip.** The source keys each bone only when it changes, so
  channels carried different keyframe counts; the app asserts one count across a
  clip. Every channel is resampled onto a single 30 fps grid, rotations with slerp
  along the shorter arc, positions linearly.
- **`min`/`max` on each sampler input.** glTF requires them, and a clip's length is
  read straight off them — without them the server reported `seconds: None`.

Nodes keep only `name`, transform and `children`: the source is a character glTF,
so its mannequin node otherwise drags in `mesh` and `skin` references the animation
file has no arrays for (see the verification section).

The rig is a Blender/Rigify export (`DEF-` bones, three joints per finger). Its
rest pose was measured against the pack's own `A_TPose` clip and found to be a
T-pose already — 53 bones compared, zero differed — so no pose offset is applied.
That matters because a `.vrma` is applied against the destination model's T-pose;
had the source been A-posed, copying rotations across would have arrived skewed.

## What is verified, and what is not

Verified on 2026-10-28:

- 46 clips converted, 2.6 MB total, none failing a structural read.
- Each carries 52 humanoid bones including 30 finger bones, one animation, and a
  non-zero duration read from its own keyframes.
- The extension is present with `specVersion: "1.0"` and a `humanBones` map.

Verified on 2026-10-29, in the library the avatar app animates with
(`@pixiv/three-vrm-animation` 3.5.5 over `three` 0.169.0, `harness/parse.mjs`):
all 46 files parse, each with 52 rotation tracks plus one hips translation track,
equal keyframe counts within a clip, and no NaN in any track.

That check caught a real defect the earlier structural read missed: the nodes were
copied straight from the source character glTF, so the mannequin node kept
`{"mesh": 0, "skin": 0}` while the file carries no `meshes` or `skins` array — a
dangling reference that makes three's GLTFLoader throw outright and makes a
validator reject the file. Three-dimensional Retarget's online viewer refused every
clip for that reason. The converter now keeps only name, transform and children on
a node.

**Verified by eye on 2026-10-29**, by Go in <https://3dretarget.com/vrm-viewer>
with `tara-bamboo.vrm`: the clips play and the retarget looks right. The one thing
that looked wrong was not the conversion — the seated clips float, because a
sitting pose needs a chair the window does not have. Their hips start 41% below
rest height, measured with `harness/hips.mjs`, against 4% for a standing idle. So
no seated clip is shipped; they stay in the converted set for whenever the window
grows a chair.

**Not verified:** how the clips look on the other nineteen models, and frame-by-frame
smoothness — the judgment so far is real eyes on one model, not a measurement.

## What this set does not cover

| Pool | Covered by this pack | Missing |
|---|---|---|
| idle | `Idle_Loop` | — |
| speaking | `Idle_Talking_Loop` | — |
| thinking | `Idle_Loop` again, on purpose | anything that reads as thinking |
| dance | `Dance_Loop` | variation; one clip is one dance |
| actions | `Jump_Start/Loop/Land`, `Interact`, `PickUp_Table`, `Hit_Chest`, `Hit_Head`, `Punch_*`, `Sword_*`, `Pistol_*`, `Spell_*`, `Roll`, `Death01` | **greeting, wave, peace sign, thumbs-up, telling a secret** |
| attention | `Interact` (an approximation) | nothing that says "look here" |
| tool | `Fixing_Kneeling` | filing cabinet, any prop-specific action |

Every pool above loops for as long as its state lasts, bar `actions`, which play once
over a loop. That is what decided the set: a pool's clip is not a gesture, it is a
posture held for seconds, so a clip that only makes sense as a one-off belongs in
`actions` however good it looks once.

Excluded for that reason: every seated clip (`Sitting_Idle_Loop`, `Sitting_Talking_Loop`,
`Sitting_Enter/Exit`, `Driving_Loop`) floats without a chair, and `Crouch_Idle_Loop` and
`PickUp_Table` became loops of crouching and grabbing, so the first is not shipped and the
second moved to `actions`. `Idle_Torch_Loop` is also out: it holds a torch there is no
model for, and thinking is a state visited on every tool call, where mime reads worse than
stillness — so `thinking/Thinking.vrma` is `Idle_Loop` byte for byte, and the pool exists
only so the state has a clip at all.

Props are the same problem in another form: the pack animates hands *as if* holding a
sword, pistol or torch, and none of those objects exist in the window. `Fixing_Kneeling`
is the one shipped on inference rather than sight: its name says kneeling, while its hips
sit at standing height (4% below rest, the same as `Idle_Loop`), so it should read as
hands busy at waist height — worth one look before it becomes a default.

The gesture gap is the one to plan around: every freely licensable source is
capture of whole bodies, and ours needs hands doing specific things.
