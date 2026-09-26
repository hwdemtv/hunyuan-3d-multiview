# -*- coding: utf-8 -*-
"""
Hunyuan 3D driver: image-to-3D with multi-view inputs (ViewImageBase64).

Design follows the job-file state machine pattern, so a run can be resumed and
a paid generation is never submitted twice by accident.

Commands
    init     create/update the job file from view images (offline)
    check    offline validation: images, view types, params, state (no token)
    submit   submit pending jobs (token via stdin), records job ids
    collect  query status, download results, write a model-viewer page
    run      check -> submit -> collect --download (token via stdin)

Usage
    python multiview_3d_driver.py init --front f.jpg --back b.jpg --left l.jpg
    python multiview_3d_driver.py check .hy3d/jobs.json          # offline, no token
    echo -n "$TOKEN" | python multiview_3d_driver.py submit  .hy3d/jobs.json
    echo -n "$TOKEN" | python multiview_3d_driver.py collect .hy3d/jobs.json
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request

BUDDY_CLOUD_SCRIPT = os.environ.get(
    "BUDDY_CLOUD_SCRIPT",
    r"C:\Users\hwdem\AppData\Local\Programs\WorkBuddy\resources\app.asar.unpacked"
    r"\resources\plugins\workbuddy-builtin\skills\buddy-multimodal-generation"
    r"\scripts\buddy-cloud.py",
)

DEFAULT_JOBS_FILE = os.path.join(".hy3d", "jobs.json")
DEFAULT_OUTPUT_DIR = os.path.join(".hy3d", "outputs")
VIEWS_31_ONLY = ("top", "bottom", "left_front", "right_front")
MAX_FILE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_B64 = 6 * 1024 * 1024

VIEWER_TMPL = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>3D Model Viewer</title>
  <script type="module" src="https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"></script>
  <style>
    body { margin: 0; overflow: hidden; background: #f0f0f0; }
    model-viewer { width: 100vw; height: 100vh; }
  </style>
</head>
<body>
  <model-viewer src="{glb}" camera-controls auto-rotate shadow-intensity="1"></model-viewer>
</body>
</html>
"""

