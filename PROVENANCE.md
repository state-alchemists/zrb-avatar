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
hips), and writes one GLB per clip with the `VRMC_vrm_animation` extension. No
motion is retimed, resampled or edited: the keyframes and their times are copied
as they are.

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

**Not verified: how the clips look on a VRoid model.** Nothing has been loaded
through three-vrm or played in a window yet. That is the next test, and until it
runs, "these retarget cleanly onto a Vroid body" is an expectation, not a fact.

## What this set does not cover

| Pool | Covered by this pack | Missing |
|---|---|---|
| idle | `Idle_Loop`, `Crouch_Idle_Loop`, `Sitting_Idle_Loop` | — |
| speaking | `Idle_Talking_Loop`, `Sitting_Talking_Loop` | — |
| dance | `Dance_Loop` | variation; one clip is one dance |
| actions | `Jump_Start/Loop/Land`, `Interact`, `PickUp_Table`, `Hit_Chest`, `Hit_Head`, `Punch_*`, `Sword_*`, `Pistol_*`, `Spell_*`, `Roll`, `Death01` | **greeting, wave, peace sign, thumbs-up, thinking, telling a secret** |
| attention | — | nothing that says "look here" |
| tool | `Interact`, `PickUp_Table`, `Fixing_Kneeling` | filing cabinet, any prop-specific action |

The gesture gap is the one to plan around: every freely licensable source is
capture of whole bodies, and ours needs hands doing specific things.
