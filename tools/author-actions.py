"""Author gesture clips as keyframes on a pose a real CC0 mocap clip already holds.

This is the gesture companion to `author-vrma.py`. Idles are sine waves; gestures are not — a wave
is "arm up, three beats, arm down", and that is a sequence, not an oscillation. So a clip here is
a list of beats:

    (0.16, {**arm("left", 5, 90, 50, -30, 100), "head": {"y": 6}}),

Between two beats every channel is interpolated with smoothstep, so a motion starts and stops with
weight behind it instead of running at constant speed. Channels are per bone per axis, so a head
can keep turning while an arm has already stopped — the overlap that makes motion read as a body
rather than as a rig.

Rules, as in `author-vrma.py`:

  - every clip starts and ends on the base pose (naming a channel at 0.0 or 1.0 overrides that),
    so a one-shot action leaves me where it found me;
  - one 30 fps time line, min/max on the sampler input;
  - the base clip's scene root, node list, rest transforms and humanoid map are copied verbatim.

Axis vocabulary, measured with `probe-axes.mjs` on this rig and not guessed (what a 12-degree turn
of that axis does to the avatar):

    bone                 x                        y                 z
    hips (rotation)      pitch, torso lean        yaw               roll
    hips (translation)   sideways (world x)       fore/aft (z)      up/down (y)
    spine                pitch                    twist             side bend
    neck, head           pitch (nod)              yaw (turn)        roll (tilt)
    shoulder             raises the whole arm    forward/back      across the body
    upperArm             swing forward/back       twist             abduct (raise sideways)
    lowerArm             bend the elbow           twist             bend across the body

Arm poses are solved, not guessed: `solve-pose.py` searches shoulder lift, abduction, swing, twist
and elbow bend, measures where the hand lands in the pelvis' own frame, and keeps the pose closest
to a target hand position with the elbow kept below the hand. The numbers quoted in each clip's
docstring are what it landed on.

Usage:
    python3 author-actions.py <base-vrma-dir> <out-dir> [clip ...]
"""

import os
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
core = SourceFileLoader("author", os.path.join(HERE, "author-vrma.py")).load_module()

FPS = core.FPS
AXIS_VECTOR = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
ORDER = ("x", "y", "z")


STANDING_BASE = core.STANDING_BASE


def base_with_standing_pose(base_rot):
    return core.standing_pose(base_rot)


def smoothstep(u):
    return u * u * (3 - 2 * u)


