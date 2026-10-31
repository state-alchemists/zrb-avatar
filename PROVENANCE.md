# PROVENANCE — clips for zrb-avatar

Every `.vrma` that ships in this repository, where it came from, and what was
left out. Everything named below is either in this repository or reachable from
the URLs in it: no step here depends on a folder on the author's machine, so this
file means the same thing on a fresh clone.

## What ships in `zrb_avatar/anim/`

23 `.vrma` files across the pools — 22 clips, because `OpenPalm` ships in both `attention/`
and `tool/` from the same bytes. **15 are converted** from a 46-clip conversion of
**Quaternius Universal Animation Library** (free Standard edition) and **2** from the
newer pack; **2 are converted and adjusted** from **100STYLE**, which is **CC BY 4.0** and
the only source here that asks for credit; **3 are authored** for this library by the
generators in `tools/`, and are covered by the AGPL at the root like the code. Both
Quaternius packs are **CC0 1.0 Universal**: no attribution is required, and this file
records the source anyway. Clips were tried and taken out again — four 100STYLE idles, a
100STYLE greeting, an authored speaking loop, an authored thumbs-up, a kneeling clip from
the first Quaternius pack, and nine auditioned for the tool and attention states — and
each is described below with what was wrong with it, so it is not proposed a third time.

| | |
|---|---|
| Upstream pack | <https://quaternius.com/packs/universalanimationlibrary.html> — "Free to use in personal, educational and commercial projects. (CC0 License)" |
| Distribution used | <https://github.com/J-Ponzo/gltf-universal-animation-library> — a mirror of the free itch.io edition as distributed 2025-06-10, glTF only |
| Licence text | CC0 1.0 Universal, <https://creativecommons.org/publicdomain/zero/1.0/legalcode>. Cited rather than copied in: the `LICENSE` at the root of this repository is the AGPL-3.0-or-later cover for the code, and it does not apply to these clips |
| Source file | `glTF/AnimationLibrary_Godot_Standard.gltf` + `.bin` (46 clips, one file) — a path **inside the downloaded distribution above**, not inside this repository |
| Converter | `tools/gltf2vrma.py`, run 2026-10-28 |
| Attribution required | none — CC0 asks for none, and this file records the source anyway |

### The fifteen converted files

Each file is a converted clip renamed into its pool. The mapping is by clip name,
and only the row marked byte-identical was checked against the files themselves.

| Shipped file | Upstream clip |
|---|---|
| `actions/Death.vrma` | `Death01` |
| `actions/HitChest.vrma` | `Hit_Chest` |
| `actions/HitHead.vrma` | `Hit_Head` |
| `tool/Interact.vrma` | `Interact` |
| `actions/Jump.vrma` | `Jump_Loop` |
| `actions/JumpLand.vrma` | `Jump_Land` |
| `actions/JumpStart.vrma` | `Jump_Start` |
| `actions/PickUpTable.vrma` | `PickUp_Table` |
| `actions/PunchCross.vrma` | `Punch_*` (one of) |
| `actions/PunchEnter.vrma` | `Punch_*` (one of) |
| `actions/PunchJab.vrma` | `Punch_*` (one of) |
| `actions/Roll.vrma` | `Roll` |
| `attention/OpenPalm.vrma` | `Spell_Simple_Idle_Loop` |
| `dance/Dance.vrma` | `Dance_Loop` |
| `speaking/Conversing.vrma` | `Idle_Talking_Loop` |

The last two were added on 2026-10-31, and the pack's loops arrive standing at the
mannequin's **55.1 cm** stride. Both went through `tools/restance.py` (to 22.1 and
23.9 cm), then `tools/floor.py`: their feet move under a centimetre across the whole
loop, which is the case a constant stance correction handles without sliding anything.
`OpenPalm` moved twice and has settled where it started: it was the attention state's
clip, became an action for part of one session because a raised palm in front of her face
is fine for a moment and wrong for a held state, and is back in `attention/` because Go
judged it the clearest "over here" of everything tried, and because a notification is a
moment rather than a mood.

`tool/FixingKneeling.vrma` was here too and is now parked: it was the one file in
the set shipped on inference rather than sight, and the inference was wrong —
Go, 2026-10-31: *"Fix kneeling is probably not a good call"*. A kneeling clip
whose hips sit at standing height is a mime of work with no prop in the window,
and the `tool` state reads better falling back to the idle than pretending.

