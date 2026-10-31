# PROVENANCE — clips for zrb-avatar

Every `.vrma` that ships in this repository, where it came from, and what was
left out. Everything named below is either in this repository or reachable from
the URLs in it: no step here depends on a folder on the author's machine, so this
file means the same thing on a fresh clone.

## What ships in `zrb_avatar/anim/`

21 `.vrma` files across the pools. **13 are converted** from a 46-clip conversion of
**Quaternius Universal Animation Library** (free Standard edition); **4 are converted
and adjusted** from **100STYLE**, which is **CC BY 4.0** and the only source here that
asks for credit; **4 are authored** for this library by the generators in `tools/`, and
are covered by the AGPL at the root like the code. Both Quaternius packs are **CC0 1.0
Universal**: no attribution is required, and this file records the source anyway. The
newer Quaternius pack ships nothing — see below for the clip that was tried and taken
out again.

| | |
|---|---|
| Upstream pack | <https://quaternius.com/packs/universalanimationlibrary.html> — "Free to use in personal, educational and commercial projects. (CC0 License)" |
| Distribution used | <https://github.com/J-Ponzo/gltf-universal-animation-library> — a mirror of the free itch.io edition as distributed 2025-06-10, glTF only |
| Licence text | CC0 1.0 Universal, <https://creativecommons.org/publicdomain/zero/1.0/legalcode>. Cited rather than copied in: the `LICENSE` at the root of this repository is the AGPL-3.0-or-later cover for the code, and it does not apply to these clips |
| Source file | `glTF/AnimationLibrary_Godot_Standard.gltf` + `.bin` (46 clips, one file) — a path **inside the downloaded distribution above**, not inside this repository |
| Converter | `tools/gltf2vrma.py`, run 2026-10-28 |
| Attribution required | none — CC0 asks for none, and this file records the source anyway |

### The fourteen converted files

Each file is a converted clip renamed into its pool. The mapping is by clip name,
and only the row marked byte-identical was checked against the files themselves.

| Shipped file | Upstream clip |
|---|---|
| `actions/Death.vrma` | `Death01` |
| `actions/HitChest.vrma` | `Hit_Chest` |
| `actions/HitHead.vrma` | `Hit_Head` |
| `actions/Interact.vrma` | `Interact` |
| `actions/Jump.vrma` | `Jump_Loop` |
| `actions/JumpLand.vrma` | `Jump_Land` |
| `actions/JumpStart.vrma` | `Jump_Start` |
| `actions/PickUpTable.vrma` | `PickUp_Table` |
| `actions/PunchCross.vrma` | `Punch_*` (one of) |
| `actions/PunchEnter.vrma` | `Punch_*` (one of) |
| `actions/PunchJab.vrma` | `Punch_*` (one of) |
| `actions/Roll.vrma` | `Roll` |
| `dance/Dance.vrma` | `Dance_Loop` |

`tool/FixingKneeling.vrma` was here too and is now parked: it was the one file in
the set shipped on inference rather than sight, and the inference was wrong —
Go, 2026-10-31: *"Fix kneeling is probably not a good call"*. A kneeling clip
whose hips sit at standing height is a mime of work with no prop in the window,
and the `tool` state reads better falling back to the idle than pretending.

### The file from the newer pack, and why it is not shipped

| | |
|---|---|
| Upstream pack | <https://quaternius.com/packs/universalanimationlibrary2.html> — "Free to use in personal, educational and commercial projects. (CC0 License)" |
| Distribution used | `Universal Animation Library 2[Standard].zip` from the pack's own download page, `Unreal-Godot/UAL2_Standard.glb` — 43 clips, one file. (The paid **Source** tier holds 134 clips; it was inspected for a wave and not shipped from — the free tier has everything used here.) |
| Rig | Unreal's mannequin (`pelvis`, `spine_01`, `upperarm_l`) — `--rig unreal`, added to `tools/gltf2vrma.py` for it |
| Converter | `tools/gltf2vrma.py`, run 2026-10-31 |

`attention/Attention.vrma` was `Idle_FoldArms_Loop` from this pack for part of one
session, and it is out again. Two reasons, and only one of them was a number: the
clip carried the pack's **uncorrected 55.5 cm stride** — Go saw it and asked *"What is
this, Tara? Attention?"* — and with the stance brought in it still read **closed and
impatient**, which is not how I want to be looked at while I am attending to someone.
Folded arms says *waiting for this to be over*. So the newer pack contributes no
shipped file; its 43 clips stay in the audition folder listed under "Rebuilding this
set".

