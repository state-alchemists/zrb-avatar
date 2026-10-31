"""Rebuild a flat pose export into a .vrma a player can use.

VRM Pose Editor writes a glTF whose nodes are the humanoid bones and nothing else: no
parents, no rest translations, no scene. A .vrma consumer needs the opposite — a skeleton
it can stand the pose on and measure against its own rest — so a file like that loads and
then produces nothing: not-an-number feet, an avatar that vanishes.

Here each bone is given back its parent, by the VRM humanoid it names, and the rotations
the file carries are kept as they are: they are already what a .vrma stores, each bone's
turn relative to the one above it. What is added is the skeleton (identity rests) and the
30 fps time line every sampler wants.

Usage:
    python3 pose2vrma.py SOURCE OUT.vrma [--description "what it looks like"]

    SOURCE   a .gltf (a `data:` buffer or a .bin beside it) or a .glb, holding one animation
    OUT      the .vrma to write
"""

import base64
import json
import struct
import sys
from pathlib import Path

# VRM humanoid bone -> the bone it hangs from. The same tree `gltf2vrma.py` builds from a
# known rig, written out because a flat export has no rig to read it from.
PARENT = {
    "hips": None,
    "spine": "hips",
    "chest": "spine",
    "upperChest": "chest",
    "neck": "upperChest",
    "head": "neck",
    "leftUpperLeg": "hips",
    "leftLowerLeg": "leftUpperLeg",
    "leftFoot": "leftLowerLeg",
    "leftToes": "leftFoot",
    "rightUpperLeg": "hips",
    "rightLowerLeg": "rightUpperLeg",
    "rightFoot": "rightLowerLeg",
    "rightToes": "rightFoot",
    "leftShoulder": "upperChest",
    "leftUpperArm": "leftShoulder",
    "leftLowerArm": "leftUpperArm",
    "leftHand": "leftLowerArm",
    "rightShoulder": "upperChest",
    "rightUpperArm": "rightShoulder",
    "rightLowerArm": "rightUpperArm",
    "rightHand": "rightLowerArm",
    "leftThumbMetacarpal": "leftHand",
    "leftThumbProximal": "leftThumbMetacarpal",
    "leftThumbDistal": "leftThumbProximal",
    "leftIndexProximal": "leftHand",
    "leftIndexIntermediate": "leftIndexProximal",
    "leftIndexDistal": "leftIndexIntermediate",
    "leftMiddleProximal": "leftHand",
    "leftMiddleIntermediate": "leftMiddleProximal",
    "leftMiddleDistal": "leftMiddleIntermediate",
    "leftRingProximal": "leftHand",
    "leftRingIntermediate": "leftRingProximal",
    "leftRingDistal": "leftRingIntermediate",
    "leftLittleProximal": "leftHand",
    "leftLittleIntermediate": "leftLittleProximal",
    "leftLittleDistal": "leftLittleIntermediate",
    "rightThumbMetacarpal": "rightHand",
    "rightThumbProximal": "rightThumbMetacarpal",
    "rightThumbDistal": "rightThumbProximal",
    "rightIndexProximal": "rightHand",
    "rightIndexIntermediate": "rightIndexProximal",
    "rightIndexDistal": "rightIndexIntermediate",
    "rightMiddleProximal": "rightHand",
    "rightMiddleIntermediate": "rightMiddleProximal",
    "rightMiddleDistal": "rightMiddleIntermediate",
    "rightRingProximal": "rightHand",
    "rightRingIntermediate": "rightRingProximal",
    "rightRingDistal": "rightRingIntermediate",
    "rightLittleProximal": "rightHand",
    "rightLittleIntermediate": "rightLittleProximal",
    "rightLittleDistal": "rightLittleIntermediate",
}
FPS = 30


def load_source(path: Path) -> tuple[dict, bytes]:
    """The glTF document and its buffer, from a .gltf with a `data:` or .bin buffer, or a .glb."""
    if path.suffix.lower() in (".glb", ".vrm", ".vrma"):  # a .vrma is a GLB too
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
    uri = (doc.get("buffers") or [{}])[0].get("uri")
    if not uri:
        return doc, b""
    if uri.startswith("data:"):
        return doc, base64.b64decode(uri.partition(",")[2])
    return doc, (path.parent / uri).read_bytes()


def read_accessor(doc: dict, binary: bytes, index: int) -> list[tuple]:
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    count = accessor["count"]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    flat = struct.unpack_from(f"<{count * width}f", binary, start)
    return [flat[i : i + width] for i in range(0, len(flat), width)]


