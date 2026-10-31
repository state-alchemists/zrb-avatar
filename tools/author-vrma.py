"""Author .vrma clips for zrb-avatar, python 3 and nothing else.

A clip here is not drawn from scratch. It takes a pose that a real mocap clip already holds —
frame 0 of a shipped CC0 clip, or the peak of a raised-arm gesture — and moves it by a small,
explicit amount: breathing in the spine, an eye-line that drifts, a weight shift between the
feet. That is why it still looks like a body: every joint keeps the rotation it had, and only
the bones named below are touched.

Rules this file enforces, because a violation here is invisible until the clip is looked at:

  - the last frame equals the first, exactly, so a looping pool has no seam to pop on:
    every motion is a whole number of cycles across the clip, or zero at both ends;
  - one time line, 30 fps, for every channel, because a .vrma consumer plays one timeline;
  - min/max on every sampler input, which is where a clip's length is read from;
  - hips translation only, and only as a delta on the base pose: the spec forbids the rest.

Usage:
    python3 author-vrma.py <base-vrma-dir> <out-dir> [clip ...]

`<base-vrma-dir>` is where the shipped clips live (zrb_avatar/anim in the package).
"""

import json
import math
import os
import struct
import sys

FPS = 30
IDENTITY = (0.0, 0.0, 0.0, 1.0)


# --- quaternions (x, y, z, w), as glTF stores them -------------------------------------------


def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def q_axis(axis, degrees):
    x, y, z = axis
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    half = math.radians(degrees) / 2
    s = math.sin(half) / length
    return (x * s, y * s, z * s, math.cos(half))


def q_norm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def q_slerp(a, b, w):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:  # shortest arc
        b, dot = tuple(-c for c in b), -dot
    if dot > 0.9995:
        return q_norm(tuple(x + (y - x) * w for x, y in zip(a, b)))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    s = math.sin(theta)
    return q_norm(
        tuple(
            (math.sin((1 - w) * theta) * x + math.sin(w * theta) * y) / s
            for x, y in zip(a, b)
        )
    )


def q_angle(a, b):
    """Degrees between two rotations, sign-insensitive."""
    dot = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2 * math.acos(max(-1.0, min(1.0, dot))))


# --- GLB in and out -------------------------------------------------------------------------

SIZE = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    raw = open(path, "rb").read()
    at, chunks = 12, {}
    while at < len(raw):
        length, kind = struct.unpack_from("<II", raw, at)
        chunks[kind] = raw[at + 8 : at + 8 + length]
        at += 8 + length
    return json.loads(chunks[0x4E4F534A].decode("utf-8")), chunks.get(0x004E4942, b"")


