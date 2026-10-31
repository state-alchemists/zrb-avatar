"""Put a clip's feet on the library floor, and change nothing else.

Every source in this library measures height from a different origin, and two of them disagree
about which axis is even up. Converted side by side, the result is what Go saw in the window:

    idle/Idle             feet at  +8.3 cm
    idle/Akimbo           feet at -93.3 cm      (a metre underground)
    actions/HitChest      feet at  +9.9 cm

Mixing them makes her jump between heights every time a state changes -- and a clip that buried
her still photographed perfectly, because every camera in the render harness is placed relative
to her. So the height is fixed once, mechanically: the hips move by a constant, and every other
frame of every other bone is the source's. This is a translation, not a correction -- it cannot
change a pose, only where the pose stands.

The axis. `gltf2vrma.py` writes the hips node at [0, 0.05, 0.917] -- the body's height is the
**z**. `bvh2vrma.mjs` writes [0, 0.98, 0] -- the height is the **y**. Both play upright, because
each file's root turns its own convention into the scene's. Reading one axis and shifting it is
therefore right for half the library and silently wrong for the other half, so the axis is read
off the hips node instead of assumed.

The size. What a shift along that axis does to the height the model actually stands at is not
exactly 1:1 -- the hips sit under a rotated parent -- so the shift is applied and then *measured*
again, and any residue is applied again. Three rounds is enough for the residual to reach zero,
and the loop says so if it does not.

Usage:
    python3 floor.py --target 8.3 CLIP.vrma [CLIP.vrma ...]
    python3 floor.py --target 8.3 --report CLIP.vrma      # measure, change nothing

The target is the height the library already agrees on: `idle/Idle`, `Talking` and `Thinking` all
sit at +8.3 cm, and they are what is on screen between everything else.
"""

import argparse
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
core = SourceFileLoader("author", os.path.join(HERE, "author-vrma.py")).load_module()

MODEL = "/tmp/pmcream"
# The name is captured lazily up to the `.vrma`, not as "no spaces": a clip may be called
# `Drunk Dance.vrma` (two in Go's own folder are), and an `\S+` stopped at the space, so the probe's
# answer for it was silently dropped and the file read as "did not answer".
FLOOR = re.compile(r"^(.*?\.vrma)\b.*?\n  floor: lowest foot at ([-\d.]+) cm", re.M)
ROUNDS = 4
SETTLED = 0.05  # cm: closer than this and the clips agree to the eye


def measure(paths):
    """{basename: lowest foot height in cm}, as the probe plays them on the model."""
    out = subprocess.run(
        ["node", "_feet.mjs", MODEL, *paths],
        cwd=HERE, capture_output=True, text=True, check=True,
    ).stdout
    return {os.path.basename(name): float(cm) for name, cm in FLOOR.findall(out)}


def up_axis(doc):
    """Which component of the hips translation is the body's height in this file."""
    human = doc["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]
    translation = doc["nodes"][human["hips"]["node"]].get("translation") or [0, 0, 0]
    return max(range(3), key=lambda i: abs(translation[i]))


def source_description(doc):
    animations = doc.get("animations") or []
    extras = animations[0].get("extras") if animations else None
    if isinstance(extras, dict) and isinstance(extras.get("description"), str):
        return extras["description"]
    return None


def per_cm(hips, axis):
    """What one centimetre of shift is worth, in this file's own units.

    A VRMA writes metres -- `gltf2vrma.py` puts the hips at 0.917, `bvh2vrma.mjs` at 0.98 -- so a
    shift in centimetres has to be divided by 100. Two of Go's own clips are not: their hips read
    **90.57**, a centimetre figure with a scaled ancestor above it, and dividing by 100 moved them
    by a hundredth of what was asked. `floor.py` asked twice, measured 12.1 -> 11.9, and gave up
    reporting them off the floor, which is what it should do when a shift does not take -- but the
    cause was here, not in the clip. Anything above ten is centimetres; the tallest hips a metre
    figure could name is the model itself.
    """
    return 1.0 if abs(hips[0][axis]) > 10 else 0.01


def shift(path, axis, delta_cm, description=None):
    """Move the hips by `delta_cm` along `axis`, and keep everything else the source's."""
    doc, rot, hips = core.tracks_of(path)
    if not hips:
        raise SystemExit(f"{os.path.basename(path)}: no hips translation to move")
    unit = per_cm(hips, axis)
    moved = [
        tuple(v + delta_cm * unit if i == axis else v for i, v in enumerate(row))
        for row in hips
    ]
    core.write_glb(path, doc, rot, moved,
                   description if description is not None else source_description(doc))


def main():
    parser = argparse.ArgumentParser(
        description="Put a clip's feet on the library floor, and change nothing else.")
    parser.add_argument("clips", nargs="+")
    parser.add_argument("--target", type=float, default=8.3,
                        help="shared floor, in cm above the model's origin")
    parser.add_argument("--report", action="store_true", help="measure, change nothing")
    parser.add_argument("--description", default=None,
                        help="what the clip says about itself; the source's is kept if left out")
    args = parser.parse_args()

    paths = [os.path.abspath(p) for p in args.clips]
    before = measure(paths)
    missing = [p for p in paths if os.path.basename(p) not in before]
    if missing:
        raise SystemExit(f"the probe did not answer for: {', '.join(missing)}")

    if args.report:
        for path in paths:
            at = before[os.path.basename(path)]
            print(f"{os.path.basename(path):<26} {at:+7.1f} cm   {at - args.target:+6.1f} from target")
        return 0

    axes = {}
    for path in paths:
        doc, _, _ = core.tracks_of(path)
        axes[os.path.basename(path)] = up_axis(doc)

    at = dict(before)
    for round_number in range(ROUNDS):
        # `shift` is also what writes the description, so with one given the first round has to run
        # for every clip, a delta of zero included; without one, only the clips that are actually
        # off the floor are rewritten. That matters for the home folder: a rewrite the package has
        # already delivered marks the copy "edited" and freezes it out of later updates.
        writes_description = args.description is not None
        unsettled = [
            p for p in paths
            if (round_number == 0 and writes_description)
            or abs(at[os.path.basename(p)] - args.target) > SETTLED
        ]
        if not unsettled:
            break
        for path in unsettled:
            name = os.path.basename(path)
            shift(path, axes[name], args.target - at[name], args.description)
        at.update(measure(unsettled))

    for path in paths:
        name = os.path.basename(path)
        print(f"{name:<26} {before[name]:+7.1f} cm -> {at[name]:+7.1f} cm   "
              f"(up is {'xyz'[axes[name]]})")

    off = {n: h for n, h in at.items() if abs(h - args.target) > 0.15}
    print(f"\n{len(paths)} measured, {len(off)} still off the floor")
    for name, height in sorted(off.items()):
        print(f"  {name}: {height:+.1f} cm")
    return 1 if off else 0


if __name__ == "__main__":
    sys.exit(main())