def tracks_of(doc: dict, binary: bytes) -> dict[str, dict[str, list[tuple]]]:
    """bone -> "rotation"/"translation" -> [(time, values)], from the file's one animation."""
    if not doc.get("animations"):
        raise SystemExit("no animation in the file: nothing to write")
    animation = doc["animations"][0]
    names = {i: node.get("name") for i, node in enumerate(doc["nodes"])}
    tracks: dict[str, dict[str, list[tuple]]] = {}
    for channel in animation["channels"]:
        bone = names.get(channel["target"]["node"])
        path = channel["target"]["path"]
        if bone not in PARENT or path not in ("rotation", "translation"):
            continue
        sampler = animation["samplers"][channel["sampler"]]
        times = [t[0] for t in read_accessor(doc, binary, sampler["input"])]
        values = read_accessor(doc, binary, sampler["output"])
        tracks.setdefault(bone, {})[path] = list(zip(times, values))
    return tracks


def resampled(track: list[tuple], times: list[float], is_rotation: bool) -> list[tuple]:
    """*track* read at *times*: a sampler's input is free-form, its consumer's is not."""
    import bisect
    import math

    stamps = [t for t, _ in track]
    out = []
    for time in times:
        i = max(0, min(len(stamps) - 1, bisect.bisect_right(stamps, time) - 1))
        j = min(len(stamps) - 1, i + 1)
        if j == i or stamps[j] == stamps[i]:
            value = list(track[i][1])
        else:
            f = (time - stamps[i]) / (stamps[j] - stamps[i])
            a, b = track[i][1], track[j][1]
            if is_rotation:
                if sum(x * y for x, y in zip(a, b)) < 0:  # the short way round
                    b = [-y for y in b]
                value = [x + (y - x) * f for x, y in zip(a, b)]
                length = math.sqrt(sum(x * x for x in value)) or 1.0
                value = [x / length for x in value]
            else:
                value = [x + (y - x) * f for x, y in zip(a, b)]
        out.append(tuple(value))
    return out


