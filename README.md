# zrb-avatar

A companion for [zrb](https://github.com/state-alchemists/zrb): a 3D character in a small, see-through, always-on-top window. She idles naturally (breathing, shifting her weight, glancing around, stretching), shows what zrb is doing and how it feels, moves her mouth with zrb's voice, dances while you play music, gestures with your own animations, and can start a conversation herself.

## Quick start

```sh
pipx install zrb-avatar          # or: pip install zrb-avatar, next to zrb; from a checkout: pip install -e .

# once, e.g. in ~/.zshrc: load the zrb side into every zrb chat
export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-avatar init-path)"

mkdir -p ~/.zrb/avatar/models && cp my.vrm ~/.zrb/avatar/models/   # a model: see Changing the model
zrb-avatar my                    # (re)start the server, open a window with my.vrm
zrb chat
```

The window is a small desktop app; download it from the [releases](https://github.com/state-alchemists/zrb-avatar/releases) or build it (see [The desktop app](#the-desktop-app)). Without it, `zrb-avatar` opens the page in your browser instead.

`zrb-avatar` opens the Windows build from WSL, the installed app on macOS, or Electron from `app/` in a source checkout (`ZRB_AVATAR_APP` overrides the program). It restarts the server each time, so the server always runs the current code with your current settings; open windows reload by themselves. The server keeps running across chats; its output goes to `zrb-avatar.log` in the temp folder, and `pkill -f "[z]rb_avatar serve"` stops it (or Ctrl-C if you started it with `zrb-avatar serve` in a terminal). The order doesn't matter: the window waits for the server, reloads itself when the server restarts, and zrb skips sending while either is missing. If a model can't be loaded, the window says so in red and tries again every few seconds.

## How it fits together

```
 zrb chat  ── POST /event ──▶  zrb-avatar server  ── page, models, animations, live events ──▶  window (face.html)
 + zrb_init.py                 (port 7777)                                                    in the app or a browser
```

| File | Role |
| --- | --- |
| `zrb_avatar/zrb_init.py` | The zrb side, loaded through `ZRB_INIT_SCRIPTS`: zrb's speech (moving the mouth, silent for "nothing to say"), the face hooks, her tools (`AvatarListActions`, `AvatarAction`, `AvatarPose`, `AvatarRest`, `AvatarBlink`, `AvatarFace`, `AvatarAutonomy`, `AvatarLook`, `AvatarListModels`, `AvatarModel`, `AvatarStatus`), and the optional triggers that let her speak first. |
| `zrb_avatar/client.py` | Sends events to the server: state and emotion from zrb's hooks, mouth openness from the loudness of the speech audio. Skips sending when the server is down. |
| `zrb_avatar/server.py` | The server (Python standard library only): serves the page, the models and the animations from their folders, and relays every event to the open windows over Server-Sent Events. |
| `zrb_avatar/face.html` | The page: its style and the three.js / three-vrm imports; the code is in `js/`. |
| `zrb_avatar/js/main.js` | Wires it together: loads the model, follows the server's events, draws each frame. |
| `zrb_avatar/js/clips.js` | Loads the `.vrma` animations as clips for the model. |
| `zrb_avatar/js/motion.js` | Which clip plays: the state's pool looping, actions once on top. |
| `zrb_avatar/js/expression.js` | Her face: blinks (her own rhythm, and a wink or slow blink she chose), lip sync, eye contact, smiles, emotions and the five blends behind them (`pout`, `smirk`, `worried`, `delighted`, `sleepy`), head tilt and nods. |
| `zrb_avatar/js/view.js` | What the user is looking at: the part of her at the middle of the window, from which side, how close. |
| `zrb_avatar/js/pose.js` | Her body moved by zrb (`AvatarPose`): hands placed by IK, head and posture, over the clip. |
| `zrb_avatar/js/blush.js` | Her blush, placed from where her blink and mouth move her face. |
| `zrb_avatar/js/framing.js` | The camera, and the app window's size: measures every clip's reach once, then frames it from any angle. |
| `zrb_avatar/__main__.py` | The `zrb-avatar` command. |
| `zrb_avatar/anim/` | The animations that ship with the package (`.vrma` files, by pool). |
| `app/` | The desktop window: an Electron shell (`main.js`) that shows the page see-through and always on top; `preload.js` lets that page size the window, and nothing else; `fbx2vrma.mjs` converts a Mixamo FBX to a `.vrma` (`npm run fbx2vrma`), not part of the app. |
| `test_face.py` | The self-check. |


| `zrb_init.py`, `.github/workflows/` | CI: the tests and lint on every push and pull request, and an LLM code review (the repository's own `review` and `submit-comment` zrb tasks, in the root `zrb_init.py`) posted on pull requests. |

zrb runs in WSL on Windows, but a window that stays on top has to be a Windows program. Windows can reach a server in WSL (not the other way around in WSL's default NAT mode), so the server runs next to zrb and the window connects to it.

## Running by hand

What `zrb-avatar` does, per platform:

```sh
zrb-avatar serve &                                                                          # the server
/mnt/c/Users/$USER/AppData/Local/zrb-avatar/zrb-avatar.exe rem </dev/null &>/dev/null &!      # Windows, from WSL
open -n -a zrb-avatar --args rem                                                            # macOS, installed app
~/zrb-avatar/app/node_modules/.bin/electron ~/zrb-avatar/app rem                              # Linux, or any OS from source
```

The window takes a model name (`rem` or `rem.vrm`; without one, the first model alphabetically) and optionally another server URL (`http://localhost:7779`). Start the Windows build detached as above, or zsh stops it ("suspended (tty input)"). Instead of the app you can open `http://localhost:7777/?model=rem` in a browser: the same page, but not see-through or always on top.

## Using the window

- **Move**: drag it. **Close**: Alt+F4 on Windows, Cmd+Q on macOS.
- **Camera angle**: click the window, then use the keys in [Keyboard](#keyboard-click-the-window-first). The angle is remembered.

## What she shows

| Glow | State | Sent by |
| --- | --- | --- |
| cyan | idle | `Stop`, `StopFailure`, `SessionStart` |
| violet (head tilts) | thinking | `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure` |
| green | running a tool | `PreToolUse` |
| red | needs attention | `Notification`, `PermissionRequest` |
| bright cyan | speaking | mouth events |

- **Voice**: the mouth follows the loudness of the speech, in 40 ms steps — one value per step of audio, windowed across the chunk boundaries of the stream, so it ends when the voice does — and she nods slightly while speaking. It needs a voice zrb plays itself (piper, openai, gemini); `say` and `termux` play the audio themselves, so zrb never sees it. The mouth still starts slightly ahead of the voice, because zrb reads audio ahead of playing it; fixing that exactly needs a frame observer in zrb's `PcmUtterance._fill`.
- **Face**: she blinks every few seconds, and her eyes are on you (the camera) the way real eyes are: small jumps around your face, a glance away now and then (often with a blink), more of them while thinking, fewer while speaking; she smiles softly every 25–50 seconds and after finishing a task.
- **Body**: each state loops its own animations, falling back to the idles; see [Animations](#animations). On the first run of a conversation she plays the `Greeting` action if there is one; later turns don't greet again.
- **Emotion**: with zrb's emotion on (`ZRB_LLM_EMOTION_ENABLED=true`), zrb opens each reply with an emoji, and she shows it for 6 s with the matching VRM expression (`happy`, `sad`, `angry`, `surprised`, `relaxed`). The user's own emotion isn't shown: her reply answers it.
- **Her own choice** (autonomy): four switches she sets herself with `AvatarAutonomy` and `GET /autonomy` reports — `dance` while music plays, `blush` (and the surprised face) when you look closely at her, `emotion` for the expressions zrb gives her replies, `greeting` when a conversation starts. Three are on until she says otherwise, because they answer something you did; `dance` starts off, because its trigger is the media watcher rather than you and the watcher cannot tell a song from a talk. A switch she moves is written to `<home>/autonomy.json` — a consent withdrawn tonight is not something tomorrow's restart quietly restores — and only the switches she moves are written, so a default can change later without a file that never mentioned a switch pinning the old one. This is not the same decision as `zrb-avatar music on|off` or `ZRB_AVATAR_TALK_FIRST`: those say what may happen, this says what she is willing to do.
- **Answering a look she has refused** (off by default): with `ZRB_AVATAR_REFLEX=on`, when the window reports a look she has said no to — a close-up of an uncovered chest, hips or thighs, or her hips seen from below, which is what `<name>.json`'s `covers` decides for the outfit she is in — her body covers itself: forearms crossed over her chest and hips, her face turned away, held until the look stops being that one. The answer rides the view event the page already sends, so it does not wait for a reply, a model or a turn, and it holds when zrb is busy, out of tokens, or not running at all. It is a refusal, not a lock: the camera is still yours, you can zoom straight back in, and she covers again. And it answers the view you settle on rather than every notch of the wheel, because the page reports a view once it has stopped moving — which is also why it is not a twitch.
- **Gestures**: zrb gets her tools, all auto-approved: `AvatarListActions` lists her animations by pool with what each looks like and how long it takes, `AvatarAction` plays one once alongside its words (an action by name: `Greeting`, `Goodbye`, …; any other clip by `pool/name`: `thinking/Thinking`), `AvatarPose` moves her body itself, with no animation file — for a few seconds, or with `seconds=0` until `AvatarRest` lets go: each hand to a place (`chest` and `hips` cover her, `chin`, `cheek`, `mouth`, `head`, `hip`, `belly`, `forward`, `up`, `wave`), her head (`down`, `up`, `left`, `right`, `tilt-left`, `tilt-right`, `away`) and her posture (`hunch`, `lean-forward`, `lean-back`, `stand-tall`); or joint by joint, in degrees held within a natural range: fingers (`fist`, `point`, `peace`, `thumbs-up`, `ok`, `open`, `relaxed`, or each finger's curl), wrists, elbows, arms (`[raise, forward]`), shoulders, spine, head (`[nod, turn, tilt]`), legs, knees and ankles; hands placed by IK on top of whatever clip plays, so it fits any model (try it: `curl -d '{"type":"pose","rightHand":"chest","leftHand":"hips","head":"away","body":"hunch","seconds":5}' localhost:7777/event`); and `AvatarLook` shows zrb her window as you see it (her pose and expression from your side, plus what she is doing and which part of her you are looking at), to check how a gesture comes across or when you ask how she looks. `AvatarListModels` lists the avatars (the `.vrm` models) and which one is showing, and `AvatarModel` switches every open window to another one, when you ask her to look different. `AvatarAutonomy` decides, and says, what she is willing to do on her own (see below). `AvatarStatus` says what the face last reported — whether anyone is at the desk and how that was decided, what you last turned the camera to, which avatar is showing, and whether music plays — for asking again mid-turn. `AvatarRest` lets go of a pose held with `seconds=0`; the server keeps such a pose, so a window that reloads comes back in the posture she chose rather than standing up mid-thought. `AvatarBlink` blinks or winks deliberately — `eyes="both"`, `"left"` or `"right"`, `seconds` from 0.08 to 2 — for the blink that is the point rather than punctuation: a wink, or a slow one that says *I heard you*. It is hers to call and nothing automatic calls it; her own blink keeps its rhythm, and the next one waits a couple of seconds past the one she chose. `AvatarFace` puts a feeling on her face on purpose — the five presets, or one of five blends no model carries as a shape: `pout` (angry and sad at half each), `smirk` (happy and angry), `worried` (sad and surprised), `delighted` (happy and surprised) and `sleepy` (relaxed and sad); `strength="soft"`, `"normal"` or `"strong"` says how hard it is worn, half or one and a half. Nothing automatic calls that either, and it fades after six seconds. Name only the joints that matter and the clip goes on breathing the rest — naming every joint gives a statue, not a person. The actions and models are read on every call, so new ones work without restarting zrb. `AvatarLook` sees only her own window, never your screen, and needs a model that reads images (or zrb's `ZRB_LLM_MULTIMODAL_MODEL`).
- **Music**: when something plays music (a browser, Spotify, …) she dances with the `dance/` animations while idle; anything zrb does takes over, and she goes back to dancing after. The server watches the system's media status: Windows' media controls (also from WSL), `playerctl` on Linux, or on macOS `media-control` (`brew install media-control`; it also sees a browser tab playing) and else Spotify and Music by AppleScript (which asks permission to control them the first time). Any playing media counts, a talk video too — which is why `dance` starts off and has to be hers (`AvatarAutonomy(dance=true)`). `zrb-avatar music on` / `off` decides it yourself, and that choice holds: the watcher goes on reporting what the system plays and is ignored until `zrb-avatar music auto` hands the question back. `ZRB_AVATAR_MUSIC=off` stops the watching.
- **Talking first** (off by default): with `ZRB_AVATAR_TALK_FIRST=idle` she speaks up after you've been quiet for a while during waking hours (8:00–22:00); with `camera`, when you sit back down after being away (whichever `ZRB_AVATAR_PRESENCE` mode is watching, a photograph or the clock); with `view`, when you turn the camera to a close look at her chest or hips, or at her hips from below (she reacts in her own words and emotion; her face already shows surprise and a blush the moment the view settles, `view` or not). She never interrupts a running turn or her own speech, waits a cooldown between such turns, and replies `…` (shown, not spoken) when there's nothing worth saying.
- **Nudging** (its own clock, on by default): every ten minutes a free rule is checked — lunch between 12:00 and 13:30, bedtime from 22:30, you back at the desk after twenty minutes away, ninety minutes of quiet, or a turn that ended in an error. Only when one of those is true does a turn start, and that turn is where it is decided whether there is anything worth saying: a reply of `…` is shown and not spoken. Her hours are her own (`ZRB_AVATAR_NUDGE_HOURS`, 7:00–24:00) because the shared waking hours end at 22:00, and what has been said is kept in `<home>/nudge.json` so a restart does not say it twice. `ZRB_AVATAR_NUDGE=off` turns it off.

- **What she knows** (always on, unlike speaking first): whatever the camera, the avatar and the music last reported is written down whether or not she is allowed to speak in that turn, and one line of it goes in front of every turn — what you last turned the camera to, whether anyone is at the desk, which avatar is showing, whether music plays. `AvatarStatus` answers the same thing mid-turn. Sensing is one loop now, so a view turn, a returning-you turn and an idle turn share one gate instead of keeping three copies of it, and it runs beside her turns rather than inside one: a reply she is in the middle of does not blind her, and a poll that fails leaves the last answer standing instead of reporting an unknown avatar and silence. Whether anyone is at the desk is decided only when `ZRB_AVATAR_PRESENCE` says so, and by the means you choose: `size` takes a photograph, whose light every look switches on and whose JPEG size stands in for a face; `idle` reads how long since the keyboard or mouse last moved, which lights nothing and needs no photograph (on WSL that is the Windows session's own clock, through one PowerShell process kept running). Both are guesses, so the line reports them as guesses and never claims to have seen a face; with `off` nothing looks at all.

The mouth, the blink and the smile need the model's `aa`, `blink` and `relaxed` expressions. A model without them still nods while speaking.

## Wiring it into zrb

`zrb_avatar/zrb_init.py` is all zrb needs; load it through `ZRB_INIT_SCRIPTS` as in [Quick start](#quick-start) (`zrb-avatar init-path` prints where it is). It owns zrb's speech (`enable_speech`), so other init scripts should only choose the voice (`CFG.DEFAULT_LLM_SPEECH_BACKEND`, `CFG.DEFAULT_LLM_SPEECH_VOICE`) and not call `enable_speech` themselves; whichever script calls it last would win.

## Who moves her

Every movement comes from somewhere, and the order matters: a held pose outranks an animation, an
animation outranks the loop, and her own consent outranks all of it — nothing below can put
something back that she has said no to.

| # | what you see | who decided it | how to change it |
| --- | --- | --- | --- |
| 1 | a posture she keeps | `AvatarPose(seconds=0)` — hers, above the clips *and* above the dance | `AvatarRest`, or another pose |
| 2 | a posture for a few seconds | `AvatarPose(seconds=1..30)` | the same, timing out on its own |
| 3 | an action, once | `AvatarAction`, `Greeting` at the start of a chat, or the clip an emotion carries | `AvatarAction` again; the loop returns after |
| 4 | the loop | the state zrb is in (`thinking`, `tool`, `attention`, `speaking`), else `dance` while music plays **and** she consents, else `idle` | the state comes from zrb; `zrb-avatar music on\|off` and `AvatarAutonomy(dance=...)` decide the dance |
| 5 | her mouth | the voice zrb is speaking with | the speech backend |
| 6 | her expression | zrb's emotion for a reply (if `autonomy.emotion`), else the clip's own | `ZRB_LLM_EMOTION_ENABLED`, `AvatarAutonomy(emotion=...)` |
| 7 | a feeling she chose | `AvatarFace` — a preset, or one of five blends (`pout`, `smirk`, `worried`, `delighted`, `sleepy`), at `soft`, `normal` or `strong` — nothing automatic calls it | it fades after six seconds |
| 8 | a blink or wink she chose | `AvatarBlink` — nothing automatic calls it | it ends on its own (0.08–2 s), and the rhythm resumes a couple of seconds later |
| 9 | a blush, and a surprised face | the view — a close look at an uncovered `chest`, `hips` or `thighs` — and `autonomy.blush` | the camera, the model's `{"covers": ...}` notes, `AvatarAutonomy(blush=...)` |
| 10 | gaze, blinking, the small smile | `js/expression.js` itself, always — except where the blink above is holding her lids | — |

The loop under all of it keeps breathing: a pose names only the joints it means, and everything
else goes on moving, which is what keeps a held pose from looking like a statue.

## Configuration

### Environment variables

The zrb-side names follow zrb's `CFG.ENV_PREFIX` (`ZRB` by default). The server and the `zrb-avatar` command run without zrb, so their names are fixed.

| Variable | Default | Read by | What it does |
| --- | --- | --- | --- |
| `ZRB_AVATAR_TALK_FIRST` | empty (never) | `zrb_init.py` | What may start a conversation: any of `idle`, `camera`, `view`, comma-separated. |
| `ZRB_AVATAR_VIEW_COOLDOWN` | `30` | `zrb_init.py` | Seconds between turns `view` starts. It skips the waking hours and the long cooldown of the others: it answers what you just did. |
| `ZRB_AVATAR_PRESENCE` | `off` | `zrb_init.py` | How to tell whether you are at the desk: `off` (nothing looks), `size` (a photograph, whose light every look switches on) or `idle` (the time since the last keypress or mouse move — no camera at all). `ZRB_AVATAR_TALK_FIRST=camera` needs one of the two. |
| `ZRB_AVATAR_PRESENCE_INTERVAL` | `5` | `zrb_init.py` | Seconds between looks with `ZRB_AVATAR_PRESENCE=idle`. A reading costs a number, not a photograph. |
| `ZRB_AVATAR_PRESENCE_SECONDS` | `300` | `zrb_init.py` | Seconds without input that still count as being at the desk: reading a page is being here, a lunch break is not. |
| `ZRB_AVATAR_REFLEX` | `off` | `server.py` | Answer a look she has refused: a close-up of an uncovered chest, hips or thighs, or her hips from below — she covers herself at once and lets go when the look does. `off` says nothing. |
| `ZRB_AVATAR_MODEL_SECONDS` | `5` | `zrb_init.py` | Seconds between asking the server which avatar is showing and whether music plays, for the line she knows. |
| `ZRB_AVATAR_IDLE_MINUTES` | `45` | `zrb_init.py` | Quiet minutes before `idle` speaks. |
| `ZRB_AVATAR_COOLDOWN` | `1200` | `zrb_init.py` | Seconds between conversations she starts herself. |
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
| `ZRB_AVATAR_SUBTITLE` | `on` | `client.py` | Caption the sentence she is speaking under her in the window, one sentence at a time. `off` sends nothing; zrb's own terminal still shows the reply. |
| `ZRB_AVATAR_URL` | `http://127.0.0.1:7777/event` | `client.py` | Where zrb sends events. |
| `ZRB_AVATAR_HOST` | `127.0.0.1` | server | The address the server listens on. |
| `ZRB_AVATAR_PORT` | `7777` | server, `zrb-avatar` | The server's port (change `ZRB_AVATAR_URL` to match). |
| `ZRB_AVATAR_HOME` | `~/.zrb/avatar` | server, `zrb_init.py` | Your folder: models in `models/`, animations in `anim/`, and her own answers — `autonomy.json` (what she agreed to do unasked), `model` (the model she last chose, so a restart does not put the folder's first name back) and `nudge.json` (today's nudges, so a restart does not repeat them). |
| `ZRB_AVATAR_MODELS` | empty | server | More model folders, searched before `<home>/models`; separated by `:` (`;` on Windows). `~` is expanded. |
| `ZRB_AVATAR_ANIMS` | empty | server, `zrb_init.py` | More animation folders, searched before `<home>/anim` and the shipped ones. |
| `ZRB_AVATAR_MUSIC` | `auto` | server | `auto` dances while media plays — Windows' media controls (also from WSL), `playerctl` on Linux, or `media-control` (else Spotify/Music by AppleScript, which asks permission the first time) on macOS; `off` only dances on `zrb-avatar music on`. |
| `ZRB_AVATAR_APP` | per platform | `zrb-avatar` | The program to open as the window, instead of the Windows build, the macOS app or Electron from source. |

### zrb settings it depends on

| Setting | Why it matters |
| --- | --- |
| `ZRB_INIT_SCRIPTS` | Must include `$(zrb-avatar init-path)`; that's what turns the face on. |
| `ZRB_LLM_EMOTION_ENABLED` | `true` makes zrb tag replies with an emotion, which she shows. Off by default. Needs a zrb with the `Emotion` hook. |
| `ZRB_LLM_SPEECH_BACKEND` / `ZRB_LLM_SPEECH_VOICE` (or `CFG.DEFAULT_LLM_SPEECH_BACKEND` / `_VOICE` in an init script) | The voice. For the mouth to move it must be one zrb plays itself (piper, openai, gemini), not `say` or `termux`. |
| `ZRB_LLM_SPEECH_PLAYER` | Leave it at `auto` (the default), with zrb's voice extra installed (`zrb[voice]`, for sounddevice): then zrb plays the audio itself and the mouth can follow it. `command` hands the audio to a player program, hidden from zrb. |
| `ZRB_HOOKS_ENABLED` | Must stay on (the default): the states and speech ride on zrb's hooks. |
| zrb's camera settings (`ZRB_LLM_CAMERA_*`) | Used by `ZRB_AVATAR_TALK_FIRST=camera` to take its photos. |

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
| `zrb-avatar model <name>` | | `zrb-avatar model rem` | Switches every open window to that model (`{"type": "model", "value": "rem"}`), and windows opened later too, and writes the choice to `<home>/model` so it survives a restart. |
| `zrb-avatar dirs` | | | Lists the model and animation folders in search order, and what they hold. |
| `zrb-avatar anim` | `reset` | `zrb-avatar anim reset` | Lists the animation folder, with which clips are your own; `reset` puts the shipped ones back, keeping every clip you edited. |
| `zrb-avatar init-path` | | | Prints the path of `zrb_init.py`, for `ZRB_INIT_SCRIPTS`. |
| `zrb-avatar.exe`, the macOS app, `electron app` | model name | `zrb-avatar.exe rem` or `rem.vrm` | The model to show (letters, digits, `-`, `_`; `.vrm` optional). |
| same | server URL | `zrb-avatar.exe http://localhost:7779` | Another server than `http://localhost:7777`. |
| the page | `?model=<name>` | `http://localhost:7777/?model=rem` | The model, in a browser; this name wins over the remembered one. A model chosen by `zrb-avatar model` or `AvatarModel` is remembered across restarts. |
| the page | `?app` | (set by the app) | See-through background, dragging moves the window, no status text. |

### Keyboard (click the window first)

| Key | What it does |
| --- | --- |
| ← → | Orbit around her. |
| ↑ ↓ | Orbit over her head or under her feet, all the way round. |
| Shift+↑ / Shift+↓ | Move the camera's focus up or down her body (to her face, say). |
| `+` / `-` | Zoom in or out (0.5× to 8×). |
| `0` | Reset the camera. |

She keeps her size from every angle. In the app she stands 45% of the screen tall at zoom 1, and the window grows and shrinks to fit what her clips can reach (a jump, outstretched arms, a dance that wanders) from the current angle and zoom, up to 0.8 of her height (and half the screen) wide and 85% of the screen tall; past that a wandering clip is cropped. Its bottom-right corner stays put. Clicks go through the see-through window to whatever is behind it, except on her (Windows and macOS; Linux can't pass the mouse through, so there the whole window takes clicks); drag her by her body to move it. The camera is saved in the window's storage and comes back next time.

The window is sized by the page it shows, which may be a server named on the command line, so the ask is clamped: the window never grows past the work area of the display it is on, never leaves that area, and a page from another origin cannot size it at all.

### Files and folders

Models and animations are looked up in folders, in this order; the first file with a given name wins, so your own files override the shipped ones. `zrb-avatar dirs` shows the list.

| Kind | Folders |
| --- | --- |
| models | `$ZRB_AVATAR_MODELS`, `~/.zrb/avatar/models` |
| animations | `$ZRB_AVATAR_ANIMS`, `~/.zrb/avatar/anim`, the package's `anim/` |

Only `models/` is yours from the start: no model ships with the package, so a fresh install has nothing to show until you put a `.vrm` there. The animations are the other way round — **the first run copies the shipped clips into `~/.zrb/avatar/anim`**, so the folder you can edit is the folder that plays. From then on a copy follows the package only while it is still byte for byte what was copied: improve a clip in a new release and your untouched copy is replaced, while one you edited is left alone and reported as `kept … (edited)`. `.template.json` beside them records what was copied and from which version; a manifest that goes missing reads as "every clip is yours", so nothing is overwritten on the strength of a file that cannot be read. `zrb-avatar anim` shows the folder and which clips are your own, and `zrb-avatar anim reset` puts the shipped clips back — including ones you deleted — while keeping what you edited.

| Path | Convention |
| --- | --- |
| `<models folder>/<name>.vrm` | A model, opened by `<name>`. Only files directly in the folder are served. |
| `<models folder>/<name>.json` | Optional notes about that model. `{"covers": ["chest", "hips", "thighs"]}` says what her outfit covers: a close look there is then not a shy one, and the camera's geometry alone — which knows nothing about clothes — no longer blushes at a hoodie. Without the file nothing is covered, as before. |
| `<anim folder>/<pool>/<name>.vrma` | An animation; the pool (a subfolder, which may nest: `actions/greetings/hi.vrma`) says when it plays. See [Animations](#animations). |

### In the code

Constants with no setting of their own; edit the file to change them.

| File | Constant | Value | What it does |
| --- | --- | --- | --- |
| `js/main.js` | `FPS` | 30 (`?fps=60` on the page) | Frames drawn per second; drawing is most of the CPU and GPU she uses. |
| `js/framing.js` | default size | 60% of the screen | How big the app window starts, before zooming. |
| `js/main.js` | `LOOK` | cyan, violet, green, red, bright cyan | The glow colour per state: idle, thinking, tool, attention, speaking. |
| `js/expression.js` | blinking | every 2–6 s, 0.12 s long | How often and how long she blinks. |
| `js/expression.js` | smile | every 25–50 s for 3 s at 0.5; 2 s after a task | The soft `relaxed` smile. |
| `js/expression.js` | thinking tilt / speaking nod | 0.12 / 0.04 rad | How far the head tilts while thinking and nods with the voice. |
| `js/view.js` | `CLOSE` / `SHY` | under 0.6 of her height across / chest, hips, thighs | What counts as a close-up, and of what she is shy. |
| `js/blush.js` | `OUT`, `SIZE`, `LIFT`, `DOWN` | 0.3, 0.32, 0.01, 0.45 | Where the blush sits, in eye widths; nudge them for a model whose blush sits off. |
| `js/motion.js` | crossfade | 0.6 s (actions 0.3 s) | Blend time between animations. |
| `client.py` | `MOUTH_STEP` | 0.04 s | Audio per mouth value; the values are windowed across chunk boundaries, so one value is one step whatever the chunking. |
| `zrb_init.py` | `AWAKE_HOURS` | 8:00–21:59 | When she may speak first. |
| `zrb_init.py` | idle check / away time | every 60 s / 5 min | How often `idle` checks; how long you must be away before `camera` greets you back. |
| `zrb_init.py` | `NOTHING_TO_SAY` | `…` | The reply she gives, unspoken, when nothing is worth saying. |
| `app/main.js` | window | 440×640, bottom right | Where the window starts; the page then sizes it to her. |
| `app/main.js` | on top | `screen-saver` level | Stays above full-screen windows too. |
| `js/expression.js` | emotion | 0.6 for 6 s | How strong and how long she shows an emotion. |
| `js/expression.js` | gaze | jumps every 0.6–3 s; glances away 18% (thinking 55%, speaking 8%) | How her eyes move around you. |
| `js/motion.js` | loop speed | 0.9–1.1× (dances 1×) | Each loop plays a little faster or slower, so repeats never match exactly. |

## Changing the model

Put any VRM file (VRM 0.x or 1.0) in `~/.zrb/avatar/models/` (or a folder in `ZRB_AVATAR_MODELS`) as `<name>.vrm` and open it by name: `zrb-avatar <name>`. Without a name, the window shows the first model alphabetically. To switch while she runs, `zrb-avatar model <name>` (or ask zrb: its `AvatarModel` tool) swaps the model in every open window, in a second or two and without reloading it; the server remembers it for windows opened later and writes it to `<home>/model`, so a restart no longer puts back the first name in the folder. `zrb-avatar <name>` shows that model in the window it opens for that launch, and leaves the remembered choice alone — name it with `zrb-avatar model <name>` when you want it to stick. `GET /model` says which one is chosen and which ones the open windows show. No model ships with the package. Model files are kept out of git (`*.vrm` is ignored). Nothing in the code depends on the model: the camera fits it, and the pose and idle come from the animations, which three-vrm retargets onto any VRM, as VRoid Hub's playground does.

Where to get models:

- **[VRoid Studio](https://vroid.com/en/studio)**: free, for Windows and macOS. Make your own and export it as VRM. You own it, and it has every expression.
- **[VRoid Hub](https://hub.vroid.com/en)**: thousands of models. Only the ones marked downloadable can be downloaded, and each has its own conditions (avatar use, commercial use, redistribution, changes). Read them before you change or share the model.
- **[BOOTH](https://booth.pm/en/search/VRM)**: search for `VRM` and filter for free items. The terms are on each item's page, often only in Japanese.
- **[Open Source Avatars](https://github.com/toxsam/open-source-avatars)**: free VRM avatars with direct download links. The license is set per collection (CC0, CC-BY, …), so check it.
- **[OpenGameArt: VRoid Studio CC0 models](https://opengameart.org/content/vroid-studio-cc0-models)**: public domain sample models from early VRoid Studio.

Fan models of existing characters (Cortana, Kusanagi, and others) are fine on your own desktop, but don't commit or share them.

## Animations

Every `<pool>/<name>.vrma` (VRM Animation) in the animation folders loads when the page starts. The pool, the subfolder, says when it plays:

A file whose `VRMC_vrm_animation` leaves `specVersion` out is read as `"1.0"`, the version most exporters mean, so clips from an older exporter still load instead of vanishing with a line in the console; one that says `"0"` is VRM 0.x, in centimetres, and is skipped with a warning.

| Pool | Plays |
| --- | --- |
| `idle/` | Loops while idle, and for every state with no pool of its own. |
| `thinking/`, `tool/`, `attention/`, `speaking/` | Loops in that state. |
| `dance/` | Loops instead of `idle/` while music plays. |
| `actions/` | Once, on `{"type": "action", "value": "<name>"}`, then back to looping. zrb plays them through its `AvatarAction` tool; `Greeting` plays when a chat starts. |

Any other pool loads too, and plays once something asks for it (`zrb-avatar action <pool>/<name>`, or zrb's `AvatarAction`).

Pools nest: a subfolder's clips belong to the pool above it, as one group in the random pick. `idle/calm/*.vrma` loops with the idles, as often as any one other idle; `actions/greetings/hi.vrma` is the action `greetings/hi`. Overrides go by the whole path: your `actions/greetings/hi.vrma` replaces only that one. A clip is always played by its full path (`thinking/Thinking`, `actions/greetings/hi`); an action's short name (`greetings/hi`) works too, unless it is some other clip's full path (an `actions/thinking/Thinking.vrma` is `actions/thinking/Thinking`, as `thinking/Thinking` is the loop).

To add your own, drop the file in — the folder is filled on the first run, so it is already there — e.g. `cp Wave.vrma ~/.zrb/avatar/anim/actions/`, and restart with `zrb-avatar` so the window loads it; zrb's tools see it at once. To take a clip out, delete it: that is recorded, so it stops playing and is not restored by a package upgrade — `zrb-avatar anim reset` is the way back. The folders cascade per file: yours replaces a shipped clip of the same `<pool>/<name>`, adds any new one, and every shipped clip you don't have still plays. A description, which `AvatarListActions` shows the agent, is read from the file's animation `extras.description` (optional). Replacing a shipped clip outright is the same thing: edit `~/.zrb/avatar/anim/idle/Relax.vrma` and it stays yours. Try an action with `zrb-avatar action Wave`.

Within a pool, clips play in random order with crossfades, picked by group: the first part of the name before a `-` (`stretch-side` belongs to `stretch`), so a family of clips comes up as often as any single other one. A pool's own clip (`idle/Idle`) is where she rests: she comes back to it after any other, and leaves it only now and then (30%). Every clip also plays mirrored, left and right swapped, as `<name>-mirror` in its group, and an action plays mirrored half the time: a wave with either hand. On top of whatever plays: the head tilts while thinking, nods while speaking, the eyes follow you, and the face shows zrb's emotion.

The shipped clips, by pool:

| Pool | Clips |
| --- | --- |
| `idle/` | `Idle`, `Idle-Proud`, `Akimbo`, `HandsInPockets`, `IdleShift` |
| `thinking/` | `Thinking` |
| `attention/` | `Attention` |
| `speaking/` | `Talking` |
| `dance/` | `Dance` |
| `actions/` | `Death`, `HitChest`, `HitHead`, `Interact`, `Jump`, `JumpLand`, `JumpStart`, `PickUpTable`, `PunchCross`, `PunchEnter`, `PunchJab`, `Roll` |

Every pool except `actions` **loops** for as long as its state lasts, and that is what decides
whether a clip belongs in one: a crouch, a greeting or a roll is an action however good it
looks once. `thinking/Thinking` is `idle/Idle` byte for byte — stillness reads better than mime
in the state visited on every tool call, and a thinking gesture that repeats becomes a tic.
[PROVENANCE.md](PROVENANCE.md) records where each clip came from, and what was left out (a seated
clip, until the window has a chair).

The page looks a clip up by name, so `zrb-avatar action Greeting` plays `actions/Greeting.vrma`; `zrb-avatar actions` lists everything with its length.

Where to get more VRMA:

- **VRoid's official 7-motion pack** (show full body, greeting, peace sign, shoot, spin, model pose, squat), free on [BOOTH](https://vroid.com/en/news/6HozzBIV0KkcKf9dc1fZGW). Free to use, commercial use with the credit "Animation credits to pixiv Inc.'s VRoid Project", but its terms forbid redistributing the files, so keep them out of git. E.g. `cp VRMA_02.vrma ~/.zrb/avatar/anim/actions/Greeting.vrma`.
- **Dances**: motion capture through bvh2vrma, e.g. the [AIST++](https://google.github.io/aistplusplus_dataset/) dance dataset (CC BY 4.0, credit needed) or the [CMU](http://mocap.cs.cmu.edu/) library (free). Mixamo's dances can be converted too, but its terms forbid sharing the files, so keep them in your own folder.
- **Mixamo** (FBX, "Without Skin"): `cd tools && npm install && node fbx2vrma.mjs <in.fbx> <out.vrma> ["what it looks like"]` converts one, retargeted the way three-vrm's [humanoidAnimation example](https://pixiv.github.io/three-vrm/packages/three-vrm/examples/humanoidAnimation/index.html) does, every track on one 30 fps time line, and a clip that keeps facing to one side turned to face front. Mixamo's terms forbid sharing the files, so keep the results in your own folder.
- **Your own**: `tools/` converts glTF, GLB, VRM (`gltf2vrma.py`), Mixamo FBX (`fbx2vrma.mjs`) and BVH motion capture (`bvh2vrma.mjs`); `tools/README.md` says what each needs and what a clip has to get right, and `node tools/vrma-info.mjs <file>` says what a loader makes of one. VRoid Studio also exports poses and animations as VRMA.

## The desktop app

Each [release](https://github.com/state-alchemists/zrb-avatar/releases) has a zip per platform:

- **Windows** (`zrb-avatar-win32-x64.zip`): unzip into `%LOCALAPPDATA%\zrb-avatar\`, so `zrb-avatar` finds `zrb-avatar.exe` there from WSL.
- **macOS** (`zrb-avatar-darwin-arm64.zip` for Apple silicon, `-x64` for Intel): move `zrb-avatar.app` to `/Applications`. The app isn't signed, so clear macOS's block once with `xattr -dr com.apple.quarantine /Applications/zrb-avatar.app`.
- **Linux** (`zrb-avatar-linux-x64.zip`): unzip anywhere and set `ZRB_AVATAR_APP` to its `zrb-avatar` program. On Wayland (GNOME), a window can't keep itself on top, so pin it by hand.

To build it yourself:

```sh
cd app
npm install
npm start                                   # run from source
npm run pack:win                            # or pack:mac / pack:linux -> dist/
cp -r dist/zrb-avatar-win32-x64 /mnt/c/Users/$USER/AppData/Local/zrb-avatar
```

If GitHub downloads are slow, prefix the build with `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`, then check the zip in `~/.cache/electron` against GitHub's `SHASUMS256.txt`.

## Test

```sh
python3 test_face.py                        # with a Python that has zrb installed
```

It checks the search paths, the event relay, the remembered model choice, the music state and watcher (`media-control` and AppleScript), and that every shipped `.vrma` is real glTF with the humanoid bones and valid channel references.

CI (`.github/workflows/test.yml`) runs it with a pyflakes lint on every push to `main` and every pull request: Python 3.11–3.14 on Linux, and 3.13 on Windows and macOS. On pull requests from a branch of this repository (not a fork), `code-review.yml` also has zrb review the diff and post the report as a comment (`zrb submit-comment`, defined in the root `zrb_init.py`); it needs the `OPENAI_API_KEY` secret (and optionally `ZRB_LLM_MODEL`), and skips itself without it.

## Releasing

Bump the version in `pyproject.toml` and `app/package.json`, then push a tag: `git tag 0.1.1 && git push origin 0.1.1`. `.github/workflows/release.yml` opens the GitHub release, publishes the package to PyPI and attaches the app zips to it; the tag must equal `pyproject.toml`'s version or the run stops before it publishes anything. PyPI publishing is trusted publishing, so first add this repository, the workflow `release.yml` and the environment `pypi` as a trusted publisher of `zrb-avatar` on pypi.org. Publishing by hand first (`python -m build && twine upload dist/*`) is also fine: the workflow notices the version is already on PyPI, skips that step and still releases the desktop app.

## Credits

The models used during development, not included: *ram* and *rem made in mexico* (Re:Zero fan models) by pon3mx on [VRoid Hub](https://hub.vroid.com/en); search for them there. Ram: use, changes, commercial use and redistribution allowed, no credit needed. Rem: the same but no commercial use. Both have mouth, blink and emotion expressions. They are fan models of the Re:Zero characters, so keep them on your own machine.