def read_accessor(doc, bin_, index):
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    code, width = {5126: ("f", 4)}[acc["componentType"]]
    n = SIZE[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or width * n
    return [
        struct.unpack_from("<" + code * n, bin_, start + i * stride)
        for i in range(acc["count"])
    ]


def tracks_of(path):
    """{bone: [quaternion per frame]}, and [hips position per frame] — as the clip plays them."""
    doc, bin_ = read_glb(path)
    anim = doc["animations"][0]
    bones = {
        v["node"]: name
        for name, v in doc["extensions"]["VRMC_vrm_animation"]["humanoid"][
            "humanBones"
        ].items()
    }
    rot, trans = {}, []
    for channel in anim["channels"]:
        sampler = anim["samplers"][channel["sampler"]]
        values = read_accessor(doc, bin_, sampler["output"])
        name = bones.get(channel["target"]["node"], "")
        if channel["target"]["path"] == "rotation":
            rot[name] = values
        elif channel["target"]["path"] == "translation" and name == "hips":
            trans = values
    return doc, rot, trans


def write_glb(path, base_doc, tracks, hips, description):
    """One 30 fps time line, one animation, the humanoid map copied from the base clip."""
    frames = len(next(iter(tracks.values())))
    times = [i / FPS for i in range(frames)]
    nodes = base_doc["nodes"]
    human = base_doc["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]
    index_of = {name: v["node"] for name, v in human.items()}

    views, blobs, accessors, offset = [], [], [], 0

    def put(values, count, kind, minmax=None):
        nonlocal offset
        data = struct.pack("<" + "f" * len(values), *values)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        blobs.append(data)
        offset += len(data)
        accessor = {
            "bufferView": len(views) - 1,
            "componentType": 5126,
            "count": count,
            "type": kind,
        }
        if minmax:
            accessor.update(minmax)
        accessors.append(accessor)
        return len(accessors) - 1

    time_accessor = put(
        times, frames, "SCALAR", {"min": [times[0]], "max": [times[-1]]}
    )
    samplers, channels = [], []
    for bone, keys in sorted(tracks.items()):
        if bone not in index_of:
            continue
        flat = [c for q in keys for c in q]
        samplers.append(
            {
                "input": time_accessor,
                "output": put(flat, frames, "VEC4"),
                "interpolation": "LINEAR",
            }
        )
        channels.append(
            {"sampler": len(samplers) - 1, "target": {"node": index_of[bone], "path": "rotation"}}
        )
    if hips:
        flat = [c for p in hips for c in p]
        samplers.append(
            {
                "input": time_accessor,
                "output": put(flat, frames, "VEC3"),
                "interpolation": "LINEAR",
            }
        )
        channels.append(
            {"sampler": len(samplers) - 1, "target": {"node": index_of["hips"], "path": "translation"}}
        )

    bin_ = b"".join(blobs)
    doc = {
        "asset": {"version": "2.0", "generator": "zrb-avatar author-vrma.py"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": base_doc["extensions"],
        # The scene root and the node list must come from the base clip untouched. Hard-coding
        # node 0 as the root looks harmless -- the same rotation keys then retarget into a
        # different pose, because the rig's real root (node 54 in the shipped idle) drops out of
        # the scene and every bone's rest transform is read without it. The arms came out 57
        # degrees off, which no inspection of the keys could explain.
        "scene": base_doc.get("scene", 0),
        "scenes": base_doc["scenes"],
        "nodes": nodes,
        "animations": [
            {
                "channels": channels,
                "samplers": samplers,
                "extras": {"description": description},
            }
        ],
        "buffers": [{"byteLength": len(bin_)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    pad = lambda b, f: b + bytes([f]) * ((4 - len(b) % 4) % 4)  # noqa: E731
    text, data = pad(json.dumps(doc).encode("utf-8"), 0x20), pad(bin_, 0)
    head = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(data))
    chunk = lambda kind, body: struct.pack("<II", len(body), kind) + body  # noqa: E731
    open(path, "wb").write(head + chunk(0x4E4F534A, text) + chunk(0x004E4942, data))


# --- the clips -----------------------------------------------------------------------------

TAU = 2 * math.pi

# The shipped `idle/Idle.vrma` starts from a wide straddle: measured on the avatar, its frame 0
# puts the feet 55.5 cm apart with the right foot 32 cm behind the left. Everything authored on it
# inherits that, which is why the first clips read as "standing astride something". This correction
# rotates both legs — and only the legs — into a standing pose 22 cm apart and level, and every
# clip in this file and in author-actions.py is built on it. Numbers from `probe-feet.mjs` plus the
# measured leg sensitivities (1.46 cm of foot per degree of abduction, 1.5 cm per degree of swing),
# iterated against the measurement until the feet landed where a standing body has them.
STANDING_BASE = {
    "leftUpperLeg": {"z": 8.5, "x": 8.0},
    "rightUpperLeg": {"z": -6.5, "x": -14.7},
}

AXIS_VECTOR = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def standing_pose(base_rot):
    """The base pose with the legs brought under her: {bone: quaternion} to build every clip on."""
    out = {}
    for bone, keys in base_rot.items():
        q = keys[0]
        for axis in ("x", "y", "z"):
            deg = STANDING_BASE.get(bone, {}).get(axis, 0.0)
            if abs(deg) > 1e-9:
                q = q_mul(q, q_axis(AXIS_VECTOR[axis], deg))
        out[bone] = q_norm(q)
    return out


def w(t, cycles=1.0, phase=0.0):
    """A whole number of cycles across the clip: zero at both ends, so the loop shuts."""
    return math.sin(TAU * cycles * t + phase)


def bump(t, start, end):
    """A rise and fall that is zero outside [start, end] and at both ends of it."""
    if not start < t < end:
        return 0.0
    x = (t - start) / (end - start)
    return math.sin(math.pi * x) ** 2


def idle_breath(rot, base):
    """Breathing, a slow eye-line, and enough weight shift to be alive."""
    return lambda bone, t: {
        "spine": [((1, 0, 0), 2.0 * w(t)), ((0, 1, 0), 0.6 * w(t, 1, math.pi))],
        "chest": [((1, 0, 0), 1.4 * w(t, 1, 0.4))],
        "neck": [((1, 0, 0), -0.7 * w(t))],
        "head": [((0, 1, 0), 3.5 * w(t, 1, 0.6)), ((1, 0, 0), 1.0 * w(t, 2))],
        "leftUpperArm": [((1, 0, 0), 1.4 * w(t, 1, math.pi))],
        "rightUpperArm": [((1, 0, 0), 1.4 * w(t))],
        "leftLowerArm": [((1, 0, 0), 1.2 * w(t, 1, math.pi))],
        "rightLowerArm": [((1, 0, 0), 1.2 * w(t))],
    }.get(bone, [])


def idle_shift(rot, base):
    """A real transfer of weight from one foot to the other, and a small hand adjustment.

    Every amplitude here was measured, not chosen. The axis probe (`probe-axes.mjs`) says the
    source rig's hips axes are x = pitch, y = yaw, z = roll, and that a hip roll slides each foot
    about 1.5 cm per degree; from the pelvis the legs have to lean the other way, about 1.46 cm
    per degree. Sweeping the two together (`probe-motion.mjs` over a grid) gives one clean
    minimum, and this is it:

      - pelvis travels 3 cm to each side (6.9 cm of travel seen on the avatar);
      - pelvis rolls 3.5 degrees, the spine bends 2.1 back, the chest 0.9, so the torso stays
        over the supporting foot instead of tipping over like a plank;
      - both upper legs lean 6 degrees the other way, which is what keeps the feet planted:
        at 4 degrees they slide 5.5 cm, at 8 degrees 6.1 cm, at 6 degrees 0.3 cm;
      - the head counter-rolls 1.4 degrees and keeps its eye-line level, so it travels 2.6 cm
        while the pelvis travels 6.9;
      - the arms swing 2 degrees, a beat late, as they do when the body moves under them.

    No knee flex: a bent knee with no floor constraint slides the foot, and the grid shows the
    feet are only planted at exactly this pairing.
    """
    sway = lambda t: w(t)  # noqa: E731
    return lambda bone, t: {
        "hips": [((0, 0, 1), 3.5 * sway(t))],
        "spine": [((0, 0, 1), -2.1 * sway(t)), ((1, 0, 0), 0.6 * w(t, 1, 1.5))],
        "chest": [((0, 0, 1), -0.9 * w(t, 1, math.pi))],
        "leftUpperLeg": [((0, 0, 1), 6.0 * sway(t))],
        "rightUpperLeg": [((0, 0, 1), 6.0 * sway(t))],
        "head": [((0, 0, 1), -1.4 * sway(t)), ((0, 1, 0), 1.8 * w(t, 1, math.pi))],
        "leftUpperArm": [((1, 0, 0), 2.2 * w(t, 1, 3.6))],
        "rightUpperArm": [((1, 0, 0), 2.2 * w(t, 1, 0.5))],
        "leftLowerArm": [((1, 0, 0), 4.0 * bump(t, 0.42, 0.62))],
    }.get(bone, [])


def talking_soft(rot, base):
    """Shoulders and forearms carry it; the fingers are not the sentence."""
    return lambda bone, t: {
        "spine": [((0, 1, 0), 1.8 * w(t)), ((1, 0, 0), 0.6 * w(t, 1, 2.0))],
        "chest": [((1, 0, 0), 1.0 * w(t, 2))],
        "leftShoulder": [((1, 0, 0), 3.5 * w(t, 1, 0.3))],
        "rightShoulder": [((1, 0, 0), 3.5 * w(t, 1, 2.1))],
        "leftUpperArm": [((1, 0, 0), 7.0 * w(t, 2, 0.5)), ((0, 0, 1), 2.5 * w(t))],
        "rightUpperArm": [((1, 0, 0), 7.5 * w(t, 2, 2.4)), ((0, 0, 1), 2.5 * w(t, 1, 1.0))],
        "leftLowerArm": [((1, 0, 0), 12.0 * w(t, 2, 1.2))],
        "rightLowerArm": [((1, 0, 0), 12.5 * w(t, 2, 3.0))],
        "leftHand": [((0, 0, 1), 5.0 * w(t, 3))],
        "rightHand": [((0, 0, 1), 5.0 * w(t, 3, 1.7))],
        "head": [((1, 0, 0), 3.0 * w(t, 2, 0.8)), ((0, 1, 0), 2.2 * w(t))],
    }.get(bone, [])


def authored_rotation(bone, t, base_q, terms):
    q = base_q
    for axis, degrees in terms:
        q = q_mul(q, q_axis(axis, degrees))
    return q_norm(q)


CLIPS = {
    # name: (pool, seconds, description, motion)
    "IdleBreath": (
        "idle",
        4.0,
        "Standing at rest, breathing: my spine and chest rise and fall once, my eyes drift "
        "and come back, my arms move a little with the breath.",
        idle_breath,
    ),
    "IdleShift": (
        "idle",
        6.0,
        "Standing at rest, moving my weight from one foot to the other over six seconds, one "
        "hand adjusting as I settle; slower and larger than a breath.",
        idle_shift,
    ),
    "TalkingSoft": (
        "speaking",
        5.0,
        "Talking with my body: shoulders, forearms and hands carry the sentence, my head "
        "nods and turns a little; the fingers stay quiet.",
        talking_soft,
    ),
}


def build(base_dir, out_dir, wanted):
    unknown = [name for name in wanted if name not in CLIPS]
    if unknown:
        # A name that matches nothing used to build nothing and say so quietly, which is how a
        # clip "regenerated" and kept measuring exactly like the old one.
        raise SystemExit(f"no such clip: {', '.join(unknown)}; try one of {', '.join(CLIPS)}")
    made = []
    for name, (pool, seconds, description, motion) in CLIPS.items():
        if wanted and name not in wanted:
            continue
        base_path = os.path.join(base_dir, "idle", "Idle.vrma")
        doc, base_rot, base_trans = tracks_of(base_path)
        standing = standing_pose(base_rot)
        frames = int(round(seconds * FPS)) + 1  # last frame repeats the first
        tracks = {}
        for bone, keys in base_rot.items():
            if not bone:
                continue
            start = standing[bone]
            terms = motion(base_rot, {b: k for b, k in base_rot.items()})
            tracks[bone] = [
                authored_rotation(bone, i / (frames - 1), start, terms(bone, i / (frames - 1)))
                for i in range(frames)
            ]
        hips = None
        if base_trans:
            sway = base_trans[0]
            hips = []
            for i in range(frames):
                t = i / (frames - 1)
                # The translation probe settles the axes: for the hips, local x is sideways in
                # the world, y is fore/aft, z is up/down -- so a "lift" written on y slides her
                # feet backwards instead of lifting her.
                side, up = 0.0, 0.0
                if name == "IdleBreath":
                    # A breath shows in the ribs and shoulders, not in the pelvis: translating
                    # the hips up raises the feet with them (1.4 cm at 6 mm of lift), and with no
                    # floor constraint the only honest way to lift the body is to straighten
                    # knees, which would slide the feet instead. So the pelvis stays put and the
                    # spine, chest and neck carry the breath.
                    side = 0.004 * w(t)
                elif name == "IdleShift":
                    # the shift itself: 3 cm to each side, and a small dip at each end of it,
                    # twice per cycle, as the weight arrives over a foot
                    side = 0.030 * w(t)
                    up = -0.0025 * (1 - math.cos(2 * TAU * t))
                hips.append((sway[0] + side, sway[1], sway[2] + up))
        target = os.path.join(out_dir, pool)
        os.makedirs(target, exist_ok=True)
        path = os.path.join(target, f"{name}.vrma")
        write_glb(path, doc, tracks, hips, description)
        made.append(path)
    return made


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    for path in build(sys.argv[1], sys.argv[2], sys.argv[3:]):
        print("wrote", path)
