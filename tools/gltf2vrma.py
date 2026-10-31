"""Convert a rigged glTF (or GLB) holding animations into .vrma files.

VRM Animation wants motion on VRM humanoid bone names, so this does three things:

1. maps the source rig's bone names to VRM humanoid names, for one of a few known
   rigs -- Blender/Rigify (`DEF-` names), Mixamo (`mixamorig:` names), Unreal's
   mannequin (`pelvis`, `upperarm_l`), or a glTF that already uses humanoid names;
   `--rig auto` picks by looking at the nodes,
2. measures the rig's pose offset from its own T-pose clip when the file has one
   (`A_TPose`, `T-Pose`, ...), because a .vrma is applied against the destination
   model's T-pose: copying rotations straight across arrives offset whenever the
   source rest pose is not one,
3. writes one GLB per clip carrying only the humanoid channels: rotation for every
   mapped bone, translation for the hips alone, which is all the spec allows.

Every channel of a clip is resampled onto one 30 fps time line (rotations slerped),
and every sampler input carries the min/max glTF requires -- both are what a
consumer expects, and a clip missing either is refused or reported as having no
length.

Usage:
    python3 gltf2vrma.py SOURCE OUT_DIR [--rig auto|rigify|mixamo|unreal|vrm|vrm-meta]
                                        [--tpose NAME] [--list] [CLIP ...]

    SOURCE   a .gltf (with its .bin beside it) or a .glb
    OUT_DIR  created when missing; one <clip name>.vrma per animation
    --list   print the rig's bones, the mapped and the unmapped ones, and stop
    CLIP...  only these animations (default: all of them)

A .vrma holds no mesh, no skin and no expression: only the humanoid channels.
"""

import bisect
import json
import math
import struct
import sys
from pathlib import Path

TPOSE_CANDIDATES = ("A_TPose", "T-Pose", "T_Pose", "TPose", "tpose")

# Rigify bone -> VRM humanoid bone. Fingers are three joints each, which is
# exactly the VRM finger chain (proximal, intermediate, distal).
RIGIFY_BONES = {
    "DEF-hips": "hips",
    "DEF-spine.001": "spine",
    "DEF-spine.002": "chest",
    "DEF-spine.003": "upperChest",
    "DEF-neck": "neck",
    "DEF-head": "head",
    "DEF-thigh.L": "leftUpperLeg",
    "DEF-shin.L": "leftLowerLeg",
    "DEF-foot.L": "leftFoot",
    "DEF-toe.L": "leftToes",
    "DEF-thigh.R": "rightUpperLeg",
    "DEF-shin.R": "rightLowerLeg",
    "DEF-foot.R": "rightFoot",
    "DEF-toe.R": "rightToes",
    "DEF-shoulder.L": "leftShoulder",
    "DEF-upper_arm.L": "leftUpperArm",
    "DEF-forearm.L": "leftLowerArm",
    "DEF-hand.L": "leftHand",
    "DEF-shoulder.R": "rightShoulder",
    "DEF-upper_arm.R": "rightUpperArm",
    "DEF-forearm.R": "rightLowerArm",
    "DEF-hand.R": "rightHand",
}

FINGERS = ("thumb", "f_index", "f_middle", "f_ring", "f_pinky")
FINGER_BONES = ("Thumb", "Index", "Middle", "Ring", "Little")
FINGER_JOINTS = ("Metacarpal", "Proximal", "Intermediate", "Distal")

for _side, _suffix in (("left", ".L"), ("right", ".R")):
    for _finger, _vrm_finger in zip(FINGERS, FINGER_BONES):
        joints = (
            ("Metacarpal", "01"),
            ("Proximal", "02"),
            ("Distal", "03"),
        )
        if _finger != "thumb":
            joints = (
                ("Proximal", "01"),
                ("Intermediate", "02"),
                ("Distal", "03"),
            )
        for _joint, _number in joints:
            RIGIFY_BONES[f"DEF-{_finger}.{_number}{_suffix}"] = (
                f"{_side}{_vrm_finger}{_joint}"
            )