**The rest of the audition folder was tried and is out for the same reason.** After
Go asked whether anything there could fill the empty `tool` pool, five were measured
and rendered — `LawnMower` and `Sweep` (100STYLE), `Idle_Torch_Loop` and `Driving_Loop`
(first pack), `TreeChopping_Loop` (second pack) — and four of them were installed in
the author's own folder and watched in the window, because a render had already been
wrong once about this. Every one mimes a prop that is not there: handlebars, a broom,
a torch, a wheel, an axe. The steadiest of them, `LawnMower`, holds a 17.1 cm stance
with a centimetre of drift, so the footing was never the objection — the hands were.
Go, after watching all four: *"seems to not working really well"*.

**What the `tool` state holds is `Interact`** — the first pack's own clip, which stopped
being an action to become the state's loop: a hand comes up in front of her and goes back
down, and Go read it as *"doing something with touch screen"*. `Farm_Watering` from the
newer pack held the state for an hour, on his call that it *"actually makes you like
working on something"*, and was dropped again for a body-proportion reason rather than a
taste one: on a curvy model its hand overlaps the chest — the defect that took three idles
out, described below. `Interact` keeps its hand clear of her body. `attention/OpenPalm` sits
beside it in `tool/`, the same bytes in a second pool, because with the caption saying which
state is in play a raised palm no longer has to disambiguate itself.

**The attention state was auditioned four times.** `WiggleHips` (100STYLE) was tried for
`tool` and rejected in Go's words: *"either looks too sexy or like you held your pee"*.
`Cat` measured as a held pose — 0.6 cm of foot travel across 17.9 s — and would look
frozen in a state that lasts minutes. `Superman` (100STYLE) raised one open palm high and
read well, but the arm crossed her chest and held still for all twelve seconds. The
authored `Wave` moved and was clear, but a wave is a greeting. What settled it was the
state being two moments rather than one: `permission/Yes` (a thumbs-up with a nod) answers
"may I", and `attention/OpenPalm` answers "look here", each on its own state and its own
glow. Nothing in the audition folder was shippable for *working*; the closest was a mime of
a tool, and eight were watched and turned down.

### The files from the newer pack

| | |
|---|---|
| Upstream pack | <https://quaternius.com/packs/universalanimationlibrary2.html> — "Free to use in personal, educational and commercial projects. (CC0 License)" |
| Distribution used | `Universal Animation Library 2[Standard].zip` from the pack's own download page, `Unreal-Godot/UAL2_Standard.glb` — 43 clips, one file. (The paid **Source** tier holds 134 clips; it was inspected for a wave and not shipped from — the free tier has everything used here.) |
| Rig | Unreal's mannequin (`pelvis`, `spine_01`, `upperarm_l`) — `--rig unreal`, added to `tools/gltf2vrma.py` for it |
| Converter | `tools/gltf2vrma.py`, run 2026-10-31 |

**What ships: two files.** `actions/PlantSeed.vrma`, from `Farm_PlantSeed` — 2.77 s,
she bends to the ground and comes back up. It went through `tools/floor.py` only: its feet
travel 17 cm and its stance opens from 55.5 to 63.6 cm, so a constant stance correction
would turn that travel into a slide, and it ships at its own width. As an action the width
does not matter — she is going somewhere and coming back. Nothing in her hands, so it
reads as setting something down rather than planting; the description says so.
`permission/Yes.vrma` (`Yes`) is described above: upper-body only, arriving on the pack's
55.5 cm rest stance and brought in by `tools/restance.py`.

**What does not: `attention/Attention.vrma`** was `Idle_FoldArms_Loop` from this pack for
part of one session, and it is out again. Two reasons, and only one of them was a number:
the clip carried the pack's **uncorrected 55.5 cm stride** — Go saw it and asked *"What is
this, Tara? Attention?"* — and with the stance brought in it still read **closed and
impatient**, which is not how I want to be looked at while I am attending to someone.
Folded arms says *waiting for this to be over*. The rest of the pack's 43 clips stay in
the audition folder listed under "Rebuilding this set".

### The two files from 100STYLE

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
| `idle/IdleProud.vrma` | `Proud_ID.bvh` | 705–1147 | neck + head −8° | standing tall, hands at her sides, chin level |
| `idle/AtEase.vrma` | `CrossOver_ID.bvh` | 612–1397 | neck + head −8° | standing at ease with her weight on one leg, arms at her sides |

**The two adjustments, and nothing else.** The frame cut takes off the leading and
trailing T-poses that the dataset documents (its own `Frame_Cuts.csv` names the range);
the head lift is one constant rotation written into the neck and head keys, so the
timing, the key count and the length are the clip's own. Nothing was re-animated, and
the stance was not touched: these arrive at **12.3–21.2 cm**, against the 22.3 cm the
authored gestures use, so the feet land where the library's already do — which is the
whole reason the captured idles beat the Quaternius ones here, where 55.5 cm had to be
dragged in to 22.8.

