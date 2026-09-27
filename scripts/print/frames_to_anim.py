# -*- coding: utf-8 -*-
"""Compose turntable PNG frames into a WeChat-article-friendly GIF + WebP.

Usage:
    python frames_to_anim.py <frames_dir> [--prefix turntable_] [--step 2]
                             [--width 400] [--gif-out X.gif] [--webp-out X.webp]

Defaults tuned for WeChat公众号: every 2nd frame, 400px wide, GIF <= ~1MB.
"""
import argparse
import glob
import os
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("frames_dir")
ap.add_argument("--prefix", default="turntable_")
ap.add_argument("--step", type=int, default=2)
ap.add_argument("--width", type=int, default=400)
ap.add_argument("--gif-out", default=None)
ap.add_argument("--webp-out", default=None)
ap.add_argument("--colors", type=int, default=128)
args = ap.parse_args()

frames = sorted(glob.glob(os.path.join(args.frames_dir, args.prefix + "*.png")))[::args.step]
if not frames:
    raise SystemExit("no frames matched: " + args.prefix + "*.png")
print("frames:", len(frames))

first = Image.open(frames[0]).convert("RGB")
ratio = args.width / first.width
size = (args.width, round(first.height * ratio))
imgs = [Image.open(f).convert("RGB").resize(size, Image.LANCZOS) for f in frames]

base = frames[0]
gif_out = args.gif_out or os.path.join(args.frames_dir, "turntable.gif")
webp_out = args.webp_out or os.path.join(args.frames_dir, "turntable.webp")

# GIF: quantize per frame to keep size low
qs = [im.quantize(colors=args.colors, method=Image.MEDIANCUT,
                  dither=Image.FLOYDSTEINBERG) for im in imgs]
qs[0].save(gif_out, save_all=True, append_images=qs[1:], duration=66, loop=0, optimize=True)

# WebP: much smaller, same animation
imgs[0].save(webp_out, save_all=True, append_images=imgs[1:],
             duration=66, loop=0, quality=78, method=6)

print("gif :", gif_out, os.path.getsize(gif_out) // 1024, "KB")
print("webp:", webp_out, os.path.getsize(webp_out) // 1024, "KB")