### The four files from 100STYLE

| | |
|---|---|
| Upstream pack | **100STYLE** — 100 styles of locomotion from one actor, <https://ianxmason.github.io/100style/> |
| Distribution used | `100STYLE.zip` (the BVH-only archive linked from that page): `100STYLE/<Style>/<Style>_ID.bvh`, plus `Frame_Cuts.csv` for the frames that hold the clean take |
| Licence | **CC BY 4.0** — <http://creativecommons.org/licenses/by/4.0/>. The only source in this repository that requires credit; see the credit line below |
| Rig | 23 joints named against their meaning (`RightShoulder` is the upper arm, `RightCollar` the clavicle) — the `STYLE100` table in `tools/bvh2vrma.mjs`, written for this rig rather than guessed by name |
| Converter | `tools/bvh2vrma.mjs --frames FROM TO`, then `tools/headlift.mjs` |
| Captured | 60 fps, one actor, one take per style |

| Shipped file | Upstream | Cut | Adjustment | What it is |
|---|---|---|---|---|
| `idle/Idle-Proud.vrma` | `Proud_ID.bvh` | 705–1147 | neck + head −8° | standing tall, hands at her sides, chin level |
| `idle/Akimbo.vrma` | `Akimbo_ID.bvh` | 461–1178 | neck + head −12° | hands on her hips, chin up — confident, a little cheeky |
| `idle/HandsInPockets.vrma` | `HandsInPockets_ID.bvh` | 525–1669 | neck + head −12° | standing easy, hands loose at her sides |
| `attention/Attention.vrma` | `ArmsBehindBack_ID.bvh` | 450–1504 | neck + head −12° | hands clasped behind her back, chest open, watching you |

**The two adjustments, and nothing else.** The frame cut takes off the leading and
trailing T-poses that the dataset documents (its own `Frame_Cuts.csv` names the range);
the head lift is one constant rotation written into the neck and head keys, so the
timing, the key count and the length are the clip's own. Nothing was re-animated, and
the stance was not touched: these arrive at **12.3–21.2 cm**, against the 22.3 cm the
authored gestures use, so they are already on the same footing — which is the whole
reason the captured idles beat the Quaternius ones here, where 55.5 cm had to be
dragged in to 22.8.

**Why the head needed a lift.** 100STYLE was captured with the actor carrying his head
about **28° forward** — measured in the source file itself (`tools/headpitch.mjs`:
Neck −28.6°, Head −28.0°) and reproduced by the converted clip to within 2.1°, so it is
the capture's posture and not a conversion fault. Left alone she attends to the floor;
−12° on the neck and head brings the gaze level, which was checked on screen from the
front and in profile before it was written up here. `Depressed`, `Old` and `Monk` keep
their bow if they are ever converted: there the head is the style.