def channel_at(beats, bone, axis, t):
    """The degrees this bone-axis should hold at t, easing between the beats that name it."""
    points = [(at, spec.get(bone, {}).get(axis)) for at, spec in beats]
    points = [(at, deg) for at, deg in points if deg is not None]
    if not points:
        return 0.0
    # A channel that begins late starts from the base pose; one that stops being named eases back
    # to the base pose by the end of the clip. Naming it again is how a hold is spelled.
    if t <= points[0][0]:
        return points[0][1] * smoothstep(t / points[0][0]) if points[0][0] > 0 else points[0][1]
    if t >= points[-1][0]:
        left = 1.0 - points[-1][0]
        return points[-1][1] * (1.0 - smoothstep((t - points[-1][0]) / left)) if left > 0 else points[-1][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            u = smoothstep((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
            return d0 + (d1 - d0) * u
    return points[-1][1]


def arm(side, shoulder, abduction, swing, twist, elbow, shoulder_across=0.0):
    """The numbers that place one arm, in the bone-axis spelling.

    `shoulder_across` is the sixth, and it exists because a hand has to reach the **midline**: the
    shush holds its fist in front of the mouth, centred, and no combination of abduction and swing
    gets the wrist there without swinging the shoulder across the chest first.
    """
    return {
        f"{side}Shoulder": {"x": shoulder, "z": shoulder_across},
        f"{side}UpperArm": {"z": abduction, "x": swing, "y": twist},
        f"{side}LowerArm": {"x": elbow},
    }


FINGERS = ("Index", "Middle", "Ring", "Little")

# How far each finger's knuckle straightens. One number does not serve all four: they start from
# different resting curls (the index at 177 degrees of bend in the rest fist, the little at 189), so
# a uniform -85 leaves the little finger at 16 degrees however far it is pushed — -90 through -120
# were swept and 16 is its floor, the best this rig will do. Measured per finger with `probe-curl.mjs`.
EXTEND = {"Index": -85, "Middle": -85, "Ring": -90, "Little": -95}

# The last joint, which nothing can measure: rotating it moves no other bone, so every joint probe is
# blind to it and only a render (or the mesh) shows the result. At 0 the fingertips keep the rig's
# resting 13 degrees of curl and read as a claw in profile — what Go saw and called "not fully open";
# at -50 the profile is a flat hand. Checked against -30, -45, -55 and -60 in `render.sh ... profile`.
DISTAL = -50

# How far each finger fans out from its neighbour, on the proximal's z axis. The index and the
# little finger do the spreading; the middle pair barely moves, which is what a human hand does.
FAN = {"Index": 16, "Middle": 5.6, "Ring": -5.6, "Little": -16}


def hand_open(side, amount=1.0):
    """An open hand: fingers straight *and* fanned, the thumb held out from the palm.

    Five measurements. Four are in `probe-curl.mjs` (the bend at each joint, in degrees, 0 being dead
    straight) and one is `probe-fingers.mjs` (the index-to-little gap). Between them they caught
    every wrong answer along the way:

    - **Straightening needs -85 / -80**, not the -32 / -22 a distance measurement suggested. Swept:
      -40 on the knuckle leaves 48 degrees of bend, -80 leaves 19, -85 leaves 4; the middle joint
      falls from 79 to 2 at -80. The earlier amounts took the fingertips from 7.0 to 8.7 cm and the
      hand still rendered as a fist (108 degrees of bend, down from 164) — the distance grew mostly
      by swinging the fingers sideways out of the palm.
    - **The knuckle is per finger.** `EXTEND`: the little finger needs -95 and even then holds 16
      degrees, the ring -90, the index and middle -85.
    - **The last joint is invisible to every probe** and has to be rendered. `DISTAL` -50 is what
      turns a claw profile into a flat hand.
    - **Fanning is the proximal's z axis**, and it is not optional: at every extension amount up to
      four times the first one, the index-to-little gap stayed at 4.5 cm. +16 on the index and -16
      on the little, at the same time, takes it to 7.9 cm.
    - **z and y mirror between the hands, x does not.** The left hand's fan at +16 on the index
      pinches the right hand to 2.0 cm; the thumb's metacarpal needs +60 on the left and -60 on the
      right. This was measured on the right hand separately, not guessed from the left.
    - **The thumb is a metacarpal, a proximal and a tip**: metacarpal y 60 lifts it off the palm
      (47 -> 25 degrees of bend there), proximal x -40 and distal y -40 straighten it (46 -> 7 at
      the tip). Curling the proximal alone, which the earlier version did, leaves the thumb folded
      across the palm.

    Against the rest fist: knuckle 3 (85) on the index and 16 (18) on the little, middle joint 2-3
    (79), thumb 44 of curl (117), index-to-little 7.9 cm (4.5), and the fingertips flat instead of
    hooked.
    """
    mirror = 1 if side == "left" else -1
    spec = {}
    for finger in FINGERS:
        spec[f"{side}{finger}Proximal"] = {"x": EXTEND[finger] * amount,
                                           "z": FAN[finger] * amount * mirror}
        spec[f"{side}{finger}Intermediate"] = {"x": -80 * amount}
        spec[f"{side}{finger}Distal"] = {"x": DISTAL * amount}
    spec[f"{side}ThumbMetacarpal"] = {"y": 60 * amount * mirror}
    spec[f"{side}ThumbProximal"] = {"x": -40 * amount}
    spec[f"{side}ThumbDistal"] = {"y": -40 * amount * mirror}
    return spec


def shush_hand(side, amount=1.0):
    """One finger up, the rest a fist: the shape of a secret.

    Only the index is opened, so the other three keep the base pose's curl; that is the gesture, not
    an unfinished open hand.

    **The thumb stands off the fist.** Go's reference drawing for the shush shows the thumb clearly,
    sticking out beside the curled fingers — a thumb left folded in the base pose disappears behind
    the hand from the front and the fist reads as a stump. `hand_open`'s thumb values (metacarpal 60,
    proximal -40, distal -40) are what lift it clear.
    """
    mirror = 1 if side == "left" else -1
    return {
        f"{side}IndexProximal": {"x": EXTEND["Index"] * amount, "z": FAN["Index"] * amount * mirror},
        f"{side}IndexIntermediate": {"x": -80 * amount},
        f"{side}IndexDistal": {"x": DISTAL * amount},
        f"{side}ThumbMetacarpal": {"y": 60 * amount * mirror},
        f"{side}ThumbProximal": {"x": -40 * amount},
        f"{side}ThumbDistal": {"y": -40 * amount * mirror},
    }


def hand_thumbs_up(side, amount=1.0):
    """A fist with the thumb standing up out of it.

    Solved, not guessed: swept over the thumb's metacarpal and proximal and the wrist, the thumb's
    tip-to-knuckle direction reaches (-0.12, 0.99, -0.07) — straight up — at metacarpal 20, proximal
    -20, with the wrist at y 60. The palm ends up 0.94 forward, which is what makes the thumb read
    against my body rather than edge-on. `hand_open`'s thumb values put it out sideways instead, the
    way an open hand wants it, and left the thumb 0.5 of the way across my chest.

    The fingers are deliberately left at the base pose: a thumbs-up is a fist.
    """
    mirror = 1 if side == "left" else -1
    return {
        f"{side}ThumbMetacarpal": {"y": 20 * amount * mirror},
        f"{side}ThumbProximal": {"x": -20 * amount},
        f"{side}ThumbDistal": {"y": 40 * amount * mirror},
    }


def build_tracks(base_rot, beats, frames):
    standing = base_with_standing_pose(base_rot)
    tracks = {}
    for bone, keys in base_rot.items():
        if not bone:
            continue
        base_q = standing[bone]
        out = []
        for i in range(frames):
            t = i / (frames - 1)
            q = base_q
            for axis in ORDER:
                deg = channel_at(beats, bone, axis, t)
                if abs(deg) > 1e-9:
                    q = core.q_mul(q, core.q_axis(AXIS_VECTOR[axis], deg))
            out.append(core.q_norm(q))
        tracks[bone] = out
    return tracks


# --- the clips -------------------------------------------------------------------------------

# A weight shift that keeps the feet planted: measured on the avatar, a hip roll of 3.5 degrees
# slides each foot 5 cm, and 6 degrees of leg abduction cancels it. Every gesture that moves the
# pelvis reuses that pair, scaled together, so the feet stay where they are.
def pelvis_shift(amount):
    """Hip roll in degrees and the leg abduction that holds the feet under it."""
    return 3.5 * amount, 6.0 * amount


def wave_arm(abduction, swing, twist, elbow, forearm_twist=-58):
    """One wave arm pose. The forearm's twist here rolls the whole hand, but it does not roll it
    far enough to show the palm on its own — see `wave_beats()` for what does.
    """
    pose = arm("left", 0, abduction, swing, twist, elbow)
    pose["leftLowerArm"] = {**pose["leftLowerArm"], "y": forearm_twist}
    return pose


def wave_beats():
    """A wave that moves: the elbow travels with the beat, the hand is open, the palm faces you.

    **The hand's face is the wrist's roll, and the axes had to be measured.** Go: *"when you wave,
    your palm should face to someone you wave to, right?"* — and then, when it still looked wrong:
    *"I think the wrist need to rotate a bit."* Both right. Read off the joints rather than the bone
    frames (a bone's local axes are a rig convention; joints are anatomy):

        fingers = middle-finger last joint - wrist        where the hand points
        across  = index knuckle - little knuckle          across the palm, little -> index
        palm    = fingers x across                        the palm's side, verified against the
                                                         rest fist: the fingertips curl onto it

    Measured over the reference `Greeting` clip Go accepts: its fingers are (-0.07, 0.92, -0.39) and
    its palm is (0.98, 0.13, 0.14) as (forward, up, outward) — fingers up, palm square to the viewer.
    Ours was (-0.95 outward) on the palm: the hand showed its edge, tilted up. That is the broken
    wrist, and no amount of the forearm's twist fixed it.

    Each axis was then swept on its own, holding everything else, and identified by what moved:

        forearm x   elbow angle 104 -> 59 while the hand stays put     elbow bend
        forearm y   elbow angle steady at 74, palm turns 172 degrees   forearm twist
        hand x      fingers swing up/down, palm barely moves           wrist flexion
        hand y      palm turns, fingers barely move                    wrist roll
        hand z      fingers swing sideways                             wrist deviation

    Solved wrist (`_joints.mjs` grid, scored against the reference frame) — palm 0.98 forward and
    fingers 0.92 up at every one of the three beats, and the hold an exact match at cost 0.03:

        beat    wrist (x, y, z)
        hold    30, 120, 0
        outer   30,  90, 0
        inner   20, 120, 15

    The 120 degrees of roll is the price of showing the palm on this rig: a wrist cannot roll, so in
    a real arm that is pronation, but here it is spelt on the hand bone and it is what the reference
    clip's frame requires. The forearm's -58 twist stays — it turns the fingers outward, clear of my
    face — it just does not do the roll.

    The open hand is named on **every** beat, not just the hold. `channel_at` eases a channel that
    stops being named back toward the base pose, so naming it once let the fingers curl shut during
    the beats — the wave was open at rest and a fist while actually waving, which is exactly the
    frame anyone looks at.
    """
    roll, leg = pelvis_shift(0.25)
    stance = {"hips": {"z": roll}, "spine": {"z": -roll * 0.4},
              "leftUpperLeg": {"z": leg}, "rightUpperLeg": {"z": leg}}
    hands = hand_open("left")
    hold = {**wave_arm(45, 50, -8, 75), **hands, "leftHand": {"x": 30, "y": 120, "z": 0}}
    wide = {**wave_arm(45, 50, -45, 78), **hands, "leftHand": {"x": 30, "y": 90, "z": 0}}
    inner = {**wave_arm(45, 50, -8, 72), **hands, "leftHand": {"x": 20, "y": 120, "z": 15}}
    return [
        (0.00, {}),
        (0.16, {**hold, **stance, "head": {"y": 6, "z": 3}}),
        (0.30, wide),
        (0.44, inner),
        (0.58, wide),
        (0.72, hold),
        (0.88, {**wave_arm(10, 18, -6, 30, forearm_twist=-20), **hand_open("left", 0.4),
                "leftHand": {"x": 12, "y": 40},
                "head": {"y": 2}, "hips": {"z": 0}, "spine": {"z": 0},
                "leftUpperLeg": {"z": 0}, "rightUpperLeg": {"z": 0}}),
        (1.00, {}),
    ]


def greeting_beats():
    """A hello that actually waves: the wave's own three solved poses, two beats instead of three.

    **What the first version got wrong, seen on screen and not in any number.** It held one
    open palm at shoulder height and called it a greeting. Rendered across its length it read as
    *"halt"* — a still hand held up at you is the stop gesture, whatever the intent behind it —
    and Go said so: *"That Greetings is also weird."* A greeting has to move. The beats are the
    whole difference between a wave and a barrier.

    So the three poses are `wave_beats()`'s verified ones, unchanged (palm 0.98 forward, fingers
    0.92 up at every beat); what is shorter is the clip — two beats rather than three, a nod over
    the second, and a quicker fall — because this plays by itself when a conversation opens.
    """
    roll, leg = pelvis_shift(0.15)
    stance = {"hips": {"z": roll}, "spine": {"z": -roll * 0.4}, "chest": {"x": 2},
              "leftUpperLeg": {"z": leg}, "rightUpperLeg": {"z": leg}}
    hands = hand_open("left")
    hold = {**wave_arm(45, 50, -8, 75), **hands, "leftHand": {"x": 30, "y": 120, "z": 0},
            **stance, "neck": {"x": 3}, "head": {"y": 5}}
    outer = {**wave_arm(45, 50, -45, 78), **hands, "leftHand": {"x": 30, "y": 90, "z": 0},
             **stance, "neck": {"x": 3}, "head": {"y": 5}}
    nod = {**hold, "neck": {"x": 8}, "head": {"x": 5, "y": 4}}
    falling = {**wave_arm(12, 16, -6, 34, forearm_twist=-20), **hand_open("left", 0.5),
               "leftHand": {"x": 14, "y": 46}, "neck": {"x": 4}, "head": {"y": 2},
               "hips": {"z": 0}, "spine": {"z": 0},
               "leftUpperLeg": {"z": 0}, "rightUpperLeg": {"z": 0}}
    return [
        (0.00, {}),
        (0.22, hold),
        (0.34, outer),
        (0.48, nod),
        (0.62, outer),
        (0.74, hold),
        (0.88, falling),
        (1.00, {}),
    ]


def attention_beats():
    """A hand offered out to the side, palm to you, for *"look here"*.

    Go: *"when you tell secret, thumbs up, and asking to 'look here' the finger should be corrected
    also"* — and the render showed why the fingers were the smaller half of it: with an open hand
    the old pose (hand 14.7 cm out, 47 up, in front of my face) covered my own face instead of
    presenting anything. Re-solved onto the target 20 cm out, 18 forward, 40 up with the elbow out
    and below: abduction 20, swing 40, twist -20, elbow 90 puts the hand at (19.9, 21.1, 41.8) with
    the elbow at (25.4, 6.2, 24.9).

    The wrist (0, 60, 30) is solved so the offered palm reads 0.97 forward with the fingers 0.92 up.
    Named on every beat, like every other hand here.
    """
    roll, leg = pelvis_shift(0.3)
    stance = {"hips": {"z": roll}, "spine": {"z": -roll * 0.6},
              "leftUpperLeg": {"z": leg}, "rightUpperLeg": {"z": leg}}
    hands = hand_open("left")
    wrist = {"x": 0, "y": 60, "z": 30}
    hold = {**arm("left", 0, 20, 40, -20, 90), **hands, "leftHand": wrist, **stance,
            "neck": {"x": 3}, "head": {"x": 2, "y": 4}}
    return [
        (0.00, {}),
        (0.22, hold),
        # identical values, so the offered hand is held rather than slid through the hold
        (0.50, hold),
        (1.00, {}),
    ]


def nod_beats():
    """A nod with the chest and shoulders in it: the head alone reads like a puppet."""
    return [
        (0.00, {}),
        (0.12, {"neck": {"x": 6}, "chest": {"x": 2},
                "leftShoulder": {"x": 2}, "rightShoulder": {"x": 2}}),
        (0.26, {"neck": {"x": 15}, "head": {"x": 8}, "chest": {"x": 4}}),
        (0.40, {"neck": {"x": 2}, "head": {"x": -3}, "chest": {"x": 0}}),
        (0.54, {"neck": {"x": 12}, "head": {"x": 6}, "chest": {"x": 3}}),
        (0.74, {"neck": {"x": 2}, "head": {"x": 0}, "chest": {"x": 1},
                "leftShoulder": {"x": 0}, "rightShoulder": {"x": 0}}),
        (1.00, {}),
    ]


def secret_beats(hand, wrist=None, reach=None, side="right", glance=-6):
    """A secret: a hand up beside my mouth, and a lean toward you.

    Go, twice: *"the hand and the body should not be that close, the hand should open a bit"*, and
    then a reference of a flat open hand held beside the mouth, and another of a one-finger shush.
    So the open hand's arm is solved to hold the wrist **20 cm from my head** — measured, `_frame.mjs`
    reports the wrist-to-head distance — where the old pose sat at 15.5 cm and visibly touched my
    cheek. The arm: abduction 10, swing 50, twist 20, elbow 80, landing at (-13.0, 20.5, 44.1) with
    the elbow at (-17.5, 9.8, 24.0), 20 cm below the hand.

    The hand is a parameter, because the two references disagree about its shape and only Go can
    settle which reads better: `hand_open` for the flat hand, `shush_hand` for one finger up.

    **The two variants face opposite ways, and each brings its own arm and wrist.** Go settled it
    with a shush photograph and a telling-a-secret drawing, after three wrong guesses of mine:

    | | what the viewer sees | measured, in the leaned hold |
    |---|---|---|
    | shush | the **edge**: neither palm nor back to you, the thumb edge turned toward me | palmI (the normal is the **back** on the right hand) (0.11, 0.14, **0.98**) sideways; index (-0.09, **0.99**, -0.13) up, tip **2.8 cm** from the mouth; thumb tip 5.9 cm *behind* the hand; wrist **+1.1 cm** from my midline, so the fist is centred in front of the mouth |
    | open hand | the **edge** of the hand, palm turned toward my own face, thumb behind | palmI (0.05, 0.20, **-0.98**) — lateral, back to my right — fingers (0.00, **0.97**, 0.20), thumb tip 8.0 cm behind |

    Five lessons, each one a rebuild:

    - **Solve the wrist in the posed body, not on a straight stance.** (0, 90, 15) solved on the
      standing base measured the back of the hand square to the viewer; rebuilt into the real hold —
      spine 7, chest 4, neck 6, head 4 plus a hips roll — it landed 40 degrees off.
    - **"The side of the hand" is not a direction.** Palm, back and edge are three targets, and the
      prose picked none of them; I built all three in turn. What decides it is the **thumb**: the
      inner edge (with the thumb) faces me, the outer edge faces the viewer, and both the palm and
      the back stay hidden.
    - **A fist belongs in front of the mouth, not beside it.** The hand has to reach the midline,
      and nothing in abduction or swing gets it there — the shoulder has to swing across, which is
      why `arm()` grew `shoulder_across`.
    - **A forearm that leans forward breaks the wrist.** The first centred solve put the index up by
      bending the hand 50 degrees off the forearm. Solved with the forearm standing up (0.95 of its
      length vertical), the same targets need only 26: shoulder across 40, abduction 20, swing 40,
      elbow 105.
    - **Measure the fingertip, not the wrist.** `_frame.mjs` prints `indexTip` and `tipToMouth`.
    """
    roll, leg = pelvis_shift(0.2)
    # the stance, the roll and the glance all mirror with the hand that does the gesture
    sign = 1 if side == "right" else -1
    stance = {"hips": {"z": roll * sign}, "spine": {"z": -roll * 0.5 * sign},
              "leftUpperLeg": {"z": leg * sign}, "rightUpperLeg": {"z": leg * sign}}
    # each variant faces its own way and reaches its own distance, so both are passed in
    wrist = wrist or {"x": 0, "y": -90, "z": -15}
    reach = reach or arm("right", 0, 10, 50, 20, 82)
    hold = {**reach, **hand(), f"{side}Hand": wrist, **stance,
            "spine": {"x": 7, "z": -roll * 0.5 * sign}, "chest": {"x": 4},
            "neck": {"x": 6}, "head": {"x": 4, "z": -4 * sign, "y": glance * sign}}
    return [
        (0.00, {}),
        (0.22, hold),
        # identical values: the hand holds still while I lean and glance, instead of sliding
        (0.52, hold),
        (1.00, {}),
    ]


def agree_beats():
    """A thumbs-up with an actual thumb in it: one arrival, a still hold, one departure.

    Swept: abduction 15, swing 40, twist 20, elbow 56 — the arm reaches forward and slightly across,
    hand at (-15.3, 27.8, 31.8) with the elbow out and below. The old note said "without finger bones
    the thumb itself is missing, so the arm and the nod carry it" — wrong: the thumb is three bones
    and always was, it just was never posed. `hand_thumbs_up` plus the wrist (0, 0, -30) puts the
    thumb's own direction at (0.08, 0.99, 0.07): straight up, its tip 9.8 cm out of the fist.

    **Smoothness is measurable, and this clip failed it.** `_speed.mjs` reports the hand's speed
    frame by frame, and the first version read: peak 186 cm/s into the pose over 0.4 s, then a crawl
    of 3-21 cm/s for 0.7 s, then a double hump on the way out (81, down to 9, back up to 55) because
    the arm lowering and the wrist unrolling finished at different times. Three causes, one each:

    - **A hold that drifts.** The settle beats used different values (elbow 50, 62, 50, neck 5, 10,
      3), so the hand kept creeping through what should have been a still hold. Here the hold beats
      carry **identical** values, so the hand is genuinely still.
    - **A return in two acts.** The way out was split across two beats (0.52 -> 0.78 -> 1.00), which
      stalls at the middle one. Now there is a single segment from the hold to rest.
    - **A snap on arrival.** The arm covered the whole distance in 0.18 of the clip. It now has 0.30,
      and the whole beat is longer between the arrival and the hold.

    Measured after: one hump in, still at the hold, one hump out.
    """
    roll, leg = pelvis_shift(0.2)
    stance = {"hips": {"z": roll}, "leftUpperLeg": {"z": leg}, "rightUpperLeg": {"z": leg}}
    thumb = hand_thumbs_up("right")
    wrist = {"x": 0, "y": 0, "z": -30}
    hold = {**arm("right", 0, 15, 40, 20, 56), **thumb, "rightHand": wrist, **stance,
            "chest": {"x": 2}, "neck": {"x": 6}, "head": {"x": 3}}
    return [
        (0.00, {}),
        (0.30, hold),
        # identical values, so the hand stops instead of drifting: a hold is a still frame
        (0.58, hold),
        (1.00, {}),
    ]


CLIPS = {
    "Wave": ("actions", 2.6,
             "A greeting wave: my left arm comes up, the forearm beats three times with the hand "
             "a beat behind it, and the arm comes down as I look at you.", wave_beats),
    "Greeting": ("actions", 2.0,
                 "A small hello: my left hand rises to shoulder height with the palm to you, a nod "
                 "carries through it, and the arm drops before it becomes a performance.", greeting_beats),
    "LookHere": ("attention", 2.8,
                 "Attention: I raise a hand to chest height, palm offered, keeping my eyes on "
                 "you and slightly to the side, and settles.", attention_beats),
    "Nod": ("actions", 1.8,
            "A nod that carries into my chest and shoulders, twice, and settles.", nod_beats),
    "TellingASecret": ("actions", 2.8,
                       "I lean in, bring an open hand up beside my mouth, glance aside and ease back.",
                       lambda: secret_beats(lambda amount=1.0: hand_open("right", amount),
                                            {"x": 30, "y": 0, "z": 0})),
    # "TellingASecretShush" is parked, not shipped: Go, after eight attempts, *"OMG let's not using
    # shush, you have try so many times and still fail"*. The pose that came closest is kept in
    # parked/ with its measurements and the reason — see parked/README.md. Do not re-add it without
    # a reference frame of the hand to match, because prose about "the side of the hand" produced
    # three different poses and none of them was right.
    "ThumbsUp": ("actions", 2.2,
                 "An agreeing gesture carried by the arm and a small nod: the arm comes forward "
                 "and up, the head follows it.", agree_beats),
}


def build(base_dir, out_dir, wanted):
    unknown = [name for name in wanted if name not in CLIPS]
    if unknown:
        raise SystemExit(f"no such clip: {', '.join(unknown)}; try one of {', '.join(CLIPS)}")
    base_path = os.path.join(base_dir, "idle", "Idle.vrma")
    made = []
    for name, (pool, seconds, description, beats_fn) in CLIPS.items():
        if wanted and name not in wanted:
            continue
        doc, base_rot, base_trans = core.tracks_of(base_path)
        frames = int(round(seconds * FPS)) + 1
        tracks = build_tracks(base_rot, beats_fn(), frames)
        hips = [tuple(base_trans[0])] * frames if base_trans else None
        target = os.path.join(out_dir, pool)
        os.makedirs(target, exist_ok=True)
        path = os.path.join(target, f"{name}.vrma")
        core.write_glb(path, doc, tracks, hips, description)
        made.append(path)
    return made


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    for path in build(sys.argv[1], sys.argv[2], sys.argv[3:]):
        print("wrote", path)
