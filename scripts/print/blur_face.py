# -*- coding: utf-8 -*-
"""Blur a rectangular region of a photo (face masking before uploading to cloud 3D gen).

Usage:
    python blur_face.py <image_in> <image_out> <x1,y1,x2,y2> [radius]
If the box is omitted, a rough center-upper-area default is applied (portrait photos).
Determine the exact face box by viewing the image first (Read tool renders images).
"""
import argparse
from PIL import Image, ImageFilter

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("dst")
ap.add_argument("box", nargs="?", default=None, help="x1,y1,x2,y2 in pixels")
ap.add_argument("--radius", type=int, default=18)
args = ap.parse_args()

img = Image.open(args.src).convert("RGB")
w, h = img.size
if args.box:
    box = tuple(int(v) for v in args.box.split(","))
else:
    # rough default: central-upper region (covers face in typical standing portrait)
    box = (int(w * 0.38), int(h * 0.12), int(w * 0.62), int(h * 0.30))

region = img.crop(box).filter(ImageFilter.GaussianBlur(args.radius))
img.paste(region, box)
img.save(args.dst, quality=92)
print("blurred", box, "->", args.dst)