# Mixamo's bone names, as an FBX-to-glTF export writes them (`mixamorig:Hips`, or
# `mixamorigHips` when the separator is dropped). Its rest pose is a T-pose, so the
# offset measured below comes out identity for a Mixamo rig -- which is why the same
# code serves both.
MIXAMO_BONES = {
    "Hips": "hips",
    "Spine": "spine",
    "Spine1": "chest",
    "Spine2": "upperChest",
    "Neck": "neck",
    "Head": "head",
}
for _mixamo_side, _vrm_side in (("Left", "left"), ("Right", "right")):
    MIXAMO_BONES.update(
        {
            f"{_mixamo_side}{_part}": f"{_vrm_side}{_vrm_part}"
            for _part, _vrm_part in (
                ("Shoulder", "Shoulder"),
                ("Arm", "UpperArm"),
                ("ForeArm", "LowerArm"),
                ("Hand", "Hand"),
                ("UpLeg", "UpperLeg"),
                ("Leg", "LowerLeg"),
                ("Foot", "Foot"),
                ("ToeBase", "Toes"),
                ("HandThumb1", "ThumbMetacarpal"),
                ("HandThumb2", "ThumbProximal"),
                ("HandThumb3", "ThumbDistal"),
            )
        }
    )
    for _mixamo_finger, _vrm_finger in (
        ("Index", "Index"),
        ("Middle", "Middle"),
        ("Ring", "Ring"),
        ("Pinky", "Little"),
    ):
        for _number, _joint in (("1", "Proximal"), ("2", "Intermediate"), ("3", "Distal")):
            MIXAMO_BONES[f"{_mixamo_side}Hand{_mixamo_finger}{_number}"] = (
                f"{_vrm_side}{_vrm_finger}{_joint}"
            )

# Unreal Engine's mannequin, as the newer Quaternius pack is exported (`pelvis`, `spine_01`,
# `upperarm_l`, `index_01_l`). These are Unreal's own retargeting names, which is why the second
# pack arrives in a different spelling from the first even though both came from the same author.
UNREAL_BONES = {
    "pelvis": "hips",
    "spine_01": "spine",
    "spine_02": "chest",
    "spine_03": "upperChest",
    "neck_01": "neck",
    "Head": "head",
}
for _ue_side, _vrm_side in (("_l", "left"), ("_r", "right")):
    UNREAL_BONES.update(
        {
            f"clavicle{_ue_side}": f"{_vrm_side}Shoulder",
            f"upperarm{_ue_side}": f"{_vrm_side}UpperArm",
            f"lowerarm{_ue_side}": f"{_vrm_side}LowerArm",
            f"hand{_ue_side}": f"{_vrm_side}Hand",
            f"thigh{_ue_side}": f"{_vrm_side}UpperLeg",
            f"calf{_ue_side}": f"{_vrm_side}LowerLeg",
            f"foot{_ue_side}": f"{_vrm_side}Foot",
            f"ball{_ue_side}": f"{_vrm_side}Toes",
            f"thumb_01{_ue_side}": f"{_vrm_side}ThumbMetacarpal",
            f"thumb_02{_ue_side}": f"{_vrm_side}ThumbProximal",
            f"thumb_03{_ue_side}": f"{_vrm_side}ThumbDistal",
        }
    )
    for _ue_finger, _vrm_finger in (
        ("index", "Index"),
        ("middle", "Middle"),
        ("ring", "Ring"),
        ("pinky", "Little"),
    ):
        for _number, _joint in (("01", "Proximal"), ("02", "Intermediate"), ("03", "Distal")):
            UNREAL_BONES[f"{_ue_finger}_{_number}{_ue_side}"] = f"{_vrm_side}{_vrm_finger}{_joint}"

