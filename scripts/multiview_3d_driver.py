# -*- coding: utf-8 -*-
"""
Hunyuan 3D driver: image-to-3D with multi-view support via ViewImageBase64.

Bypasses the limitation of the builtin buddy-cloud.py (which only supports
ViewImageUrl in --multi-view). Loads buddy-cloud.py as a module and calls its
signing/polling functions directly, so no public image hosting is needed.

Usage:
    echo -n "<token>" | python multiview_3d_driver.py \
        --front view_front.jpg [--back view_back.jpg] [--left view_left.jpg] \
        [--right view_right.jpg] [--model 3.1] [--pbr] [--face-count 500000]

Token comes from stdin (never pass it as an argument or env var).
Output: JSON with job_id / status / result_files on stdout, progress on stderr.
"""
import argparse
import base64
import importlib.util
import json
import os
import sys

# Builtin skill script shipped with WorkBuddy Desktop.
BUDDY_CLOUD_SCRIPT = os.environ.get(
    "BUDDY_CLOUD_SCRIPT",
    r"C:\Users\hwdem\AppData\Local\Programs\WorkBuddy\resources\app.asar.unpacked"
    r"\resources\plugins\workbuddy-builtin\skills\buddy-multimodal-generation"
    r"\scripts\buddy-cloud.py",
)


def load_module(path):
    spec = importlib.util.spec_from_file_location("buddy_cloud", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level only; main() not invoked
    return mod


def file_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True, help="Front view image (main image)")
    ap.add_argument("--back"); ap.add_argument("--left"); ap.add_argument("--right")
    ap.add_argument("--model", default="3.1", choices=["3.0", "3.1"])
    ap.add_argument("--pbr", action="store_true", help="Enable PBR materials")
    ap.add_argument("--face-count", type=int, default=500000)
    ap.add_argument("--no-poll", action="store_true")
    args = ap.parse_args()

    token = sys.stdin.readline().strip()
    if not token:
        print(json.dumps({"error": "TOKEN_MISSING"}))
        sys.exit(1)

    mod = load_module(BUDDY_CLOUD_SCRIPT)
    mod._ACTIVE_TOKEN = token  # enable token redaction in output

    body = {
        "ImageBase64": file_b64(args.front),
        "Model": args.model,
        "EnablePBR": bool(args.pbr),
        "FaceCount": args.face_count,
    }
    multi = [
        {"ViewType": vt, "ViewImageBase64": file_b64(path)}
        for vt, path in (("back", args.back), ("left", args.left), ("right", args.right))
        if path
    ]
    if multi:
        body["MultiViewImages"] = multi

    cfg = mod._PROVIDER_MAP["3d"]
    endpoint = mod._DEFAULT_ENDPOINT
    print(f"[INFO] endpoint={endpoint} submit={cfg['submit_action']}", file=sys.stderr)

    submit = mod._call_api(endpoint, cfg["provider"], cfg["service"], cfg["version"],
                           cfg["submit_action"], body, token)
    job_id = submit.get("JobId")
    if not job_id:
        print(json.dumps({"error": "NO_JOB_ID", "raw": submit}, ensure_ascii=False))
        sys.exit(1)
    print(f"[INFO] Job submitted: {job_id}", file=sys.stderr)

    if args.no_poll:
        print(json.dumps({"job_id": job_id, "status": "SUBMITTED"}, ensure_ascii=False))
        return

    result = mod._poll_job(endpoint, cfg["provider"], cfg["service"], cfg["version"],
                           cfg["query_action"], job_id, token, 5, 600)
    files = [
        {"type": f.get("Type", "").lower(),
         "url": f.get("Url"),
         "preview_image_url": f.get("PreviewImageUrl")}
        for f in result.get("ResultFile3Ds", [])
    ]
    print(json.dumps({
        "job_id": job_id,
        "status": result.get("Status"),
        "credit_consumed": result.get("ResultCreditConsumed"),
        "result_files": files,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
