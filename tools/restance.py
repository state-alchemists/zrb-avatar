"""Pull a captured clip's feet under the pelvis, and nothing else.

A capture stands the way its performer stood: Quaternius' idle holds a 55 cm stride, one
foot 12 cm ahead of the pelvis and the other 20 cm behind. That is fine in a game and wrong
at a desk — and mixed into one library it is worse than wrong, because an action ending on
a 22 cm stance and a loop holding 55 cm makes her feet jump every time a gesture finishes.

So the stance is corrected mechanically rather than re-animated: each thigh takes a constant
rotation that brings its foot to the target, and the hips rise by however far the feet
dropped when the legs came under her. Every other frame of every other bone is untouched, so
the breathing, the head, the arms and the timing stay the capture's.

What this cannot do, and says so:

  - **A constant correction slides the feet of a clip that moves its weight.** The legs turn
    the same amount on every frame, so a pelvis that sways carries the feet with it. `--drift`
    measures how far the feet travel in the source; past a couple of centimetres, the clip
    wants per-frame planting, not this.
  - **It does not fix knees or toes.** Rotating a thigh turns the knee and the foot too. The
    numbers cannot tell you whether that reads; render the `legs` view and look.

Usage:
    python3 restance.py IN.vrma OUT.vrma [--width 22.3] [--drift] [--description TEXT]

`--width` is the stance to land on, in centimetres between the feet, centred on the pelvis.
The default is the width the authored gestures stand on, so that the whole library agrees.
`--description` replaces the clip's own; left out, whatever the source said about itself is
kept, because correcting a stance is not a reason to relabel a clip.
"""

import argparse
import itertools
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
core = SourceFileLoader("author", os.path.join(HERE, "author-vrma.py")).load_module()

AXIS = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
LEG = {"left": "leftUpperLeg", "right": "rightUpperLeg"}
FEET = re.compile(
    r"(\S+\.vrma)\s+L foot ([-\d.]+),([-\d.]+),([-\d.]+)\s+"
    r"R foot ([-\d.]+),([-\d.]+),([-\d.]+)\s+apart\s+([\d.]+).*?"
    r"world L ([-\d.]+),([-\d.]+) R ([-\d.]+),([-\d.]+)"
)
# coarse to fine, so a 25 cm move is found with a handful of probes rather than a thousand
COARSE = list(itertools.product(range(-21, 22, 7), range(-28, 29, 7)))
FINE = [(dz, dx) for dz, dx in itertools.product(range(-3, 4), range(-3, 4))]


def feet_of(paths, drift=False):
    """Where the probe says the feet are: {file: {"left": [side, fwd, up], "right": [...]}}."""
    args = ["node", "_feet.mjs", "/tmp/pmcream", *paths]
    if drift:
        args.append("--drift")
    out = subprocess.run(args, cwd=HERE, capture_output=True, text=True, check=True).stdout
    found = {}
    for line in out.splitlines():
        m = FEET.search(line)
        if m:
            found[os.path.basename(m.group(1))] = {
                "left": [float(m.group(i)) for i in (2, 3, 4)],
                "right": [float(m.group(i)) for i in (5, 6, 7)],
                # the world distance between the feet, and each foot's position on fixed axes
                "apart": float(m.group(8)),
                "world": {"left": (float(m.group(9)), float(m.group(10))),
                          "right": (float(m.group(11)), float(m.group(12)))},
            }
    return found


class Clip:
    """One clip, with the rig the clip was captured on kept verbatim."""

    def __init__(self, source, work, description=None):
        self.doc, self.rot, trans = core.tracks_of(source)
        self.hips = [tuple(t) for t in trans]
        self.base_hips = list(self.hips)
        self.work = work
        self.description = description
        self.angles = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.serial = 0
        os.makedirs(work, exist_ok=True)
        for name in os.listdir(work):
            os.remove(os.path.join(work, name))

    def write(self, tag):
        self.serial += 1
        path = os.path.join(self.work, f"{self.serial:04d}-{tag}.vrma")
        tracks = {}
        for bone, keys in self.rot.items():
            if not bone:
                continue
            side = next((s for s, b in LEG.items() if b == bone), None)
            z, x = self.angles[side] if side else (0.0, 0.0)
            out = []
            for q in keys:
                for axis, deg in (("x", x), ("z", z)):
                    if abs(deg) > 1e-9:
                        q = core.q_mul(q, core.q_axis(AXIS[axis], deg))
                out.append(core.q_norm(q))
            tracks[bone] = out
        core.write_glb(path, self.doc, tracks, self.hips, self.description)
        return path

    def measure(self, tag="probe"):
        path = self.write(tag)
        return feet_of([path])[os.path.basename(path)]