# A glTF already built for VRM needs no mapping at all: the names are the names.
_HUMANOID_CORE = ("hips", "spine", "chest", "upperChest", "neck", "head")
_HUMANOID_LIMBS = (
    "Shoulder",
    "UpperArm",
    "LowerArm",
    "Hand",
    "UpperLeg",
    "LowerLeg",
    "Foot",
    "Toes",
)
_HUMANOID_FINGERS = ("Thumb", "Index", "Middle", "Ring", "Little")
_HUMANOID_JOINTS = ("Metacarpal", "Proximal", "Intermediate", "Distal")
VRM_BONES = {name: name for name in _HUMANOID_CORE}
VRM_BONES.update(
    {
        f"{side}{limb}": f"{side}{limb}"
        for side in ("left", "right")
        for limb in _HUMANOID_LIMBS
    }
)
VRM_BONES.update(
    {
        f"{side}{finger}{joint}": f"{side}{finger}{joint}"
        for side in ("left", "right")
        for finger in _HUMANOID_FINGERS
        for joint in _HUMANOID_JOINTS
    }
)

RIG_TABLES = {
    "rigify": RIGIFY_BONES,
    "mixamo": MIXAMO_BONES,
    "unreal": UNREAL_BONES,
    "vrm": VRM_BONES,
}

COMPONENT_FLOAT = 5126
ARRAY_VEC4 = "VEC4"
ARRAY_VEC3 = "VEC3"