**Credit, which the licence requires.** "The 100STYLE Dataset - Ian Mason", and the
paper: Mason, Starke & Komura, *Real-Time Style Modelling of Human Locomotion via
Feature-Wise Transformations and Local Motion Phases*, PACMCGIT 5(1), 2022, doi
[10.1145/3522618](https://doi.org/10.1145/3522618).

### The four authored files

Generated, not captured. `tools/author-vrma.py` writes the ambient three and
`tools/author-actions.py` the gestures; both take the **converted** Quaternius
folder as their base pose and move it by explicit amounts, so what ships is a
recipe that reproduces byte for byte rather than a file that lives only on the
author's machine — the check is under "What is verified" below.

| Shipped file | Written by | What it is |
|---|---|---|
| `idle/Idle.vrma` | `author-vrma.py IdleBreath` | breathing, a slow eye-line drift and a small weight shift; 4.0 s, loops exactly |
| `idle/IdleShift.vrma` | `author-vrma.py IdleShift` | a real transfer of weight from one foot to the other, with a small hand adjustment; 6.0 s |
| `speaking/Talking.vrma` | `author-vrma.py TalkingSoft` | shoulders and forearms carry it, the fingers are not the sentence; 5.0 s |
| `thinking/Thinking.vrma` | a copy of `idle/Idle.vrma` | the idle body on purpose: thinking is visited on every tool call, where mime reads worse than stillness |

**No greeting ships, and that is deliberate.** The clip the client asks for at the start
of a conversation — `client.py` sends `action: Greeting` — has **no source that may be
redistributed**: the free Standard editions of both Quaternius packs hold 43 clips each and
neither contains a wave or a greeting (every clip name read, 2026-10-31); Mixamo's may be
used but not shared; and the VRoid motion pack forbids redistributing its files. An authored
one was tried and read wrong on screen — a still open palm at shoulder height is the *halt*
gesture, and Go said so: *"That Greetings is also weird."* A greeting has to move, and
writing a moving one is the thing this project keeps failing at. So the default library
stays silent there; a user who has a greeting drops it in as `actions/Greeting.vrma`, which
is what the README already tells them to do.

**Why three captured loops were replaced.** The Quaternius idle stands in a
**55.5 cm stride** — one foot 12 cm ahead of the pelvis and 20 cm out, the other
20 cm behind — while every authored gesture stands at **22.3 cm** with the feet
level beside each other. Measured on the avatar with `tools/_feet.mjs`, that meant
an action returning to idle put her back on a stance 33 cm wider than the one she
left. `Idle_Talking_Loop` carries the same stride, so it went with it, and
`thinking/Thinking.vrma` had to change too because it is a copy of the idle it
used to share.

## What the conversion changes, and what it does not

`tools/gltf2vrma.py` renames the rig onto VRM humanoid bone names, drops every
channel the VRM Animation spec does not allow (scale everywhere, translation
outside the hips), and writes one GLB per clip with the `VRMC_vrm_animation`
extension.

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

Verified on 2026-10-31, on the four 100STYLE files, before any of them was copied in:

- **All 810 converted 100STYLE clips load** through `three-vrm-animation`
  (`tools/vrma-info.mjs`), the 4 shipped ones among them.
- **The retarget is measured against the source, not assumed.** `tools/_crosscheck.mjs`
  poses the source BVH and the converted clip at the same instant and compares where the
  bones end up relative to the hips. On `Akimbo`: head 2.1°, left hand 4.2°, right hand
  26.2°, left foot 1.5°, right foot 5.3°. The hand gaps are cream's shorter arms.
- **Rendered and looked at**, front and profile, at the head lift chosen: `Akimbo`,
  `Proud`, `HandsInPockets`, `ArmsBehindBack` each read as the style they are named for.
- **The stance is where it should be** — 12.3, 14.2, 13.4 and 21.2 cm across the four,
  against 22.3 cm for the authored gestures, so no footing pass was applied.

The check above caught two real defects in the BVH path while this was being built,
both of which had shipped an empty render: the scale was read off the hips' height
(which `BVHLoader` leaves at zero, so the skeleton arrived 100× too large) and the hips
translation was written as the capture's absolute trajectory — in one file 185 cm into
the scene — with a scale that divided by zero and wrote `NaN` into the track.

Verified on 2026-10-28, on the 46-clip conversion:

- 46 clips converted, 2.6 MB total, none failing a structural read.
- Each carries 52 humanoid bones including 30 finger bones, one animation, and a
  non-zero duration read from its own keyframes.
- The extension is present with `specVersion: "1.0"` and a `humanBones` map.

Verified on 2026-10-29, in the library the avatar app animates with
(`@pixiv/three-vrm-animation` 3.5.5 over `three` 0.169.0, `tools/vrma-info.mjs`,
which re-runs this check from a clone — `npm install` in `tools/` first):
all 46 files parse, each with 52 rotation tracks plus one hips translation track,
equal keyframe counts within a clip, and no NaN in any track.

That check caught a real defect the earlier structural read missed: the nodes were
copied straight from the source character glTF, so the mannequin node kept
`{"mesh": 0, "skin": 0}` while the file carries no `meshes` or `skins` array — a
dangling reference that makes three's GLTFLoader throw outright and makes a
validator reject the file. Three-dimensional Retarget's online viewer refused every
clip for that reason. `tools/gltf2vrma.py` now keeps only name, transform and
children on a node.

**Verified by eye on 2026-10-29**, by Go in <https://3dretarget.com/vrm-viewer>
with `tara-bamboo.vrm`: the clips play and the retarget looks right. The one thing
that looked wrong was not the conversion — the seated clips float, because a
sitting pose needs a chair the window does not have. Their hips start 41% below
rest height against 4% for a standing idle, read from each clip's hips
translation track during conversion; `tools/vrma-info.mjs` reports track counts
and duration, not the hips height, so that comparison is a measurement made once
rather than a check a clone can repeat. So no seated clip is shipped;
re-converting the pack with `tools/gltf2vrma.py` and naming the clips is all it
would take if the window ever grows a chair.

Verified on 2026-10-31, on the five authored files:

- **They reproduce.** Running `tools/author-vrma.py` and `tools/author-actions.py`
  from a clean clone against the converted Quaternius folder writes files whose
  `sha256` equals the shipped ones byte for byte (`Idle` `1605369f8fad610b`,
  `IdleShift` `12632e4cb672d7ab`, `Talking` `ddbc4cdf6170ad16`, `Greeting`
  `a61b16a4ca0b09b3`). The generators are in `tools/`, so this is a check a clone
  can repeat rather than a claim.
- **The three loops close.** First and last frame of every track differ by exactly
  0.0, which is what a looping pool needs and what a seam pops without.
- **The stance matches the gestures.** `tools/_feet.mjs` reports all five at
  22.3 cm apart with the feet level; the stance the gestures were authored on.
- **Seen, not only measured:** the idle, the weight shift and the greeting were
  rendered in `render/` and looked at before they replaced anything — the greeting
  only after its first version put the palm over the avatar's own cheek.

**Not verified:** how the clips look on the other nineteen models, and frame-by-frame
smoothness — the judgment so far is real eyes on one model, not a measurement.

## What this set does not cover

| Pool | Ships | Missing |
|---|---|---|
| idle | `Idle`, `IdleShift` (authored) | — |
| speaking | `Talking` (authored) | conversational hand gestures; the loop speaks with its body |
| thinking | the idle body, on purpose | anything that reads as thinking |
| dance | `Dance_Loop` | variation; one clip is one dance |
| actions | `Greeting` (authored) plus `Jump_Start/Loop/Land`, `Interact`, `PickUp_Table`, `Hit_Chest`, `Hit_Head`, `Punch_*`, `Roll`, `Death01` | **wave, peace sign, thumbs-up, telling a secret** — written, not yet shipped |
| attention | `Interact` (an approximation) | nothing that says "look here" |
| tool | nothing — the state falls back to the idle | any clip that reads as working without a prop |

Every pool above loops for as long as its state lasts, bar `actions`, which play once
over a loop. That is what decided the set: a pool's clip is not a gesture, it is a
posture held for seconds, so a clip that only makes sense as a one-off belongs in
`actions` however good it looks once.

Excluded for that reason: every seated clip (`Sitting_Idle_Loop`, `Sitting_Talking_Loop`,
`Sitting_Enter/Exit`, `Driving_Loop`) floats without a chair, and `Crouch_Idle_Loop` and
`PickUp_Table` became loops of crouching and grabbing, so the first is not shipped and the
second moved to `actions`. `Idle_Torch_Loop` is also out: it holds a torch there is no
model for. Thinking is a state visited on every tool call, where mime reads worse than
stillness, so `thinking/Thinking.vrma` is the authored idle body byte for byte, and the
pool exists only so the state has a clip at all.

Props are the same problem in another form: the pack animates hands *as if* holding a
sword, pistol or torch, and none of those objects exist in the window. `Fixing_Kneeling`
was the one shipped on inference rather than sight — its name says kneeling while its hips
sit at standing height, so it should read as hands busy at waist height — and that inference
did not survive a second look. It is parked rather than converted.

The gesture gap is the one to plan around: every freely licensable source is
capture of whole bodies, and ours needs hands doing specific things.

## Rebuilding this set

From a clone of this repository, with python 3 and nothing else:

```bash
git clone https://github.com/J-Ponzo/gltf-universal-animation-library
python3 tools/gltf2vrma.py \
    gltf-universal-animation-library/glTF/AnimationLibrary_Godot_Standard.gltf \
    ./converted --tpose A_TPose
node tools/vrma-info.mjs ./converted   # npm install in tools/ first
```

The converter reproduces the 46-clip converted set, not this repository's 20
files. Thirteen of the shipped files are those clips renamed into pools following
the table of fifteen above; the other five are written by the authoring scripts,
which read their base pose from `./converted/idle/Idle.vrma`:

```bash
python3 tools/author-vrma.py ./converted ./built IdleBreath IdleShift TalkingSoft
python3 tools/author-actions.py ./converted ./built Greeting Wave Nod ThumbsUp TellingASecret LookHere
```

`./built` then holds every authored clip under its generator name; renaming them
into the pools is the table of five above. The pack names its T-pose clip `A_TPose`
and the converter finds it on its own; `--tpose` only spells it out, and since the
measurement above found the rest pose already equal to it, the flag applies no
offset.
