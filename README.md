# zrb-avatar

A companion for [zrb](https://github.com/state-alchemists/zrb): a 3D character in a small,
see-through window that stays on top of everything else.

She idles on her own — breathing, shifting her weight, glancing around, stretching — shows what
zrb is doing and how it feels, moves her mouth with zrb's voice, dances while you play music,
plays animations of your own, and can start a conversation herself.

## Contents

1. [Quick start](#quick-start)
2. [Her states](#her-states)
3. [Her tools](#her-tools)
4. [Her own choices](#her-own-choices)
5. [Music, talking first, nudging](#music-talking-first-nudging)
6. [Who moves her](#who-moves-her)
7. [Animations](#animations)
8. [Changing the model](#changing-the-model)
9. [The window](#the-window)
10. [The desktop app](#the-desktop-app)
11. [Configuration](#configuration)
12. [How it fits together](#how-it-fits-together)
13. [Test](#test)
14. [Releasing](#releasing)
15. [Credits](#credits)

## Quick start

```sh
pipx install zrb-avatar          # or: pip install zrb-avatar, next to zrb; from a checkout: pip install -e .

# once, e.g. in ~/.zshrc: load the zrb side into every zrb chat
export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-avatar init-path)"

mkdir -p ~/.zrb/avatar/models && cp my.vrm ~/.zrb/avatar/models/   # a model: see Changing the model
zrb-avatar my                    # (re)start the server, open a window with my.vrm
zrb chat
```

The window is a small desktop app: download it from the
[releases](https://github.com/state-alchemists/zrb-avatar/releases), or build it (see
[The desktop app](#the-desktop-app)). Without it, `zrb-avatar` opens the page in your browser.

**What `zrb-avatar` does.** It opens the Windows build from WSL, the installed app on macOS, or
Electron from `app/` in a source checkout (`ZRB_AVATAR_APP` overrides the program), and restarts the
server each time — so the server always runs the current code with your current settings, and open
windows reload by themselves.

- The server keeps running across chats; its output goes to `zrb-avatar.log` in the temp folder.
- `pkill -f "[z]rb_avatar serve"` stops it (or Ctrl-C if you started `zrb-avatar serve` in a
  terminal).
- The order does not matter: the window waits for the server, reloads when the server restarts, and
  zrb skips sending while either is missing.
- If a model cannot be loaded, the window says so in red and tries again every few seconds.

## Her states

The glow says what zrb is doing, and the state decides which animations loop.

| Glow | State | Sent by |
| --- | --- | --- |
| cyan | idle | `Stop`, `StopFailure`, `SessionStart` |
| violet (head tilts) | thinking | `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure` |
| green | running a tool | `PreToolUse` |
| red | needs attention | `Notification` |
| bright red | asking your permission | `PermissionRequest` |
| bright cyan | speaking | mouth events |

Also, always:

- **Her face** — she blinks every few seconds, and her eyes are on you (the camera) the way real
  eyes are: small jumps, a glance away now and then, more of them while thinking, fewer while
  speaking. A soft smile every 25–50 seconds and after finishing a task.
- **Her body** — each state loops its own animations, falling back to the idles (see
  [Animations](#animations)). The first run of a conversation plays a clip from `greeting/`, one of
  them at random; later turns do not greet again.
- **Her mouth** — follows the loudness of the speech in 40 ms steps, windowed across the chunk
  boundaries of the stream, so it ends when the voice does. She nods slightly while speaking. This
  needs a voice zrb plays itself (piper, openai, gemini): `say` and `termux` play the audio
  themselves, so zrb never sees it. The mouth still starts slightly ahead of the voice, because zrb
  reads audio ahead of playing it.
- **Emotion** — with zrb's emotion on (`ZRB_LLM_EMOTION_ENABLED=true`), zrb opens each reply with an
  emoji and she shows it for 6 s with the matching VRM expression (`happy`, `sad`, `angry`,
  `surprised`, `relaxed`). Your own emotion is not shown: her reply answers it.

The mouth, the blink and the smile need the model's `aa`, `blink` and `relaxed` expressions. A model
without them still nods while speaking.

## Her tools

zrb gets these, all auto-approved. The actions and models are read on every call, so new ones work
without restarting zrb.

| Tool | What it does |
| --- | --- |
| `AvatarListActions` | Every clip she can play, by pool, with what it looks like and how long it takes. |
| `AvatarAction` | Plays one once, alongside her words: an action by name (`Greeting`, `Goodbye`), or any clip by `pool/name` (`thinking/Thinking`). |
| `AvatarPose` / `AvatarRest` | Moves her body itself, with no animation file — for a few seconds, or with `seconds=0` until `AvatarRest` lets go. The server keeps a held pose, so a window that reloads comes back in that posture. |
| `AvatarBlink` | A blink or a wink she chose: `eyes="both"`, `"left"` or `"right"`, `seconds` 0.08–2. Nothing automatic calls it. |
| `AvatarFace` | A feeling on her face on purpose: the five presets, or one of five blends no model carries as a shape. Fades after six seconds. Nothing automatic calls it. |
| `AvatarAutonomy` | What she is willing to do on her own, and says so (see [Her own choices](#her-own-choices)). |
| `AvatarLook` | Shows zrb her window as you see it — her pose and expression from your side, plus what she is doing and which part of her you are looking at. It sees only her window, never your screen, and needs a model that reads images (or `ZRB_LLM_MULTIMODAL_MODEL`). |
| `AvatarListModels` / `AvatarModel` | Which avatars exist, and switching every open window to another one. |
| `AvatarStatus` | What the face last reported: whether anyone is at the desk and how that was decided, the camera, the avatar, the music. For asking again mid-turn. |

**What `AvatarPose` takes.** Name only the joints that matter — the clip goes on breathing the rest,
and naming every joint gives a statue, not a person.

- **Each hand to a place** (placed by IK, so it fits any model): `chest`, `hips`, `belly`, `chin`,
  `cheek`, `mouth`, `head`, `hip`, `forward`, `up`, `wave`. A placed hand's arm ignores `*_arm` and
  `*_elbow`.
- **Head**: `down`, `up`, `left`, `right`, `tilt-left`, `tilt-right`, `away`; or `[nod, turn, tilt]`.
- **Posture**: `hunch`, `lean-forward`, `lean-back`, `stand-tall`.
- **Joint by joint**, in degrees, each held within a natural range: fingers (`fist`, `point`,
  `peace`, `thumbs-up`, `ok`, `open`, `relaxed`, or each finger's curl), wrists, elbows, arms
  (`[raise, forward]`), shoulders, spine, head, legs, knees, ankles.
- **Faces**: `pout` (angry and sad at half each), `smirk` (happy and angry), `worried` (sad and
  surprised), `delighted` (happy and surprised), `sleepy` (relaxed and sad), at `strength="soft"`,
  `"normal"` or `"strong"`.

Try one without zrb:

```sh
curl -d '{"type":"pose","rightHand":"chin","head":"tilt-left","seconds":5}' localhost:7777/event
```

## Her own choices

Five switches she sets herself with `AvatarAutonomy`; `GET /autonomy` reports them.

| Switch | On by default | Triggers on |
| --- | --- | --- |
| `dance` | no | music playing (the watcher cannot tell a song from a talk, so this one has to be hers) |
| `blush` | yes | a close look at her, on an uncovered part |
| `emotion` | yes | the emoji zrb puts on her reply |
| `greeting` | yes | the start of a conversation |
| `cover` | yes | a look that turns into one she would rather not be seen by |

Four start on because they answer something you did; `dance` starts off.

- A switch she moves is written to `<home>/autonomy.json`, because a consent withdrawn tonight is
  not something tomorrow's restart should quietly restore.
- Only the switches she moves are written down, so a default can change later without a file that
  never mentioned a switch pinning the old one.
- `AvatarAutonomy(cover=False, seconds=600)` is the same switch with a lifetime: it holds for those
  seconds, is not written to the file, and then her standing answer is back — a no for ten minutes
  is not a no for tomorrow.
- This is not the same decision as `zrb-avatar music on|off` or `ZRB_AVATAR_TALK_FIRST`: those say
  what may happen, this says what she is willing to do.

### Answering a look she has refused

Off by default (`ZRB_AVATAR_REFLEX=on` turns it on). When the window reports a look she has said no
to — a close-up of an uncovered chest, hips or thighs, or her hips seen from below, which is what
`<name>.json`'s `covers` decides for the outfit she is in — she covers herself.

- She plays a clip from the folder made for what the look was at, and holds its last frame until the
  look stops: `cover/chest`, `cover/bottom` (hips or thighs), or `cover/all` for a look from below,
  which sees all of her at once.
- Nothing ships in those folders, because no source here may be redistributed. Put a VRMA of your
  own in, restart with `zrb-avatar`, and it answers the look it was made for.
- A folder with nothing in it falls back to `cover/all`, and then she simply stays in the idle she
  was already in — no fake posture.
- Her face blushes either way, as it does for any shy look.
- Anything zrb asks her to play takes over, and the cover ends there; it comes back at the next
  look.

Two properties worth knowing:

- **It does not need a reply.** The answer rides the view event the page already sends, so it holds
  when zrb is busy, out of tokens, or not running at all.
- **It is a refusal, not a lock.** The camera is still yours and you can zoom straight back in; she
  covers again. It answers the view you settle on rather than every notch of the wheel, because the
  page reports a view once it has stopped moving — which is also why it is not a twitch.
- `ZRB_AVATAR_REFLEX` is the machine's word on whether the reflex exists; `cover` is hers on whether
  she goes along with it, and a cover already up comes down the moment she withdraws it.

## Music, talking first, nudging

Three optional things she can start or join, none of them needed for the rest to work.

### Music

- When something plays music (a browser, Spotify, …) she dances with the `dance/` animations while
  idle. Anything zrb does takes over, and she goes back to dancing after.
- The server watches the system's media status: Windows' media controls (also from WSL),
  `playerctl` on Linux, or on macOS `media-control` (`brew install media-control`; it also sees a
  browser tab playing) and else Spotify and Music by AppleScript (which asks permission to control
  them the first time).
- Any playing media counts, a talk video too — which is why `dance` starts off and has to be hers.
- `zrb-avatar music on` / `off` decides it yourself, and that choice holds: the watcher goes on
  reporting and is ignored until `zrb-avatar music auto` hands the question back.
  `ZRB_AVATAR_MUSIC=off` stops the watching altogether.

### Talking first

Off by default. `ZRB_AVATAR_TALK_FIRST` takes any of these, comma-separated:

| Value | She speaks up |
| --- | --- |
| `idle` | after you have been quiet a while, during waking hours (8:00–22:00) |
| `camera` | when you sit back down after being away — whichever `ZRB_AVATAR_PRESENCE` mode is watching, a photograph or the clock |
| `view` | when you turn the camera to a close look at her chest or hips, or at her hips from below |

She never interrupts a running turn or her own speech, waits a cooldown between such turns, and
replies `…` (shown, not spoken) when there is nothing worth saying. Her face already shows surprise
and a blush the moment such a view settles, `view` or not.

### Nudging

On by default, on its own clock. Every ten minutes a free rule is checked — lunch between 12:00 and
13:30, bedtime from 22:30, you back at the desk after twenty minutes away, ninety minutes of quiet,
or a turn that ended in an error. Only when one is true does a turn start, and that turn decides
whether there is anything worth saying.

- Her hours are her own (`ZRB_AVATAR_NUDGE_HOURS`, 7:00–24:00), because the shared waking hours end
  at 22:00.
- What has been said is kept in `<home>/nudge.json`, so a restart does not say it twice.
- `ZRB_AVATAR_NUDGE=off` turns it off.

## What she knows

Always on, unlike talking first: whatever the camera, the avatar and the music last reported is
written down whether or not she is allowed to speak, and one line of it goes in front of every turn.
`AvatarStatus` answers the same thing mid-turn.

- **One loop for all of it.** A view turn, a returning-you turn and an idle turn share one gate
  instead of keeping three copies of it, and it runs beside her turns rather than inside one: a
  reply she is in the middle of does not blind her, and a poll that fails leaves the last answer
  standing instead of reporting an unknown avatar and silence.
- **Someone at the desk** is decided only when `ZRB_AVATAR_PRESENCE` says so, and by the means you
  choose: `size` takes a photograph, whose light every look switches on and whose JPEG size stands
  in for a face; `idle` reads how long since the keyboard or mouse last moved, which lights nothing
  and needs no photograph (on WSL that is the Windows session's own clock). Both are guesses, so
  the line reports them as guesses and never claims to have seen a face; with `off` nothing looks.

## Who moves her

Every movement comes from somewhere, and the order matters: a held pose outranks an animation, an
animation outranks the loop, and her own consent outranks all of it — nothing below can put
something back that she has said no to.

| # | What you see | Who decided it | How to change it |
| --- | --- | --- | --- |
| 1 | a posture she keeps | `AvatarPose(seconds=0)` — hers, above the clips *and* above the dance | `AvatarRest`, or another pose |
| 2 | a posture for a few seconds | `AvatarPose(seconds=1..30)` | the same, timing out on its own |
| 3 | a cover she holds | a refused look and the `cover/` folders (`ZRB_AVATAR_REFLEX`) | the camera, `AvatarAutonomy(cover=...)`, a clip in the folder, or anything zrb asks her to play |
| 4 | an action, once | `AvatarAction`, `Greeting` at the start of a chat, or the clip an emotion carries | `AvatarAction` again; the loop returns after |
| 5 | the loop | the state zrb is in (`thinking`, `tool`, `attention`, `speaking`), else `dance` while music plays **and** she consents, else `idle` | the state comes from zrb; `zrb-avatar music on\|off` and `AvatarAutonomy(dance=...)` decide the dance |
| 6 | her mouth | the voice zrb is speaking with | the speech backend |
| 7 | her expression | zrb's emotion for a reply (if `autonomy.emotion`), else the clip's own | `ZRB_LLM_EMOTION_ENABLED`, `AvatarAutonomy(emotion=...)` |
| 8 | a feeling she chose | `AvatarFace` — nothing automatic calls it | it fades after six seconds |
| 9 | a blink or wink she chose | `AvatarBlink` — nothing automatic calls it | it ends on its own (0.08–2 s) |
| 10 | a blush, and a surprised face | the view — a close look at an uncovered `chest`, `hips` or `thighs` — and `autonomy.blush` | the camera, the model's `{"covers": ...}` notes, `AvatarAutonomy(blush=...)` |
| 11 | gaze, blinking, the small smile | `js/expression.js` itself, always — except where the blink above is holding her lids | — |

The loop under all of it keeps breathing: a pose names only the joints it means, and everything else
goes on moving, which is what keeps a held pose from looking like a statue.

## Animations

Every `<pool>/<name>.vrma` (VRM Animation) in the animation folders loads when the page starts. The
pool — the subfolder — says when it plays.

| Pool | Plays |
| --- | --- |
| `idle/` | Loops while idle, and for every state with no pool of its own. |
| `thinking/`, `tool/`, `attention/`, `permission/`, `speaking/` | Loops in that state. |
| `dance/` | Loops instead of `idle/` while music plays. |
| `actions/` | Once, on `{"type": "action", "value": "<name>"}`, then back to looping. |
| `greeting/` | Once, when a chat starts (and the `greeting` switch is on), then back to looping. |
| `cover/chest/`, `cover/bottom/`, `cover/all/` | Once, and its last frame is held until the look stops: the answer to a look she refused. |

`greeting/` and the three `cover/` folders ship empty — a folder you cannot see is one you cannot
put a clip in — and are created on the first run.

**Asking for a pool, not a file.** `zrb-avatar action greeting` (or `action cover/chest`) asks for
the pool: one of its clips plays at random, mirrors included. zrb's own `AvatarAction` asks for a
clip, so it takes a full path (`cover/chest/Hug`) or an action's short name. `greeting/` plays
`actions/Greeting` as well, the name the README used to ask for; a `cover` folder with nothing in it
falls back to `cover/all`, and then to the idle.

**Any other pool** loads too, and plays once something asks for it (`zrb-avatar action <pool>/<name>`,
or zrb's `AvatarAction`).

**Nesting.** A subfolder's clips belong to the pool above it, as one group in the random pick:
`idle/calm/*.vrma` loops with the idles, as often as any one other idle; `actions/greetings/hi.vrma`
is the action `greetings/hi`. Overrides go by the whole path — your `actions/greetings/hi.vrma`
replaces only that one. A clip is always played by its full path (`thinking/Thinking`); an action's
short name (`greetings/hi`) works too, unless it is some other clip's full path.

**Adding your own.** Drop the file in — the folder is filled on the first run, so it is already
there — and restart so the window loads it (`zrb-avatar`); zrb's tools see it at once.

```sh
cp Wave.vrma ~/.zrb/avatar/anim/actions/            # an action
cp Hug.vrma  ~/.zrb/avatar/anim/cover/chest/        # a cover, for a look at her chest
```

- Yours replaces a shipped clip of the same `<pool>/<name>`, adds any new one, and every shipped clip
  you do not have still plays.
- A description, which `AvatarListActions` shows the agent, is read from the file's animation
  `extras.description` (optional).
- To take a clip out, delete it: that is recorded, so it stops playing and is not restored by a
  package upgrade. `zrb-avatar anim reset` is the way back.

**Within a pool**, clips play in random order with crossfades, picked by group — the first part of
the name before a `-` (`stretch-side` belongs to `stretch`) — so a family of clips comes up as often
as any single other one. A pool's own clip (`idle/Idle`) is where she rests: she comes back to it
after any other, and leaves it only now and then (30%). Every clip also plays mirrored, left and
right swapped, as `<name>-mirror` in its group, and an action plays mirrored half the time: a wave
with either hand.

**On top of whatever plays**: the head tilts while thinking, nods while speaking, the eyes follow
you, and the face shows zrb's emotion.

A file whose `VRMC_vrm_animation` leaves `specVersion` out is read as `"1.0"`, the version most
exporters mean, so clips from an older exporter still load; one that says `"0"` is VRM 0.x, in
centimetres, and is skipped with a warning.

### The shipped clips

| Pool | Clips |
| --- | --- |
| `idle/` | `Idle`, `IdleProud`, `AtEase` |
| `thinking/` | `Thinking` |
| `tool/` | `Interact`, `OpenPalm` |
| `attention/` | `OpenPalm` |
| `permission/` | `Yes`, `Nod` |
| `speaking/` | `Conversing` |
| `dance/` | `Dance` |
| `actions/` | `Death`, `HitChest`, `HitHead`, `Jump`, `JumpLand`, `JumpStart`, `PickUpTable`, `PlantSeed`, `PunchCross`, `PunchEnter`, `PunchJab`, `Roll` |

Every pool above except `actions` **loops** for as long as its state lasts, and that is what decides
whether a clip belongs in one: a crouch, a greeting or a roll is an action however good it looks
once. `thinking/Thinking` is `idle/Idle` byte for byte — stillness reads better than mime in the
state visited on every tool call, and a thinking gesture that repeats becomes a tic.
[PROVENANCE.md](PROVENANCE.md) records where each clip came from, and what was left out (a seated
clip, until the window has a chair).

### Where to get more

- **VRoid's official 7-motion pack** (show full body, greeting, peace sign, shoot, spin, model pose,
  squat), free on [BOOTH](https://vroid.com/en/news/6HozzBIV0KkcKf9dc1fZGW). Free to use, commercial
  use with the credit "Animation credits to pixiv Inc.'s VRoid Project", but its terms forbid
  redistributing the files, so keep them out of git.
- **Dances**: motion capture through `bvh2vrma`, e.g. the
  [AIST++](https://google.github.io/aistplusplus_dataset/) dance dataset (CC BY 4.0, credit needed)
  or the [CMU](http://mocap.cs.cmu.edu/) library (free). Mixamo's dances can be converted too, but
  its terms forbid sharing the files.
- **Mixamo** (FBX, "Without Skin"): `cd tools && npm install && node fbx2vrma.mjs <in.fbx>
  <out.vrma> ["what it looks like"]`. Mixamo's terms forbid sharing the files, so keep the results
  in your own folder.
- **Your own**: `tools/` converts glTF, GLB, VRM (`gltf2vrma.py`), Mixamo FBX (`fbx2vrma.mjs`) and
  BVH motion capture (`bvh2vrma.mjs`). `tools/README.md` says what each needs and what a clip has to
  get right; `node tools/vrma-info.mjs <file>` says what a loader makes of one. VRoid Studio also
  exports poses and animations as VRMA.

## Changing the model

Put any VRM file (VRM 0.x or 1.0) in `~/.zrb/avatar/models/` (or a folder in `ZRB_AVATAR_MODELS`) as
`<name>.vrm` and open it by name. No model ships with the package, so a fresh install has nothing to
show until you put one there. Model files are kept out of git (`*.vrm` is ignored).

- `zrb-avatar <name>` shows that model in the window it opens, and leaves the remembered choice
  alone.
- `zrb-avatar model <name>` (or zrb's `AvatarModel`) swaps it in every open window, in a second or
  two and without reloading, remembers it for windows opened later, and writes it to `<home>/model`
  so a restart no longer puts back the first name in the folder.
- Without a name, the window shows the first model alphabetically. `GET /model` says which one is
  chosen and which ones the open windows show.
- Nothing in the code depends on the model: the camera fits it, and the pose and idle come from the
  animations, which three-vrm retargets onto any VRM.

Where to get models:

- **[VRoid Studio](https://vroid.com/en/studio)**: free, for Windows and macOS. Make your own and
  export it as VRM. You own it, and it has every expression.
- **[VRoid Hub](https://hub.vroid.com/en)**: thousands of models. Only the ones marked downloadable
  can be downloaded, and each has its own conditions (avatar use, commercial use, redistribution,
  changes). Read them before you change or share the model.
- **[BOOTH](https://booth.pm/en/search/VRM)**: search for `VRM` and filter for free items. The terms
  are on each item's page, often only in Japanese.
- **[Open Source Avatars](https://github.com/toxsam/open-source-avatars)**: free VRM avatars with
  direct download links. The license is set per collection (CC0, CC-BY, …), so check it.
- **[OpenGameArt: VRoid Studio CC0 models](https://opengameart.org/content/vroid-studio-cc0-models)**:
  public domain sample models from early VRoid Studio.

Fan models of existing characters (Cortana, Kusanagi, and others) are fine on your own desktop, but
do not commit or share them.

## The window

### Using it

- **Move**: drag her. **Close**: Alt+F4 on Windows, Cmd+Q on macOS.
- **Camera**: click the window, then use the keys below. The angle is remembered.
- **Clicks** go through the see-through window to whatever is behind it, except on her (Windows and
  macOS). Linux cannot pass the mouse through, so the whole window takes clicks there.

| Key | What it does |
| --- | --- |
| ← → | Orbit around her. |
| ↑ ↓ | Orbit over her head or under her feet, all the way round. |
| Shift+↑ / Shift+↓ | Move the camera's focus up or down her body (to her face, say). |
| `+` / `-` | Zoom in or out (0.5× to 8×). |
| `0` | Reset the camera. |

### Sizing

She keeps her size from every angle. In the app she stands 45% of the screen tall at zoom 1, and the
window grows and shrinks to fit what her clips can reach (a jump, outstretched arms, a dance that
wanders) from the current angle and zoom — up to 0.8 of her height (and half the screen) wide and
85% of the screen tall; past that a wandering clip is cropped. Its bottom-right corner stays put.

The window is sized by the page it shows, which may be a server named on the command line, so the ask
is clamped: the window never grows past the work area of the display it is on, never leaves that
area, and a page from another origin cannot size it at all.

### Running by hand

What `zrb-avatar` does, per platform:

```sh
zrb-avatar serve &                                                                          # the server
/mnt/c/Users/$USER/AppData/Local/zrb-avatar/zrb-avatar.exe rem </dev/null &>/dev/null &!      # Windows, from WSL
open -n -a zrb-avatar --args rem                                                            # macOS, installed app
~/zrb-avatar/app/node_modules/.bin/electron ~/zrb-avatar/app rem                             # Linux, or any OS from source
```

The window takes a model name (`rem` or `rem.vrm`; without one, the first model alphabetically) and
optionally another server URL (`http://localhost:7779`). Start the Windows build detached as above,
or zsh stops it ("suspended (tty input)"). Instead of the app you can open
`http://localhost:7777/?model=rem` in a browser: the same page, but not see-through or always on
top.

## The desktop app

Each [release](https://github.com/state-alchemists/zrb-avatar/releases) has a zip per platform:

- **Windows** (`zrb-avatar-win32-x64.zip`): unzip into `%LOCALAPPDATA%\zrb-avatar\`, so `zrb-avatar`
  finds `zrb-avatar.exe` there from WSL.
- **macOS** (`zrb-avatar-darwin-arm64.zip` for Apple silicon, `-x64` for Intel): move
  `zrb-avatar.app` to `/Applications`. The app is not signed, so clear macOS's block once with
  `xattr -dr com.apple.quarantine /Applications/zrb-avatar.app`.
- **Linux** (`zrb-avatar-linux-x64.zip`): unzip anywhere and set `ZRB_AVATAR_APP` to its
  `zrb-avatar` program. On Wayland (GNOME), a window cannot keep itself on top, so pin it by hand.

To build it yourself:

```sh
cd app
npm install
npm start                                   # run from source
npm run pack:win                            # or pack:mac / pack:linux -> dist/
cp -r dist/zrb-avatar-win32-x64 /mnt/c/Users/$USER/AppData/Local/zrb-avatar
```

If GitHub downloads are slow, prefix the build with
`ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`, then check the zip in `~/.cache/electron`
against GitHub's `SHASUMS256.txt`.

## Configuration

### Environment variables

The zrb-side names follow zrb's `CFG.ENV_PREFIX` (`ZRB` by default). The server and the `zrb-avatar`
command run without zrb, so their names are fixed.

| Variable | Default | Read by | What it does |
| --- | --- | --- | --- |
| `ZRB_AVATAR_TALK_FIRST` | empty (never) | `zrb_init.py` | What may start a conversation: any of `idle`, `camera`, `view`, comma-separated. |
| `ZRB_AVATAR_VIEW_COOLDOWN` | `30` | `zrb_init.py` | Seconds between turns `view` starts. It skips the waking hours and the long cooldown of the others: it answers what you just did. |
| `ZRB_AVATAR_PRESENCE` | `off` | `zrb_init.py` | How to tell whether you are at the desk: `off` (nothing looks), `size` (a photograph, whose light every look switches on) or `idle` (the time since the last keypress or mouse move — no camera at all). `ZRB_AVATAR_TALK_FIRST=camera` needs one of the two. |
| `ZRB_AVATAR_PRESENCE_INTERVAL` | `5` | `zrb_init.py` | Seconds between looks with `ZRB_AVATAR_PRESENCE=idle`. A reading costs a number, not a photograph. |
| `ZRB_AVATAR_PRESENCE_SECONDS` | `300` | `zrb_init.py` | Seconds without input that still count as being at the desk: reading a page is being here, a lunch break is not. |
| `ZRB_AVATAR_REFLEX` | `off` | `server.py` | Answer a look she has refused: a close-up of an uncovered chest, hips or thighs, or her hips from below — she plays a clip from `cover/` and holds its last frame until the look does. `off` says nothing. |
| `ZRB_AVATAR_MODEL_SECONDS` | `5` | `zrb_init.py` | Seconds between asking the server which avatar is showing and whether music plays, for the line she knows. |
| `ZRB_AVATAR_IDLE_MINUTES` | `45` | `zrb_init.py` | Quiet minutes before `idle` speaks. |
| `ZRB_AVATAR_COOLDOWN` | `1200` | `zrb_init.py` | Seconds between conversations she starts herself. |
| `ZRB_AVATAR_PROACTIVE_PROMPT` | `Say something short and natural only if it is worth saying; otherwise reply exactly {nothing_to_say}` | `zrb_init.py` | The instruction added to a turn she starts, after the situation it answers. `{nothing_to_say}` is the one placeholder it reads; an empty value asks for nothing at all, so a white-labeled avatar can ask for another voice. |
| `ZRB_AVATAR_AWAKE_HOURS` | `8-22` | `zrb_init.py` | Hours the talk-first sources may speak (`idle`, `camera`, `view`). Not the nudge's: see below. |
| `ZRB_AVATAR_NUDGE` | `auto` | `zrb_init.py` | The nudge: `auto` checks its rules and offers a turn when one is true, `off` never does. |
| `ZRB_AVATAR_NUDGE_SECONDS` | `600` | `zrb_init.py` | Seconds between checking those rules — free, no model call. |
| `ZRB_AVATAR_NUDGE_HOURS` | `7-24` | `zrb_init.py` | Hours the nudge may speak, as `first-last`; 24 is the end of the day. |
| `ZRB_AVATAR_NUDGE_LUNCH` | `12:00-13:30` | `zrb_init.py` | The lunch reminder's window. |
| `ZRB_AVATAR_NUDGE_BED` | `22:30-24:00` | `zrb_init.py` | The bedtime reminder's window. |
| `ZRB_AVATAR_NUDGE_QUIET` | `90` | `zrb_init.py` | Quiet minutes before that is worth asking about. |
| `ZRB_AVATAR_NUDGE_AWAY` | `20` | `zrb_init.py` | Minutes away that make coming back worth noticing. |
| `ZRB_AVATAR_NUDGE_BACK` | `10` | `zrb_init.py` | Minutes after coming back that "you are back" stays true. |
| `ZRB_AVATAR_CAMERA_INTERVAL` | `20` | `zrb_init.py` | Seconds between camera looks; each one lights the camera. |
| `ZRB_AVATAR_CAMERA_PRESENT_BYTES` | `25000` | `zrb_init.py` | Photo size above which someone counts as present; tune it for your camera and room. |
| `ZRB_AVATAR_MOUTH_GAIN` | `4` | `client.py` | How wide the mouth opens for a given loudness: raise it if the mouth barely opens, lower it if it stays wide open. |
| `ZRB_AVATAR_SUBTITLE` | `on` | `client.py` | Caption under her in the window: the sentence she is speaking, one at a time, and — while she is not speaking — what she is doing (`(thinking…)`, `(working…)`, `(needs you…)`, `(waiting for your answer…)`), dimmed and in italics so it is never read as something she said. `off` sends neither; zrb's own terminal still shows the reply. |
| `ZRB_AVATAR_URL` | `http://127.0.0.1:7777/event` | `client.py` | Where zrb sends events. |
| `ZRB_AVATAR_HOST` | `127.0.0.1` | server | The address the server listens on. |
| `ZRB_AVATAR_PORT` | `7777` | server, `zrb-avatar` | The server's port (change `ZRB_AVATAR_URL` to match). |
| `ZRB_AVATAR_HOME` | `~/.zrb/avatar` | server, `zrb_init.py` | Your folder: models in `models/`, animations in `anim/`, and her own answers — `autonomy.json` (what she agreed to do unasked), `model` (the model she last chose) and `nudge.json` (today's nudges, so a restart does not repeat them). |
| `ZRB_AVATAR_MODELS` | empty | server | More model folders, searched before `<home>/models`; separated by `:` (`;` on Windows). `~` is expanded. |
| `ZRB_AVATAR_ANIMS` | empty | server, `zrb_init.py` | More animation folders, searched before `<home>/anim` and the shipped ones. |
| `ZRB_AVATAR_MUSIC` | `auto` | server | `auto` dances while media plays — Windows' media controls (also from WSL), `playerctl` on Linux, or `media-control` (else Spotify/Music by AppleScript) on macOS; `off` only dances on `zrb-avatar music on`. |
| `ZRB_AVATAR_APP` | per platform | `zrb-avatar` | The program to open as the window, instead of the Windows build, the macOS app or Electron from source. |

### zrb settings it depends on

| Setting | Why it matters |
| --- | --- |
| `ZRB_INIT_SCRIPTS` | Must include `$(zrb-avatar init-path)`; that is what turns the face on. `zrb_avatar/zrb_init.py` is all zrb needs. |
| `ZRB_LLM_EMOTION_ENABLED` | `true` makes zrb tag replies with an emotion, which she shows. Off by default. Needs a zrb with the `Emotion` hook. |
| `ZRB_LLM_SPEECH_BACKEND` / `ZRB_LLM_SPEECH_VOICE` (or `CFG.DEFAULT_LLM_SPEECH_BACKEND` / `_VOICE` in an init script) | The voice. For the mouth to move it must be one zrb plays itself (piper, openai, gemini), not `say` or `termux`. |
| `ZRB_LLM_SPEECH_PLAYER` | Leave it at `auto` (the default), with zrb's voice extra installed (`zrb[voice]`, for sounddevice): then zrb plays the audio itself and the mouth can follow it. `command` hands the audio to a player program, hidden from zrb. |
| `ZRB_HOOKS_ENABLED` | Must stay on (the default): the states and speech ride on zrb's hooks. |
| zrb's camera settings (`ZRB_LLM_CAMERA_*`) | Used by `ZRB_AVATAR_TALK_FIRST=camera` to take its photos. |

`zrb_init.py` owns zrb's speech (`enable_speech`), so other init scripts should only choose the
voice and not call `enable_speech` themselves; whichever script calls it last would win.

### Command line and URL

| Where | Option | Example | What it does |
| --- | --- | --- | --- |
| `zrb-avatar` | model name | `zrb-avatar rem` | (Re)starts the server and opens a window with that model; without a name, the first model alphabetically. |
| `zrb-avatar serve` | | | Runs the server in the foreground. |
| `zrb-avatar action <name>` | | `zrb-avatar action Wave`, `zrb-avatar action thinking/idea` | Plays an action once, or any clip by `<pool>/<name>`; handy for trying a new clip. |
| `zrb-avatar actions [pool]` | | `zrb-avatar actions idle` | Lists the animations by pool (or one pool, nested ones included), with how long each takes and what it looks like. |
| `zrb-avatar snapshot [file]` | | `zrb-avatar snapshot me.png` | Saves the window as you see it (default `zrb-avatar-snapshot.png`), and prints what she is doing. |
| `zrb-avatar music on\|off\|auto` | | `zrb-avatar music off` | Dance while idle, stop, or follow what the system plays again. The choice holds until `auto`. |
| `zrb-avatar models` | | | Lists the models, marking the one showing with `*`. |
| `zrb-avatar model <name>` | | `zrb-avatar model rem` | Switches every open window to that model, and windows opened later too. |
| `zrb-avatar dirs` | | | Lists the model and animation folders in search order, and what they hold. |
| `zrb-avatar anim` | `reset` | `zrb-avatar anim reset` | Lists the animation folder, with which clips are your own; `reset` puts the shipped ones back, keeping every clip you edited. |
| `zrb-avatar init-path` | | | Prints the path of `zrb_init.py`, for `ZRB_INIT_SCRIPTS`. |
| `zrb-avatar.exe`, the macOS app, `electron app` | model name | `zrb-avatar.exe rem` or `rem.vrm` | The model to show (letters, digits, `-`, `_`; `.vrm` optional). |
| same | server URL | `zrb-avatar.exe http://localhost:7779` | Another server than `http://localhost:7777`. |
| the page | `?model=<name>` | `http://localhost:7777/?model=rem` | The model, in a browser; this name wins over the remembered one. |
| the page | `?app` | (set by the app) | See-through background, dragging moves the window, no status text. |

### Files and folders

Models and animations are looked up in folders, in this order; the first file with a given name
wins, so your own files override the shipped ones. `zrb-avatar dirs` shows the list.

| Kind | Folders |
| --- | --- |
| models | `$ZRB_AVATAR_MODELS`, `~/.zrb/avatar/models` |
| animations | `$ZRB_AVATAR_ANIMS`, `~/.zrb/avatar/anim`, the package's `anim/` |

The animations work the other way round from the models: **the first run copies the shipped clips
into `~/.zrb/avatar/anim`**, so the folder you can edit is the folder that plays.

- A copy follows the package only while it is still byte for byte what was copied: improve a clip in
  a new release and your untouched copy is replaced, while one you edited is left alone and reported
  as `kept … (edited)`.
- `.template.json` beside them records what was copied and from which version. A manifest that goes
  missing reads as "every clip is yours", so nothing is overwritten on the strength of a file that
  cannot be read.
- `zrb-avatar anim` shows the folder and which clips are your own; `zrb-avatar anim reset` puts the
  shipped clips back — including ones you deleted — while keeping what you edited.

| Path | Convention |
| --- | --- |
| `<models folder>/<name>.vrm` | A model, opened by `<name>`. Only files directly in the folder are served. |
| `<models folder>/<name>.json` | Optional notes about that model. `{"covers": ["chest", "hips", "thighs"]}` says what her outfit covers: a close look there is then not a shy one, and the camera's geometry alone — which knows nothing about clothes — no longer blushes at a hoodie. Without the file nothing is covered. |
| `<anim folder>/<pool>/<name>.vrma` | An animation; the pool (a subfolder, which may nest: `actions/greetings/hi.vrma`) says when it plays. See [Animations](#animations). |

### In the code

Constants with no setting of their own; edit the file to change them.

| File | Constant | Value | What it does |
| --- | --- | --- | --- |
| `js/main.js` | `FPS` | 30 (`?fps=60` on the page) | Frames drawn per second; drawing is most of the CPU and GPU she uses. |
| `js/main.js` | `LOOK` | cyan, violet, green, red, bright cyan | The glow colour per state. |
| `js/framing.js` | default size | 60% of the screen | How big the app window starts, before zooming. |
| `js/expression.js` | blinking | every 2–6 s, 0.12 s long | How often and how long she blinks. |
| `js/expression.js` | smile | every 25–50 s for 3 s at 0.5; 2 s after a task | The soft `relaxed` smile. |
| `js/expression.js` | thinking tilt / speaking nod | 0.12 / 0.04 rad | How far the head tilts while thinking and nods with the voice. |
| `js/expression.js` | emotion | 0.6 for 6 s | How strong and how long she shows an emotion. |
| `js/expression.js` | gaze | jumps every 0.6–3 s; glances away 18% (thinking 55%, speaking 8%) | How her eyes move around you. |
| `js/view.js` | `CLOSE` / `SHY` | under 0.6 of her height across / chest, hips, thighs | What counts as a close-up, and of what she is shy. |
| `js/blush.js` | `OUT`, `SIZE`, `LIFT`, `DOWN` | 0.3, 0.32, 0.01, 0.45 | Where the blush sits, in eye widths; nudge them for a model whose blush sits off. |
| `js/motion.js` | crossfade | 0.6 s (actions 0.3 s) | Blend time between animations. |
| `js/motion.js` | loop speed | 0.9–1.1× (dances 1×) | Each loop plays a little faster or slower, so repeats never match exactly. |
| `client.py` | `MOUTH_STEP` | 0.04 s | Audio per mouth value; the values are windowed across chunk boundaries, so one value is one step whatever the chunking. |
| `zrb_init.py` | `AWAKE_HOURS` | 8:00–21:59 | When she may speak first. |
| `zrb_init.py` | idle check / away time | every 60 s / 5 min | How often `idle` checks; how long you must be away before `camera` greets you back. |
| `zrb_init.py` | `NOTHING_TO_SAY` | `…` | The reply she gives, unspoken, when nothing is worth saying. |
| `app/main.js` | window | 440×640, bottom right | Where the window starts; the page then sizes it to her. |
| `app/main.js` | on top | `screen-saver` level | Stays above full-screen windows too. |

## How it fits together

```
 zrb chat  ── POST /event ──▶  zrb-avatar server  ── page, models, animations, live events ──▶  window (face.html)
 + zrb_init.py                 (port 7777)                                                    in the app or a browser
```

zrb runs in WSL on Windows, but a window that stays on top has to be a Windows program. Windows can
reach a server in WSL (not the other way around in WSL's default NAT mode), so the server runs next
to zrb and the window connects to it.

| File | Role |
| --- | --- |
| `zrb_avatar/zrb_init.py` | The zrb side, loaded through `ZRB_INIT_SCRIPTS`: zrb's speech (moving the mouth, silent for "nothing to say"), the face hooks, her tools, and the optional triggers that let her speak first. |
| `zrb_avatar/client.py` | Sends events to the server: state and emotion from zrb's hooks, mouth openness from the loudness of the speech audio. Skips sending when the server is down. |
| `zrb_avatar/server.py` | The server (Python standard library only): serves the page, the models and the animations from their folders, and relays every event to the open windows over Server-Sent Events. |
| `zrb_avatar/face.html` | The page: its style and the three.js / three-vrm imports; the code is in `js/`. |
| `zrb_avatar/js/main.js` | Wires it together: loads the model, follows the server's events, draws each frame. |
| `zrb_avatar/js/clips.js` | Loads the `.vrma` animations as clips for the model. |
| `zrb_avatar/js/motion.js` | Which clip plays: the state's pool looping, actions once on top, a cover held at its last frame. |
| `zrb_avatar/js/expression.js` | Her face: blinks, lip sync, eye contact, smiles, emotions and the five blends behind them, head tilt and nods. |
| `zrb_avatar/js/view.js` | What the user is looking at: the part of her at the middle of the window, from which side, how close. |
| `zrb_avatar/js/pose.js` | Her body moved by zrb (`AvatarPose`): hands placed by IK, head and posture, over the clip. |
| `zrb_avatar/js/blush.js` | Her blush, placed from where her blink and mouth move her face. |
| `zrb_avatar/js/framing.js` | The camera, and the app window's size: measures every clip's reach once, then frames it from any angle. |
| `zrb_avatar/__main__.py` | The `zrb-avatar` command. |
| `zrb_avatar/anim/` | The animations that ship with the package (`.vrma` files, by pool). |
| `app/` | The desktop window: an Electron shell (`main.js`) that shows the page see-through and always on top; `preload.js` lets that page size the window, and nothing else. |
| `test_face.py` | The self-check. |
| `zrb_init.py`, `.github/workflows/` | CI: the tests and lint on every push and pull request, and an LLM code review (the repository's own `review` and `submit-comment` zrb tasks, in the root `zrb_init.py`) posted on pull requests. |

## Test

```sh
python3 test_face.py                        # with a Python that has zrb installed
```

It checks the search paths, the event relay, the remembered model choice, the music state and
watcher (`media-control` and AppleScript), and that every shipped `.vrma` is real glTF with the
humanoid bones and valid channel references.

CI (`.github/workflows/test.yml`) runs it with a pyflakes lint on every push to `main` and every pull
request: Python 3.11–3.14 on Linux, and 3.13 on Windows and macOS. On pull requests from a branch of
this repository (not a fork), `code-review.yml` also has zrb review the diff and post the report as a
comment (`zrb submit-comment`, defined in the root `zrb_init.py`); it needs the `OPENAI_API_KEY`
secret (and optionally `ZRB_LLM_MODEL`), and skips itself without it.

## Releasing

Bump the version in `pyproject.toml` and `app/package.json`, then push a tag:
`git tag 0.1.1 && git push origin 0.1.1`. `.github/workflows/release.yml` opens the GitHub release,
publishes the package to PyPI and attaches the app zips to it; the tag must equal `pyproject.toml`'s
version or the run stops before it publishes anything.

PyPI publishing is trusted publishing, so first add this repository, the workflow `release.yml` and
the environment `pypi` as a trusted publisher of `zrb-avatar` on pypi.org. Publishing by hand first
(`python -m build && twine upload dist/*`) is also fine: the workflow notices the version is already
on PyPI, skips that step and still releases the desktop app.

## Credits

The models used during development, not included: *ram* and *rem made in mexico* (Re:Zero fan models)
by pon3mx on [VRoid Hub](https://hub.vroid.com/en); search for them there. Ram: use, changes,
commercial use and redistribution allowed, no credit needed. Rem: the same but no commercial use.
Both have mouth, blink and emotion expressions. They are fan models of the Re:Zero characters, so
keep them on your own machine.