def quat_multiply(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def quat_inverse(q: list[float]) -> list[float]:
    x, y, z, w = q
    return [-x, -y, -z, w]


def read_accessor(doc: dict, binary: bytes, index: int, width: int) -> list[tuple]:
    """The values of a float accessor, as tuples of *width* floats."""
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    if (view.get("byteStride") or 0) not in (0, width * 4):
        raise ValueError(f"accessor {index} is interleaved, not supported")
    flat = struct.unpack_from(f"<{count * width}f", binary, start)
    return [flat[i : i + width] for i in range(0, len(flat), width)]


def load_source(path: Path) -> tuple[dict, bytes]:
    """The glTF document and its binary buffer, from a .gltf+.bin pair or one .glb."""
    if path.suffix.lower() in (".glb", ".vrm"):  # a .vrm is a GLB with a VRM extension
        raw = path.read_bytes()
        offset, doc, binary = 12, None, b""
        while offset < len(raw):
            length, kind = struct.unpack_from("<II", raw, offset)
            chunk = raw[offset + 8 : offset + 8 + length]
            if kind == 0x4E4F534A:
                doc = json.loads(chunk)
            elif kind == 0x004E4942:
                binary = chunk
            offset += 8 + length + (-length % 4)
        if doc is None:
            raise SystemExit(f"{path} has no JSON chunk: not a GLB")
        return doc, binary
    doc = json.loads(path.read_text())
    buffers = doc.get("buffers") or [{}]
    uri = buffers[0].get("uri")
    if not uri:
        return doc, b""
    return doc, (path.parent / uri).read_bytes()


def humanoid_of(node_name: str, rig: str) -> str | None:
    """The VRM humanoid bone a node stands for, or None. Mixamo's prefix is optional."""
    plain = node_name.replace("mixamorig:", "").replace("mixamorig", "").strip()
    return RIG_TABLES[rig].get(plain)


def rig_from_vrm_meta(doc: dict) -> dict[str, str] | None:
    """A VRM file's own humanoid map, as {node name: humanoid bone}.

    A VRoid export names its bones `J_Bip_C_Hips` and keeps the humanoid mapping in the
    `VRMC_vrm` extension, so the names themselves say nothing. Reading that map is the
    only way a VRM can serve as a source rig.
    """
    humanoid = (doc.get("extensions", {}).get("VRMC_vrm") or {}).get("humanoid") or {}
    bones = humanoid.get("humanBones") or {}
    table: dict[str, str] = {}
    for bone, entry in bones.items():
        node = (entry or {}).get("node")
        if isinstance(node, int) and 0 <= node < len(doc.get("nodes", [])):
            table[doc["nodes"][node].get("name", "")] = bone
    return table or None


def detect_rig(doc: dict) -> str:
    """Which known rig this file is, from the bone names it actually has."""
    if rig_from_vrm_meta(doc) is not None:
        return "vrm-meta"
    names = [node.get("name", "") for node in doc["nodes"]]
    if any(name.startswith("DEF-") for name in names):
        return "rigify"
    if any("mixamorig" in name for name in names):
        return "mixamo"
    if "pelvis" in names and any(name.startswith("upperarm_") for name in names):
        return "unreal"
    if any(name in VRM_BONES for name in names):
        return "vrm"
    raise SystemExit(
        "cannot tell which rig this is: pass --rig rigify|mixamo|unreal|vrm, or --list to see "
        f"the bone names. First nodes: {', '.join(names[:8])}"
    )


def pose_offsets(doc: dict, binary: bytes, tpose_name: str | None) -> dict[int, list[float]]:
    """Per-bone quaternion that turns the rig's rest pose into its own T-pose.

    Rotations in a .vrma are applied against the destination model's rest pose,
    which VRM fixes as T-pose. When the file carries a T-pose clip the offset can be
    measured instead of assumed: offset = q_tpose * inverse(q_rest). Without one it is
    identity, which is right exactly when the rest pose is already a T-pose (Mixamo's
    is) -- otherwise the clips arrive skewed, and no amount of code here can know.
    """
    rest = {i: node.get("rotation", [0, 0, 0, 1]) for i, node in enumerate(doc["nodes"])}
    clip = None
    if tpose_name:
        clip = next((a for a in doc["animations"] if a.get("name") == tpose_name), None)
        if clip is None:
            print(f"no clip named {tpose_name!r}: assuming the rest pose is a T-pose")
    if clip is None:
        return {index: [0, 0, 0, 1] for index in rest}
    offsets: dict[int, list[float]] = {}
    for channel in clip["channels"]:
        if channel["target"]["path"] != "rotation":
            continue
        node = channel["target"]["node"]
        sampler = clip["samplers"][channel["sampler"]]
        first = read_accessor(doc, binary, sampler["output"], 4)[0]
        offsets[node] = quat_multiply(list(first), quat_inverse(list(rest[node])))
    return offsets


def find_tpose(doc: dict) -> str | None:
    """The clip that looks like a T-pose, by name, so `--tpose` need not be passed."""
    for animation in doc.get("animations", []):
        name = animation.get("name", "")
        if name.strip().lower() in (candidate.lower() for candidate in TPOSE_CANDIDATES):
            return name
    return None


def clip_payload(doc: dict, binary: bytes, clip: dict, offsets: dict, rig: str) -> dict:
    """One animation's channels, mapped and offset, ready to serialise."""
    bones: dict[str, int] = {}
    for index, node in enumerate(doc["nodes"]):
        name = humanoid_of(node.get("name", ""), rig)
        if name:
            bones[name] = index
    by_node = {index: name for name, index in bones.items()}

    channels = []
    for channel in clip["channels"]:
        node = channel["target"]["node"]
        path = channel["target"]["path"]
        if node not in by_node or path not in ("rotation", "translation"):
            continue
        # The spec allows translation on the hips alone.
        if path == "translation" and by_node[node] != "hips":
            continue
        sampler = clip["samplers"][channel["sampler"]]
        times = read_accessor(doc, binary, sampler["input"], 1)
        width = 4 if path == "rotation" else 3
        values = read_accessor(doc, binary, sampler["output"], width)
        if path == "rotation":
            offset = offsets.get(node, [0, 0, 0, 1])
            values = [tuple(quat_multiply(list(offset), list(v))) for v in values]
        channels.append(
            {"node": node, "path": path, "times": [t[0] for t in times],
             "values": values, "width": width}
        )
    return {"bones": bones, "channels": _one_timeline(channels)}


FPS = 30
"""One grid for the whole clip: every channel shares it, as the face server expects."""


def quat_slerp(a: list[float], b: list[float], t: float) -> list[float]:
    """*a* turned *t* of the way to *b*, along the shorter arc."""
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-x for x in b], -dot
    if dot > 0.9995:
        out = [x + t * (y - x) for x, y in zip(a, b)]
    else:
        theta = math.acos(max(-1.0, min(1.0, dot)))
        sin = math.sin(theta)
        out = [
            (math.sin((1 - t) * theta) / sin) * x + (math.sin(t * theta) / sin) * y
            for x, y in zip(a, b)
        ]
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