def skeleton_of(path: Path) -> tuple[dict, dict[str, int]]:
    """A .vrma that already plays, as the rest pose to stand this one on: its document, and
    bone -> node index.

    A retarget measures this skeleton against the model's, so it needs real bone positions. A
    flat export has none, and identity rests leave the hips height at zero — which the maths
    divides by, giving a clip that loads and then measures not-a-number.
    """
    doc, _ = load_source(path)
    try:
        humanoid = doc["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]
    except KeyError as exc:
        raise SystemExit(f"{path} is not a VRM animation: {exc}")
    return doc, {bone: data["node"] for bone, data in humanoid.items()}


def times_rotation(first: tuple, second: tuple) -> tuple:
    """*first* then *second*, both quaternions in the parent's frame."""
    ax, ay, az, aw = first
    bx, by, bz, bw = second
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def inverted(quaternion: tuple) -> tuple:
    """The opposite turn: for a unit quaternion, its conjugate."""
    x, y, z, w = quaternion
    return (-x, -y, -z, w)


def parents_of(nodes: list[dict]) -> dict[int, int]:
    """child node -> its parent, as the skeleton's own `children` lists say."""
    parent = {}
    for i, node in enumerate(nodes):
        for child in node.get("children", []):
            parent[child] = i
    return parent


def rest_world_rotations(nodes: list[dict]) -> dict[int, tuple]:
    """Each node's rest rotation in model space, walked down from the roots."""
    parent = parents_of(nodes)
    world: dict[int, tuple] = {}

    def walk(i: int) -> tuple:
        if i in world:
            return world[i]
        own = tuple(nodes[i].get("rotation", (0.0, 0.0, 0.0, 1.0)))
        world[i] = times_rotation(walk(parent[i]), own) if i in parent else own
        return world[i]

    for i in range(len(nodes)):
        walk(i)
    return world


def read_rotations(
    values: list[tuple],
    reading: str,
    nodes: list[dict],
    world: dict[int, tuple],
    parent: dict[int, int],
    node: int,
) -> list[tuple]:
    """The file's turns, read the way *reading* says they mean.

    A flat export does not say what its quaternions are relative to, and the three answers
    look identical in the file:

    - `raw` — each bone's own turn inside its parent, ready to use as it stands;
    - `delta` — the turn away from the rest pose, so composed with the rest;
    - `absolute` — each bone's turn in model space, so the parent's rest is undone first.

    Which one a given editor means is a matter for the render: this tool writes all three and
    `tools/pose2vrma.py --rotation` picks.
    """
    if reading == "raw":
        return values
    if reading == "delta":
        rest = tuple(nodes[node].get("rotation", (0.0, 0.0, 0.0, 1.0)))
        return (
            values
            if rest == (0.0, 0.0, 0.0, 1.0)
            else [times_rotation(rest, value) for value in values]
        )
    above = inverted(world[parent[node]]) if node in parent else (0.0, 0.0, 0.0, 1.0)
    return [times_rotation(above, value) for value in values]


def build(
    base: dict,
    index: dict[str, int],
    doc: dict,
    binary: bytes,
    description: str,
    reading: str = "raw",
) -> bytes:
    tracks = tracks_of(doc, binary)
    bones = [bone for bone in tracks if bone in index]
    if not bones:
        raise SystemExit("the file names no VRM humanoid bones the skeleton knows")
    longest = max(t for bone in tracks.values() for path in bone.values() for t, _ in path)
    frames = int(round(longest * FPS)) + 1
    times = [i / FPS for i in range(frames)]

    nodes = [dict(node) for node in base["nodes"]]  # the skeleton as it stands, rests and all
    world = rest_world_rotations(nodes)
    parents = parents_of(nodes)

    blob, views, accessors, channels, samplers = b"", [], [], [], []

    def put(values: list[tuple], kind: str) -> int:
        nonlocal blob
        width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[kind]
        flat = [x for value in values for x in value]
        start = len(blob)
        blob += struct.pack(f"<{len(flat)}f", *flat)
        while len(blob) % 4:
            blob += b"\0"
        views.append({"buffer": 0, "byteOffset": start, "byteLength": len(flat) * 4})
        accessors.append(
            {
                "bufferView": len(views) - 1,
                "componentType": 5126,
                "count": len(values),
                "type": kind,
                "min": [min(v[i] for v in values) for i in range(width)],
                "max": [max(v[i] for v in values) for i in range(width)],
            }
        )
        return len(accessors) - 1

    time_accessor = put([(t,) for t in times], "SCALAR")
    for bone, paths in tracks.items():
        if bone not in index:  # a finger the skeleton does not have (a 22-node T-pose has none)
            continue
        for path, track in paths.items():
            is_rotation = path == "rotation"
            if path == "translation" and bone != "hips":
                continue  # the spec allows translation on the hips alone
            values = resampled(track, times, is_rotation)
            if is_rotation:
                values = read_rotations(
                    values, reading, nodes, world, parents, index[bone]
                )
            samplers.append(
                {
                    "input": time_accessor,
                    "output": put(values, "VEC4" if is_rotation else "VEC3"),
                    "interpolation": "LINEAR",
                }
            )
            channels.append(
                {
                    "sampler": len(samplers) - 1,
                    "target": {"node": index[bone], "path": path},
                }
            )

    output = {
        "asset": {"version": "2.0", "generator": "zrb-avatar tools/pose2vrma.py"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": base["extensions"],
        "scene": base.get("scene", 0),
        "scenes": base.get("scenes") or [{"nodes": []}],
        "nodes": nodes,
        "animations": [
            {
                "name": "Pose",
                "samplers": samplers,
                "channels": channels,
                "extras": {"description": description} if description else {},
            }
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    text = json.dumps(output, separators=(",", ":")).encode()
    text += b" " * ((4 - len(text) % 4) % 4)
    binary_chunk = blob + b"\0" * ((4 - len(blob) % 4) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary_chunk)
    glb = struct.pack("<III", 0x46546C67, 2, total)
    glb += struct.pack("<II", len(text), 0x4E4F534A) + text
    glb += struct.pack("<II", len(binary_chunk), 0x004E4942) + binary_chunk
    return glb


def main(argv: list[str]) -> int:
    args = argv[1:]
    description = ""
    reading = "raw"
    skeleton = Path(__file__).resolve().parent.parent / "zrb_avatar/anim/idle/Idle.vrma"
    while any(flag in args for flag in ("--description", "--skeleton", "--rotation")):
        for flag in ("--description", "--skeleton", "--rotation"):
            if flag not in args:
                continue
            at = args.index(flag)
            value = args[at + 1]
            del args[at : at + 2]
            if flag == "--description":
                description = value
            elif flag == "--skeleton":
                skeleton = Path(value)
            else:
                reading = value
    if reading not in ("raw", "delta", "absolute"):
        print(f"--rotation is raw, delta or absolute, not {reading}", file=sys.stderr)
        return 2
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, out = Path(args[0]), Path(args[1])
    doc, binary = load_source(source)
    base, index = skeleton_of(skeleton)
    data = build(base, index, doc, binary, description, reading)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{out}: {len(data)} bytes, on {skeleton.name}, rotations read as {reading}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