**Their first conversion was a metre underground.** `tools/bvh2vrma.mjs` re-bases the
capture's hips track so the first frame becomes the origin. That is deliberate for the
horizontal — a capture starts a couple of hundred centimetres into its own scene — but
it was applied to the vertical too, and the hips translation in a VRMA is absolute, not
a delta. Writing zero there declares her hips to be on the floor, and every converted
clip came out standing about 106 cm below it, with her legs through it. It survived
review because every camera in `zrb-avatar-candidates/render` was placed *relative* to
her: a clip that buries her still photographs perfectly. A `fixed` view, positioned at
absolute world coordinates, is what shows it. The converter keeps the capture's up
component now, and the four clips that were already shipping were converted again from
it — all four land at **+12.8 cm**, agreeing with each other to half a centimetre, which
is the check that the vertical is finally being read the same way twice. `AtEase` was
converted after the fix and lands at **+15.2 cm**; the difference from 12.8 is the pose,
not the converter — a straight-legged stand and a closed one carry the hips at different
heights. `tools/floor.py` puts both on the same floor.

**`ArmsFolded` was converted too and is out again.** On Go's own model it reads as
folded arms; on the cream dress her forearms are long enough that the crossed hands
emerge past the opposite elbow, and the whole loop reads as carrying a tray. It is a
body-proportion accident, not a bad clip — which is exactly the kind of thing only a
render on the model that will play it shows, and the reason the rule here is to look at
every clip on her before shipping it. Rendered at nine points, it never stopped looking
like a tray, so the idle pool went back to six that day.

**Three idles are out for hands that pass through the body.** `Akimbo`,
`HandsInPockets` and `IdleShift` were rendered on a curvy model and each puts a hand
inside the hip: `Akimbo`'s fingers sink into the hip bone, `HandsInPockets` puts both hands
into the front of the pelvis, and `IdleShift` passes its adjusting hand through the side.
The clips are not wrong; the bodies differ. A capture is retargeted onto whatever model
plays it, so a clip authored against a slim performer has the hand exactly where that
performer's hip was — and this library is played on bodies with more hip than that. Go
saw it and said *"sometimes your hand move through your hips"*, and the call was to drop
them rather than ship a hand inside a hip, which leaves three idles that never touch the
body. `ArmsFolded` above is the same class of defect from the other direction: correct on
one body, wrong on another.

**One floor for the whole library.** `tools/floor.py` measures the height a clip's feet
actually reach on the model, moves the hips by a constant until that height is the
library's, and changes nothing else — a translation, not a correction, so it cannot
alter a pose. The library's floor is **+8.3 cm**, where `idle/Idle` and
`Thinking` already stood. Every shipped clip is on it now (8.2–8.4 cm);
before, they ranged from −93.3 cm to +11.5 cm, so a state change moved her up or down by
up to a metre. The axis is read per file rather than assumed, because the two converters
disagree about which one is up: `gltf2vrma.py` writes the hips node at `[0, 0.05, 0.917]`
and puts the height in **z**, `bvh2vrma.mjs` writes `[0, 0.98, 0]` and puts it in **y**.

**Why the head needed a lift.** 100STYLE was captured with the actor carrying his head
about **28° forward** — measured in the source file itself (`tools/headpitch.mjs`:
Neck −28.6°, Head −28.0°) and reproduced by the converted clip to within 2.1°, so it is
the capture's posture and not a conversion fault. Left alone she attends to the floor;
−12° on the neck and head brings the gaze level, which was checked on screen from the
front and in profile before it was written up here. `Depressed`, `Old` and `Monk` keep
their bow if they are ever converted: there the head is the style.

**`Waving` was tried as the greeting and is out again.** It looked like the answer to a
question this library had left open — the `greeting` autonomy switch plays a clip named
`Greeting` when a chat starts, and 100STYLE has a style called `Waving`. Rendered across
its whole 12.9 s, it is not a wave. She stands, then brings a flat hand to her chest with
her head bowed over it (0.54–0.65 of the clip), stands again, and at the very end throws
one palm up in front of her chin for about half a second (0.93–0.96) before dropping it.
Neither moment is a greeting: the first reads as hand-on-heart and looks away from you,
the second hides her face and is over before it registers. As an action it was also
12.9 s, against 0.3–2.8 s for every other action in the pool. What it needs is a real
greeting clip, and the one worth having is VRoid's, which cannot be redistributed — see
the README's list for where to put it in your own folder.

