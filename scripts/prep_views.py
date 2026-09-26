#!/usr/bin/env python3
"""Normalize view images before submitting them to Hunyuan 3D.

Validated by an A/B run (same character, both 3.1 + PBR, 40 credits each):
re-cropped inputs (subject bbox + white square canvas, subject spanning 70%)
beat naive third-of-the-width crops. Note the winning model had *fewer*
triangles -- the gain is reconstruction accuracy, not mesh density, so do
not compensate by raising --face-count.

Usage:
    python scripts/prep_views.py front.jpg back.jpg left.jpg --out-dir .hy3d/inputs
    python scripts/prep_views.py --front f.jpg --back b.jpg --left l.jpg --ratio 0.7
"""
import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

BG_CUTOFF = 235      # min(R,G,B) >= this is treated as background
TARGET_RATIO = 0.70  # subject's longer side as a fraction of the canvas
MIN_SIDE = 512
MAX_SIDE = 1024
PROBE = 64           # bbox is measured on this downsample


def subject_bbox(im):
    w, h = im.size
    px = im.resize((PROBE, PROBE), Image.BILINEAR).load()
    xs, ys = [], []
    for x in range(PROBE):
        for y in range(PROBE):
            if min(px[x, y]) < BG_CUTOFF:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0, 0, w, h
    return (min(xs) * w // PROBE, min(ys) * h // PROBE,
            min((max(xs) + 1) * w // PROBE, w), min((max(ys) + 1) * h // PROBE, h))


def normalize(src, dst, ratio=TARGET_RATIO):
    im = Image.open(src).convert("RGB")
    subj = im.crop(subject_bbox(im))
    sw, sh = subj.size
    side = min(max(int(max(sw, sh) / ratio), MIN_SIDE), MAX_SIDE)
    canvas = Image.new("RGB", (side, side), "white")
    scale = min(side * ratio / sw, side * ratio / sh)
    subj = subj.resize((max(int(sw * scale), 1), max(int(sh * scale), 1)), Image.LANCZOS)
    canvas.paste(subj, ((side - subj.width) // 2, (side - subj.height) // 2))
    canvas.save(dst, quality=92)
    return {"src": src, "dst": dst, "canvas": side,
            "subject": (subj.width, subj.height),
            "origin": im.size, "bytes": os.path.getsize(dst)}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("images", nargs="*", help="view images (name is kept)")
    p.add_argument("--front"); p.add_argument("--back")
    p.add_argument("--left"); p.add_argument("--right")
    p.add_argument("--top"); p.add_argument("--bottom")
    p.add_argument("--out-dir", default=os.path.join(".hy3d", "inputs"))
    p.add_argument("--ratio", type=float, default=TARGET_RATIO,
                   help="subject's longer side / canvas side (default 0.70)")
    p.add_argument("--bg-cutoff", type=int, default=BG_CUTOFF)
    a = p.parse_args()

    named = [("--front", a.front), ("--back", a.back), ("--left", a.left),
             ("--right", a.right), ("--top", a.top), ("--bottom", a.bottom)]
    jobs = []
    for flag, path in named:
        if path:
            jobs.append((path, os.path.join(a.out_dir, flag.lstrip("-") + ".jpg")))
    for path in a.images:
        jobs.append((path, os.path.join(a.out_dir,
                                        os.path.splitext(os.path.basename(path))[0] + ".jpg")))
    if not jobs:
        p.error("no input images given")

    globals()["BG_CUTOFF"] = a.bg_cutoff
    os.makedirs(a.out_dir, exist_ok=True)
    for src, dst in jobs:
        if not os.path.exists(src):
            print(f"[WARN] missing: {src}", file=sys.stderr)
            continue
        r = normalize(src, dst, a.ratio)
        print(f"{r['dst']}  canvas {r['canvas']}px  subject {r['subject'][0]}x{r['subject'][1]} "
              f"(from {r['origin'][0]}x{r['origin'][1]})  {r['bytes'] // 1024} KB")
    print(f"\nnext: python scripts/multiview_3d_driver.py init --front "
          f"{os.path.join(a.out_dir, 'front.jpg')} ...")


if __name__ == "__main__":
    main()