def _sample(channel: dict, moment: float) -> tuple:
    """The channel's value at *moment*, interpolated between its own keyframes."""
    times, values, width = channel["times"], channel["values"], channel["width"]
    if moment <= times[0]:
        return tuple(values[0])
    if moment >= times[-1]:
        return tuple(values[-1])
    index = bisect.bisect_right(times, moment) - 1
    index = max(0, min(index, len(times) - 2))
    span = times[index + 1] - times[index]
    alpha = 0.0 if span <= 0 else (moment - times[index]) / span
    start, end = list(values[index]), list(values[index + 1])
    if width == 4:
        return tuple(quat_slerp(start, end, alpha))
    return tuple(s + (e - s) * alpha for s, e in zip(start, end))


def _one_timeline(channels: list[dict]) -> list[dict]:
    """Every channel resampled onto one 30 fps grid.

    The source clips key each bone when it changes, so channels carry different
    keyframe counts and different times. Resampling here is what makes a clip a
    single time line: the face server asserts one keyframe count across all
    channels, and a mixed set plays unevenly.
    """
    if not channels:
        return channels
    end = max(channel["times"][-1] for channel in channels)
    steps = max(1, round(end * FPS))
    grid = [round(i / FPS, 6) for i in range(steps + 1)]
    out = []
    for channel in channels:
        out.append(
            {
                "node": channel["node"],
                "path": channel["path"],
                "times": grid,
                "values": [_sample(channel, moment) for moment in grid],
                "width": channel["width"],
            }
        )
    return out


# What a .vrma's nodes may keep. The source is a character glTF, so its nodes
# also point at meshes and skins (`mesh: 0, skin: 0` on the mannequin). Those
# arrays do not come along, and a dangling `skin` is not a harmless extra:
# three's GLTFLoader crashes outright building its scene graph, and a strict
# validator rejects the file — which is how 3dretarget's viewer refused every clip.
NODE_KEYS = ("name", "translation", "rotation", "scale", "children")


def animation_nodes(doc: dict) -> list[dict]:
    """The pack's node tree, with mesh and skin references stripped."""
    return [
        {key: node[key] for key in NODE_KEYS if key in node} for node in doc["nodes"]
    ]


def build_glb(doc: dict, payload: dict) -> bytes:
    """A GLB carrying one animation and the humanoid bone map."""
    binary = bytearray()
    accessors, views, samplers, channels = [], [], [], []

    def add_accessor(values: list[tuple], width: int, kind: str) -> int:
        offset = len(binary)
        for value in values:
            binary.extend(struct.pack(f"<{width}f", *value))
        while len(binary) % 4:
            binary.append(0)
        views.append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(binary) - offset}
        )
        accessors.append(
            {
                "bufferView": len(views) - 1,
                "componentType": COMPONENT_FLOAT,
                "count": len(values),
                "type": kind,
                # glTF requires min/max on an animation sampler's input accessor,
                # and consumers read a clip's length straight off it — the avatar
                # server shows "seconds: None" without them.
                "min": [min(value[i] for value in values) for i in range(width)],
                "max": [max(value[i] for value in values) for i in range(width)],
            }
        )
        return len(accessors) - 1

    for channel in payload["channels"]:
        times = add_accessor([(t,) for t in channel["times"]], 1, "SCALAR")
        kind = ARRAY_VEC4 if channel["width"] == 4 else ARRAY_VEC3
        values = add_accessor(channel["values"], channel["width"], kind)
        samplers.append({"input": times, "output": values, "interpolation": "LINEAR"})
        channels.append(
            {
                "sampler": len(samplers) - 1,
                "target": {"node": channel["node"], "path": channel["path"]},
            }
        )

    out = {
        "asset": {"version": "2.0", "generator": "zrb-avatar gltf2vrma"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": animation_nodes(doc),
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
        "animations": [{"samplers": samplers, "channels": channels}],
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {
            "VRMC_vrm_animation": {
                "specVersion": "1.0",
                "humanoid": {
                    "humanBones": {
                        name: {"node": index}
                        for name, index in payload["bones"].items()
                    }
                },
            }
        },
    }

    # A GLB points at node 0 as its root scene entry; the pack's node 0 is a
    # bone, so list the actual roots instead.
    roots = [
        index
        for index in range(len(doc["nodes"]))
        if not any(index in node.get("children", []) for node in doc["nodes"])
    ]
    out["scenes"] = [{"nodes": roots}]
    out["buffers"][0]["byteLength"] = len(binary) + (-len(binary) % 4)

    json_bytes = json.dumps(out, separators=(",", ":")).encode()
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    binary.extend(b"\0" * ((-len(binary)) % 4))
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(binary), 0x004E4942)
        + bytes(binary)
    )