def solve_leg(clip, side, target, steps):
    """Try every step for one thigh, keep the one that lands its foot nearest the target."""
    best, best_cost = clip.angles[side], None
    for dz, dx in steps:
        clip.angles[side] = (best[0] + dz, best[1] + dx) if steps is FINE else (dz, dx)
        feet = clip.measure()
        cost = cost_of(feet, side, target)
        if best_cost is None or cost < best_cost:
            best, best_cost = clip.angles[side], cost
    clip.angles[side] = best
    return best_cost


def cost_of(feet, side, target):
    # scored on fixed axes, not the pelvis frame: that frame is built out of the two thighs, so it
    # turns when they do, and a solver reading it would be rewarded for moving the ruler — the
    # first version "passed" with the legs crossed over each other
    x, z = feet["world"][side]
    return abs(x - target[0]) + abs(z - target[1])


def main():
    parser = argparse.ArgumentParser(
        description="Pull a captured clip's feet under the pelvis, and nothing else.")
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--width", type=float, default=22.3)
    parser.add_argument("--description", default=None)
    parser.add_argument("--drift", action="store_true", help="report how far the feet travel")
    args = parser.parse_args()
    # a relative target would be resolved against this script's folder by the probe, which runs
    # there — and the run would then die after writing the file, half-reported
    args.source = os.path.abspath(args.source)
    args.target = os.path.abspath(args.target)

    half = args.width / 2
    goal = {"left": (half, 0.0), "right": (-half, 0.0)}
    before = feet_of([args.source], drift=args.drift)[os.path.basename(args.source)]
    floor = (before["left"][2] + before["right"][2]) / 2

    clip = Clip(args.source, "/tmp/restance-work", description=args.description)
    if clip.description is None:
        # the clip keeps what it already said about itself; only a clip that said nothing gets
        # nothing, rather than every action inheriting an idle loop's description
        animations = clip.doc.get("animations") or []
        extras = animations[0].get("extras") if animations else None
        if isinstance(extras, dict) and isinstance(extras.get("description"), str):
            clip.description = extras["description"]
    for side in ("left", "right"):
        solve_leg(clip, side, goal[side], COARSE)
    # the two thighs share the frame the feet are measured in — the pelvis is built from them —
    # so one pass leaves a residue; alternating settles it
    for _ in range(3):
        for side in ("left", "right"):
            solve_leg(clip, side, goal[side], FINE)

    # The legs came under her, so the feet sit lower: lift the hips by exactly that much and the
    # feet return to the floor they stood on. `z` is up in every clip this tool runs on -- the
    # Quaternius files carry the body's height in z, and their hips node sits at [0, 0.05, 0.917]
    # to say so. The 100STYLE files put it in y instead; those arrive with a correct stance and do
    # not come through here. `tools/floor.py` is the authority on the height regardless: it measures
    # the floor the model actually stands at and moves the hips until the clip agrees with the rest
    # of the library, so whatever residue this leaves is taken out there.
    dropped = (clip.measure()["left"][2] + clip.measure()["right"][2]) / 2 - floor
    clip.hips = [(x, y, z - dropped / 100) for x, y, z in clip.base_hips]
    after = clip.measure("final")

    final = os.path.join(clip.work, f"{clip.serial:04d}-final.vrma")
    os.replace(final, args.target)
    print(f"{os.path.basename(args.source)} -> {args.target}")
    for side in ("left", "right"):
        z, x = clip.angles[side]
        print(f"  {side:5} thigh z {z:+.0f} x {x:+.0f}   "
              f"foot {before[side]} -> {after[side]}")
    print(f"  apart {before['apart']:.1f} cm -> {after['apart']:.1f} cm (target {args.width}); "
          f"hips lifted {-dropped:+.1f} cm")
    if args.drift:
        for label, path in (("before", args.source), ("after", args.target)):
            args_out = subprocess.run(
                ["node", "_feet.mjs", "/tmp/pmcream", path, "--drift"],
                cwd=HERE, capture_output=True, text=True, check=True).stdout
            for line in args_out.splitlines():
                if "across the clip" in line:
                    print(f"  {label}: {line.strip()}")
        print("  a constant correction turns travel into a slide, so render the legs view too")


if __name__ == "__main__":
    sys.exit(main())
