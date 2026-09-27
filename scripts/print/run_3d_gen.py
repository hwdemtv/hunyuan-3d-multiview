# -*- coding: utf-8 -*-
"""Image-to-3D generation via buddy-cloud.py, bypassing Windows command-line length limits.

Passes the image as --image-base64, which can exceed the ~32KB Windows argv limit.
This wrapper loads buddy-cloud.py via importlib and injects sys.argv programmatically.

Usage:
    python run_3d_gen.py <image_path> [--enable-pbr] [--prompt "..."]
    Token is read from stdin (never pass on command line).

Output: JSON on stdout with job_id / status / ResultFile3Ds (signed URLs).
"""
import base64
import importlib.util
import sys

BUDDY_CLOUD = (r"C:\Users\hwdem\AppData\Local\Programs\WorkBuddy\resources"
               r"\app.asar.unpacked\resources\plugins\workbuddy-builtin"
               r"\skills\buddy-multimodal-generation\scripts\buddy-cloud.py")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        print("usage: run_3d_gen.py <image_path> [--enable-pbr] [--prompt TXT]", file=sys.stderr)
        sys.exit(2)
    image_path = args[0]
    extra = args[1:]
    if "--enable-pbr" not in extra:
        extra.append("--enable-pbr")

    spec = importlib.util.spec_from_file_location("buddy_cloud", BUDDY_CLOUD)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)  # __name__ != "__main__", main() will not auto-run

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    argv = ["buddy-cloud.py", "3d", "--image-base64", b64]
    if extra:
        argv += extra
    argv.append("--token-stdin")
    sys.argv = argv
    bc.main()


if __name__ == "__main__":
    main()