TOKEN = None  # set by `run` so stdin is consumed once


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_module(path):
    spec = importlib.util.spec_from_file_location("buddy_cloud", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level only; main() is not invoked
    return mod


def file_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def sniff_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitize(text, token=""):
    out = str(text)
    if token and len(token) >= 8:
        out = out.replace(token, "[redacted]")
    out = re.sub(r"data:[^\s\"']+", "[image redacted]", out)
    out = re.sub(r"https?://[^\s\"']+", "[URL redacted]", out)
    out = re.sub(r"[A-Za-z0-9+/_=-]{80,}", "[payload redacted]", out)
    return out[:400]


def get_token():
    global TOKEN
    if TOKEN:
        return TOKEN
    TOKEN = sys.stdin.readline().strip()
    if not TOKEN:
        raise SystemExit("Token required on stdin (connect_cloud_service -> tempToken).")
    return TOKEN


def resolve_path(rel, base):
    """Resolve a view image path: absolute as-is, else relative to cwd, else to the jobs file."""
    if os.path.isabs(rel):
        return rel
    if os.path.exists(rel):
        return os.path.abspath(rel)
    return os.path.join(base, rel)


def read_jobs(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_jobs(path, data):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


class JobLock:
    def __init__(self, path):
        self.path = path + ".lock"

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit(f"Another command holds {self.path}; never run overlapping commands.")
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# offline validation
# --------------------------------------------------------------------------

def validate_job(job, base, input_checks=True):
    if job.get("state") == "downloaded" or (job.get("job_id") and job.get("state") == "DONE".lower()):
        return "already-submitted", None
    if job.get("job_id"):
        return "submitted", None
    if job.get("state") in ("submitting", "submission-uncertain"):
        return job["state"], None
    if not input_checks:
        return "ready", None

    if not job.get("id"):
        return "invalid-params", "Each job needs an id."
    model = job.get("model", "3.1")
    if model not in ("3.0", "3.1"):
        return "invalid-params", "model must be 3.0 or 3.1."
    fc = job.get("face_count", 500000)
    if not (isinstance(fc, int) and 3000 <= fc <= 1500000):
        return "invalid-params", "face_count must be an integer in 3000..1500000."
    views = job.get("views") or {}
    if not views.get("front"):
        return "invalid-params", "views.front is required (main image)."
    for vt in VIEWS_31_ONLY:
        if views.get(vt) and model != "3.1":
            return "invalid-params", f"{vt} requires model 3.1."
    if len([v for v in views.values() if v]) > 8:
        return "invalid-params", "At most 8 view images."

    total = 0
    for vt, rel in views.items():
        if not rel:
            continue
        p = resolve_path(rel, base)
        if not os.path.exists(p):
            return "waiting-for-image", f"{vt}: {rel} not found yet."
        data = open(p, "rb").read()
        if sniff_mime(data) is None:
            return "invalid-image", f"{vt}: {rel} is not png/jpg/webp."
        if len(data) > MAX_FILE_BYTES:
            return "invalid-image", f"{vt}: {rel} exceeds 6MB."
        try:
            from PIL import Image
            with Image.open(p) as im:
                if max(im.size) < 128 or max(im.size) > 5000:
                    return "invalid-image", f"{vt}: resolution {im.size} outside 128..5000."
        except ImportError:
            pass
        total += len(base64.b64encode(data))
    if total > MAX_TOTAL_B64:
        return "invalid-image", "Total base64 of view images exceeds 6MB."
    return "ready", None


def build_body(job, base):
    views = job["views"]

    def abs_path(rel):
        return resolve_path(rel, base)

    body = {
        "ImageBase64": file_b64(abs_path(views["front"])),
        "Model": job.get("model", "3.1"),
        "EnablePBR": bool(job.get("pbr", True)),
        "FaceCount": job.get("face_count", 500000),
    }
    if job.get("generate_type") and job["generate_type"] != "Normal":
        body["GenerateType"] = job["generate_type"]
    multi = [{"ViewType": vt, "ViewImageBase64": file_b64(abs_path(rel))}
             for vt, rel in views.items() if rel and vt != "front"]
    if multi:
        body["MultiViewImages"] = multi
    return body


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args):
    views = {k: v for k, v in (("front", args.front), ("back", args.back),
                               ("left", args.left), ("right", args.right),
                               ("top", args.top), ("bottom", args.bottom),
                               ("left_front", args.left_front),
                               ("right_front", args.right_front))
             if v}
    if not views.get("front"):
        raise SystemExit("init requires --front")
    data = {"jobs": []}
    if os.path.exists(args.jobs_file):
        data = read_jobs(args.jobs_file)
    job = {
        "id": args.id or f"job-{int(time.time())}",
        "views": views,
        "model": args.model,
        "pbr": args.pbr,
        "face_count": args.face_count,
        "generate_type": args.generate_type,
        "state": "pending",
    }
    data["jobs"] = [j for j in data["jobs"] if j.get("id") != job["id"]] + [job]
    write_jobs(args.jobs_file, data)
    print(json.dumps({"created": job["id"], "jobs_file": args.jobs_file},
                     ensure_ascii=False, indent=2))


def cmd_check(args):
    data = read_jobs(args.jobs_file)
    base = os.path.dirname(os.path.abspath(args.jobs_file))
    report = []
    for job in data["jobs"]:
        state, err = validate_job(job, base)
        report.append({"id": job.get("id"), "state": state, "error": err})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(r["state"].startswith("invalid") for r in report):
        sys.exit(1)


def cmd_submit(args):
    token = get_token()
    data = read_jobs(args.jobs_file)
    base = os.path.dirname(os.path.abspath(args.jobs_file))
    mod = load_module(BUDDY_CLOUD_SCRIPT)
    mod._ACTIVE_TOKEN = token
    cfg = mod._PROVIDER_MAP["3d"]
    endpoint = mod._DEFAULT_ENDPOINT

    with JobLock(args.jobs_file):
        report = []
        for job in data["jobs"]:
            state, err = validate_job(job, base)
            if state == "submission-uncertain" and not args.force_retry:
                report.append({"id": job.get("id"), "state": state,
                               "error": "Submission outcome unknown. Recover with collect, "
                                        "or pass --force-retry if you accept a second charge."})
                continue
            if state != "ready":
                report.append({"id": job.get("id"), "state": state, "error": err})
                continue
            job["state"] = "submitting"
            job["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            for k in ("error", "error_stage"):
                job.pop(k, None)
            write_jobs(args.jobs_file, data)
            try:
                resp = mod._call_api(endpoint, cfg["provider"], cfg["service"],
                                     cfg["version"], cfg["submit_action"],
                                     build_body(job, base), token)
            except SystemExit:
                job["state"] = "submission-uncertain"
                job["error_stage"] = "rejected"
                job["error"] = ("Submission rejected before a job id was returned. "
                                "Fix the reported cause, then submit again.")
                write_jobs(args.jobs_file, data)
                report.append({"id": job.get("id"), "state": job["state"],
                               "error": job["error"]})
                continue
            job_id = resp.get("JobId")
            if not job_id:
                job["state"] = "submission-uncertain"
                job["error_stage"] = "no-job-id"
                write_jobs(args.jobs_file, data)
                report.append({"id": job.get("id"), "state": job["state"],
                               "error": "No JobId in response; verify before re-submitting."})
                continue
            job["job_id"] = job_id
            job["state"] = "submitted"
            job["request_id"] = resp.get("RequestId", "")
            write_jobs(args.jobs_file, data)
            report.append({"id": job.get("id"), "state": "submitted", "job_id": job_id})
    print(json.dumps(report, ensure_ascii=False, indent=2))


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        f.write(r.read())
    return os.path.getsize(dest)


def cmd_collect(args):
    token = get_token()
    data = read_jobs(args.jobs_file)
    mod = load_module(BUDDY_CLOUD_SCRIPT)
    mod._ACTIVE_TOKEN = token
    cfg = mod._PROVIDER_MAP["3d"]
    endpoint = mod._DEFAULT_ENDPOINT
    os.makedirs(args.output_dir, exist_ok=True)

    with JobLock(args.jobs_file):
        report = []
        deadline = time.time() + args.max_poll_time
        while True:
            pending = False
            for job in data["jobs"]:
                if not job.get("job_id") or job.get("state") == "downloaded":
                    continue
                try:
                    result = mod._call_api(endpoint, cfg["provider"], cfg["service"],
                                           cfg["version"], cfg["query_action"],
                                           {"JobId": job["job_id"]}, token)
                except SystemExit:
                    job["error_stage"] = "query-error"
                    job["error"] = "Status query failed; keep job_id and retry collect."
                    write_jobs(args.jobs_file, data)
                    report.append({"id": job.get("id"), "state": "query-error"})
                    continue
                status = result.get("Status", "")
                job["state"] = (status or "unknown").lower()
                job["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                if status == "FAIL":
                    job["error_stage"] = "result-error"
                    job["error"] = sanitize(result.get("ErrorMessage", "Generation failed."), token)
                    write_jobs(args.jobs_file, data)
                    report.append({"id": job.get("id"), "state": "FAIL",
                                   "error_stage": "result-error", "error": job["error"]})
                    continue
                if status != "DONE":
                    pending = True
                    continue
                files = [{"type": (f.get("Type") or "").lower(), "url": f.get("Url"),
                          "preview_image_url": f.get("PreviewImageUrl")}
                         for f in result.get("ResultFile3Ds", [])]
                if not files:
                    job["error_stage"] = "result-error"
                    job["error"] = "DONE but no result files; retry collect with same job id."
                    write_jobs(args.jobs_file, data)
                    report.append({"id": job.get("id"), "state": "DONE",
                                   "error_stage": "result-error", "error": job["error"]})
                    continue
                job["result_files"] = files
                job["credit_consumed"] = result.get("ResultCreditConsumed")
                write_jobs(args.jobs_file, data)
                entry = {"id": job.get("id"), "state": "DONE", "files": files}
                if args.download:
                    try:
                        stamp = time.strftime("%Y%m%d_%H%M%S")
                        saved = {}
                        glb = next((f for f in files if f["type"] == "glb"), None)
                        prev = next((f for f in files if f.get("preview_image_url")), None)
                        if glb:
                            p = os.path.join(args.output_dir, f"{job['id']}_{stamp}.glb")
                            download(glb["url"], p)
                            saved["glb"] = p
                        if prev:
                            p = os.path.join(args.output_dir, f"{job['id']}_{stamp}_preview.png")
                            download(prev["preview_image_url"], p)
                            saved["preview"] = p
                        if saved.get("glb"):
                            vp = os.path.join(args.output_dir, f"{job['id']}_{stamp}_viewer.html")
                            with open(vp, "w", encoding="utf-8") as f:
                                f.write(VIEWER_TMPL.format(glb=os.path.basename(saved["glb"])))
                            saved["viewer"] = vp
                        job["downloaded"] = saved
                        job["state"] = "downloaded"
                        write_jobs(args.jobs_file, data)
                        entry["saved"] = saved
                    except Exception as e:  # download-only failure: never regenerate
                        job["error_stage"] = "download-error"
                        job["error"] = sanitize(e, token)
                        write_jobs(args.jobs_file, data)
                        entry["error_stage"] = "download-error"
                        entry["error"] = job["error"]
                report.append(entry)
            if not pending or args.no_poll or time.time() > deadline:
                break
            print(f"[INFO] still running, next check in {args.poll_interval}s ...", file=sys.stderr)
            time.sleep(args.poll_interval)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_run(args):
    get_token()
    cmd_check(args)
    cmd_submit(args)
    cmd_collect(args)


def build_parser():
    p = argparse.ArgumentParser(
        description="Hunyuan multi-view image-to-3D driver (job-file state machine).",
        epilog="Recipes:\n"
               "  1) single image:  init --front a.jpg --no-pbr\n"
               "  2) three views:   init --front f.jpg --back b.jpg --left l.jpg\n"
               "  3) white model:   init --front f.jpg --generate-type Geometry\n"
               "  4) resume:        check .hy3d/jobs.json   (offline, no token)\n")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--jobs-file", default=DEFAULT_JOBS_FILE)
        return sp

    ini = common(sub.add_parser("init", help="create/update the job file from view images"))
    ini.add_argument("--front"); ini.add_argument("--back")
    ini.add_argument("--left"); ini.add_argument("--right")
    ini.add_argument("--top"); ini.add_argument("--bottom")
    ini.add_argument("--left-front", dest="left_front")
    ini.add_argument("--right-front", dest="right_front")
    ini.add_argument("--id")
    ini.add_argument("--model", default="3.1", choices=["3.0", "3.1"])
    ini.add_argument("--pbr", action="store_true", default=True)
    ini.add_argument("--no-pbr", dest="pbr", action="store_false")
    ini.add_argument("--face-count", type=int, default=500000)
    ini.add_argument("--generate-type", default="Normal",
                     choices=["Normal", "LowPoly", "Geometry", "Sketch"])
    ini.set_defaults(func=cmd_init)

    chk = common(sub.add_parser("check", help="offline validation, no token needed"))
    chk.set_defaults(func=cmd_check)

    sb = common(sub.add_parser("submit", help="submit pending jobs"))
    sb.add_argument("--force-retry", action="store_true",
                    help="re-submit submission-uncertain jobs (may cost credits twice)")
    sb.set_defaults(func=cmd_submit)

    cl = common(sub.add_parser("collect", help="query status / download results"))
    cl.add_argument("--download", action="store_true", default=True)
    cl.add_argument("--no-download", dest="download", action="store_false")
    cl.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    cl.add_argument("--poll-interval", type=int, default=10)
    cl.add_argument("--max-poll-time", type=int, default=600)
    cl.add_argument("--no-poll", action="store_true")
    cl.set_defaults(func=cmd_collect)

    rn = common(sub.add_parser("run", help="check -> submit -> collect --download"))
    rn.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    rn.add_argument("--poll-interval", type=int, default=10)
    rn.add_argument("--max-poll-time", type=int, default=600)
    rn.add_argument("--force-retry", action="store_true")
    rn.set_defaults(func=cmd_run)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