**Credit, which the licence requires.** "The 100STYLE Dataset - Ian Mason", and the
paper: Mason, Starke & Komura, *Real-Time Style Modelling of Human Locomotion via
Feature-Wise Transformations and Local Motion Phases*, PACMCGIT 5(1), 2022, doi
[10.1145/3522618](https://doi.org/10.1145/3522618).

### The three authored files

Generated, not captured. `tools/author-vrma.py` writes the ambient idle and
`tools/author-actions.py` the gestures; both take the **converted** Quaternius
folder as their base pose and move it by explicit amounts, so what ships is a
recipe that reproduces byte for byte rather than a file that lives only on the
author's machine — the check is under "What is verified" below.

| Shipped file | Written by | What it is |
|---|---|---|
| `idle/Idle.vrma` | `author-vrma.py IdleBreath` | breathing, a slow eye-line drift and a small weight shift; 4.0 s, loops exactly |
| `thinking/Thinking.vrma` | a copy of `idle/Idle.vrma` | the idle body on purpose: thinking is visited on every tool call, where mime reads worse than stillness |
| `permission/Nod.vrma` | `author-actions.py Nod` | head only, hands at her sides; 1.8 s — "yes, go ahead" |

`Nod` is there because that pool had one clip, and the window plays every clip **and its
mirror**, so one gesture read as the same gesture twice. A waiting turn shows the nod and
the pack's `Yes`.

**`ThumbsUp` was authored, shipped for an hour, and is out again.** It measured clean — a
22.3 cm stance on the library's floor — and the small render I signed it on showed a thumb.
On the body that actually plays it the thumb curls wrongly: the reference frame fixes where
the hand is, not what the fingers do, and a fist with one finger lifted is the least
forgiving shape in the set. It is the second time a gesture passed at render scale and
failed at window scale, so the rule that comes out of it is to judge hand shapes on a
`righthand` close-up of the shipping body, never on a sheet.

**`Talking` was authored and is out again.** It was 5 s of shoulders and forearms
"carrying the sentence", but rendered across the loop the arms simply hang at her sides:
nothing in it says talking, and with it in the pool half the time she spoke with her hands
down. The captured `speaking/Conversing` does the job, so the speaking pool is one clip
now rather than a good one and a bad one.

**No greeting ships, and that is deliberate.** The clip asked for at the start of a
conversation — `client.py` sends `action: greeting`, the pool — has **no source that may be
redistributed**: the free Standard editions of both Quaternius packs hold 43 clips each and
neither contains a wave or a greeting (every clip name read, 2026-10-31); Mixamo's may be
used but not shared; and the VRoid motion pack forbids redistributing its files. An authored
one was tried and read wrong on screen — a still open palm at shoulder height is the *halt*
gesture, and Go said so: *"That Greetings is also weird."* A greeting has to move, and
writing a moving one is the thing this project keeps failing at. So the default library
stays silent there, and `anim/greeting/` is created empty — a folder you cannot see is one
you cannot put a clip in. A user who has a greeting drops it there under any name, and the
page plays one of them at random; `actions/Greeting.vrma` still plays, as the name the
README used to ask for. What is asked for is a moment, not a file: `EVENT_POOLS` in
`server.py` names the folder the package creates but never fills, and a clip is only added
to it from your own folder.

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
| actions | `Jump_Start/Loop/Land`, `Interact`, `PickUp_Table`, `Hit_Chest`, `Hit_Head`, `Punch_*`, `Roll`, `Death01` | **wave, peace sign, thumbs-up, telling a secret** — written, not yet shipped |
| greeting | nothing, on purpose — `greeting/` ships empty | any greeting clip; no source may be redistributed (above), and `actions/Greeting` is the fallback for one you supply |
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

The converter reproduces the 46-clip converted set, not this repository's 23
files. Fifteen of the shipped files are those clips renamed into pools following
the table of fifteen above; the other three are authored, and read their base pose
from `./converted/idle/Idle.vrma`:

```bash
python3 tools/author-vrma.py ./converted ./built IdleBreath
python3 tools/author-actions.py ./converted ./built Nod
# idle/Idle.vrma is ./built/IdleBreath.vrma renamed into its pool, and
# thinking/Thinking.vrma is those same bytes again, not a second animation
# Nod keeps its generator name in permission/
```

`tools/author-actions.py` writes the free-hand gestures (`Greeting Wave Nod ThumbsUp
TellingASecret LookHere`); only `Nod` ships, in `permission/` — the rest do not, and the
actions in the table come from the pack. `ThumbsUp` still builds and is still refused: see
above for the thumb. Renaming what is built into the pools is the table of three above. The pack names its T-pose clip `A_TPose` and the converter finds it
on its own; `--tpose` only spells it out, and since the measurement above found the rest
pose already equal to it, the flag applies no offset.