def parse_args(argv: list[str]) -> tuple[Path, Path, list[str], str, str | None, bool]:
    """SOURCE, OUT_DIR, clips, rig, tpose, and whether to only list bones."""
    rig, tpose, show_bones, positional = "auto", None, False, []
    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        if arg == "--rig":
            rig = args.pop(0) if args else "auto"
        elif arg == "--tpose":
            tpose = args.pop(0) if args else None
        elif arg == "--list":
            show_bones = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        elif arg == "--":
            positional.extend(args)
            args.clear()
        else:
            positional.append(arg)
    if not positional:
        print(__doc__)
        raise SystemExit(2)
    if rig != "auto" and rig not in RIG_TABLES:
        raise SystemExit(f"unknown rig {rig!r}: one of auto, {', '.join(RIG_TABLES)}")
    source = Path(positional[0])
    out_dir = Path(positional[1]) if len(positional) > 1 else Path.cwd()
    return source, out_dir, positional[2:], rig, tpose, show_bones


def list_bones(doc: dict, rig: str) -> int:
    """What the rig maps to, and what it has that no humanoid bone wants."""
    mapped, unmapped = [], []
    for node in doc["nodes"]:
        name = node.get("name", "")
        human = humanoid_of(name, rig)
        (mapped if human else unmapped).append(f"{name} -> {human}" if human else name)
    print(f"mapped ({len(mapped)}):")
    for entry in mapped:
        print(f"  {entry}")
    print(f"unmapped ({len(unmapped)}):")
    for entry in unmapped:
        print(f"  {entry}")
    return 0


def main(argv: list[str]) -> int:
    source, out_dir, wanted, rig, tpose, show_bones = parse_args(argv)
    doc, binary = load_source(source)
    # A VRM carries its own humanoid map, so that table is built from the file rather
    # than from a constant: installed here so the rest of the module need not know.
    from_meta = rig_from_vrm_meta(doc)
    if from_meta is not None:
        RIG_TABLES["vrm-meta"] = from_meta
    rig = detect_rig(doc) if rig == "auto" else rig
    if rig == "vrm-meta" and not from_meta:
        raise SystemExit(f"{source.name} has no VRMC_vrm humanoid map to read")
    print(
        f"{source.name}: rig {rig}, {len(doc.get('nodes', []))} nodes, "
        f"{len(doc.get('animations', []))} clips"
    )
    if show_bones:
        return list_bones(doc, rig)

    tpose = tpose or find_tpose(doc)
    offsets = pose_offsets(doc, binary, tpose)
    moved = [
        f"{doc['nodes'][index].get('name')}={[round(v, 3) for v in value]}"
        for index, value in offsets.items()
        if abs(value[3]) < 0.999
    ]
    where = f"clip {tpose!r}" if tpose else "the rest pose (no T-pose clip)"
    print(f"pose offsets from {where}: {len(offsets)} bones, {len(moved)} not identity")
    for entry in moved[:6]:
        print(f"  {entry}")

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for clip in doc.get("animations", []):
        name = clip.get("name", "clip")
        if wanted and name not in wanted:
            continue
        payload = clip_payload(doc, binary, clip, offsets, rig)
        if not payload["channels"]:
            print(f"  skip {name}: no humanoid channels")
            continue
        (out_dir / f"{name}.vrma").write_bytes(build_glb(doc, payload))
        written += 1
    print(f"wrote {written} clips to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
