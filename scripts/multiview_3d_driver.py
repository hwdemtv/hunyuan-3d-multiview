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
import glob
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request

_WORKBUDDY_SKILLS_ROOTS = [
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "WorkBuddy",
                 "resources", "app.asar.unpacked", "resources", "plugins",
                 "workbuddy-builtin", "skills"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."),
]
# Upstream renamed this file before (buddy-cloud.py ->
# buddy-multimodal-generation.py). Probe, never hardcode a single path.
_BUILTIN_CANDIDATES = [
    ("buddy-multimodal-generation", "buddy-multimodal-generation.py"),
    ("buddy-multimodal-generation", "buddy-cloud.py"),
    ("buddy-cloud", "buddy-cloud.py"),
    ("miora-image-generation", "buddy-cloud.py"),
    ("buddy-image-processing", "buddy-cloud.py"),
]


def _script_has_3d(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(400000)
    except OSError:
        return False
    return "_PROVIDER_MAP" in head and '"3d"' in head


def find_builtin_script():
    """Locate the WorkBuddy builtin multimodal script that signs the tcproxy call."""
    env = os.environ.get("BUDDY_CLOUD_SCRIPT")
    if env and os.path.exists(env):
        return env
    for root in _WORKBUDDY_SKILLS_ROOTS:
        for sub, name in _BUILTIN_CANDIDATES:
            p = os.path.join(root, sub, "scripts", name)
            if os.path.exists(p):
                return p
        hits = sorted(glob.glob(os.path.join(root, "*", "scripts", "*.py")))
        for p in hits:
            if _script_has_3d(p):
                return p
    return os.path.join(_WORKBUDDY_SKILLS_ROOTS[0], "buddy-multimodal-generation",
                        "scripts", "buddy-multimodal-generation.py")


BUDDY_CLOUD_SCRIPT = find_builtin_script()

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

def image_quality_hints(path):
    """Cheap heuristics: solid background and subject ratio (advisory only)."""
    hints = []
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            border = [im.getpixel((x, y))
                      for x in range(0, w, max(1, w // 20))
                      for y in (0, h - 1)]
            border += [im.getpixel((x, y))
                       for y in range(0, h, max(1, h // 20))
                       for x in (0, w - 1)]
            n = len(border)
            mean = [sum(c[i] for c in border) / n for i in range(3)]
            var = sum(sum((c[i] - mean[i]) ** 2 for i in range(3)) for c in border) / n
            std = var ** 0.5
            if std > 40:
                hints.append("background is not uniform; a plain background improves reconstruction")
            small = im.resize((64, 64)).load()
            subject = sum(1 for x in range(64) for y in range(64)
                          if sum((small[x, y][i] - mean[i]) ** 2 for i in range(3)) ** 0.5 > 45)
            ratio = subject / (64 * 64)
            if ratio < 0.5:
                hints.append(f"subject occupies ~{ratio:.0%} of the frame; aim for >50%")
            if ratio > 0.95:
                hints.append("subject may be cropped by the frame edge")
    except ImportError:
        pass
    return hints


def validate_job(job, base, input_checks=True):
    if job.get("state") == "downloaded" or (job.get("job_id") and job.get("state") == "DONE".lower()):
        return "already-submitted", None, []
    if job.get("job_id"):
        return "submitted", None, []
    if job.get("state") in ("submitting", "submission-uncertain"):
        return job["state"], None, []
    if not input_checks:
        return "ready", None, []

    if not job.get("id"):
        return "invalid-params", "Each job needs an id.", []
    model = job.get("model", "3.1")
    if model not in ("3.0", "3.1"):
        return "invalid-params", "model must be 3.0 or 3.1.", []
    gtype = job.get("generate_type", "Normal")
    if model == "3.1" and gtype in ("LowPoly", "Sketch"):
        return "invalid-params", f"{gtype} is unavailable on model 3.1 (use 3.0).", []
    if gtype == "Geometry" and job.get("pbr", True):
        return "invalid-params", "Geometry ignores PBR; set pbr=false to avoid confusion.", []
    fc = job.get("face_count", 500000)
    if not (isinstance(fc, int) and 3000 <= fc <= 1500000):
        return "invalid-params", "face_count must be an integer in 3000..1500000.", []
    views = job.get("views") or {}
    if not views.get("front"):
        return "invalid-params", "views.front is required (main image).", []
    for vt in VIEWS_31_ONLY:
        if views.get(vt) and model != "3.1":
            return "invalid-params", f"{vt} requires model 3.1.", []
    if len([v for v in views.values() if v]) > 8:
        return "invalid-params", "At most 8 view images.", []

    hints, total = [], 0
    for vt, rel in views.items():
        if not rel:
            continue
        p = resolve_path(rel, base)
        if not os.path.exists(p):
            return "waiting-for-image", f"{vt}: {rel} not found yet.", hints
        data = open(p, "rb").read()
        if sniff_mime(data) is None:
            return "invalid-image", f"{vt}: {rel} is not png/jpg/webp.", hints
        if len(data) > MAX_FILE_BYTES:
            return "invalid-image", f"{vt}: {rel} exceeds 6MB.", hints
        try:
            from PIL import Image
            with Image.open(p) as im:
                if max(im.size) < 128 or max(im.size) > 5000:
                    return "invalid-image", f"{vt}: resolution {im.size} outside 128..5000.", hints
        except ImportError:
            pass
        for h in image_quality_hints(p):
            hints.append(f"{vt}: {h}")
        total += len(base64.b64encode(data))
    if total > MAX_TOTAL_B64:
        return "invalid-image", "Total base64 of view images exceeds 6MB.", hints
    return "ready", None, hints


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
        state, err, hints = validate_job(job, base)
        entry = {"id": job.get("id"), "state": state, "error": err}
        if hints:
            entry["hints"] = hints
        report.append(entry)
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
            state, err, hints = validate_job(job, base)
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


def write_viewer(path, glb_name):
    """Fill the preview template.

    Plain str.replace, NOT str.format: the template carries a CSS block
    (`body { margin: 0; ... }`) whose braces would crash .format() with
    KeyError: 'margin'.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(VIEWER_TMPL.replace("{glb}", glb_name))


def reuse_downloads(job, output_dir):
    """Return already-downloaded artifacts for this job, or None.

    Keeps collect idempotent: a partially failed run (e.g. viewer write
    crashed) must not re-download 50 MB just to retry the last step.
    """
    saved = job.get("downloaded")
    if isinstance(saved, dict) and all(os.path.exists(p) for p in saved.values()):
        return saved
    pattern = os.path.join(output_dir, f"{job.get('id')}_*")
    hits = sorted(glob.glob(pattern + ".glb"), reverse=True)
    if not hits:
        return None
    stem = hits[0][:-4]
    found = {"glb": hits[0]}
    for suffix, key in (("_preview.png", "preview"), ("_viewer.html", "viewer")):
        p = stem + suffix
        if os.path.exists(p):
            found[key] = p
    # viewer written before the fix may be missing; regenerate it
    if "viewer" not in found:
        vp = stem + "_viewer.html"
        write_viewer(vp, os.path.basename(hits[0]))
        found["viewer"] = vp
    return found


def cmd_collect(args):
    data = read_jobs(args.jobs_file)
    os.makedirs(args.output_dir, exist_ok=True)
    api = {}

    def load_api():
        """Load the builtin signer lazily.

        Reusing artifacts that are already on disk must not require a token
        or even the builtin script to be present.
        """
        if not api:
            token = get_token()
            mod = load_module(BUDDY_CLOUD_SCRIPT)
            mod._ACTIVE_TOKEN = token
            api.update(token=token, mod=mod,
                       cfg=mod._PROVIDER_MAP["3d"],
                       endpoint=mod._DEFAULT_ENDPOINT)
        return api

    with JobLock(args.jobs_file):
        report = []
        deadline = time.time() + args.max_poll_time
        while True:
            pending = False
            for job in data["jobs"]:
                if not job.get("job_id") or job.get("state") == "downloaded":
                    # a successful run must not keep a stale error on record
                    if job.get("state") == "downloaded" and job.get("error_stage"):
                        job.pop("error_stage", None)
                        job.pop("error", None)
                        write_jobs(args.jobs_file, data)
                    continue
                # offline fast path: results already fetched, files already on disk
                if job.get("result_files") and args.download and not args.force_download:
                    reused = reuse_downloads(job, args.output_dir)
                    if reused:
                        job["downloaded"] = reused
                        job["state"] = "downloaded"
                        job.pop("error_stage", None)
                        job.pop("error", None)
                        write_jobs(args.jobs_file, data)
                        report.append({"id": job.get("id"), "state": "DONE",
                                       "saved": reused, "reused": True})
                        continue
                try:
                    a = load_api()
                    result = a["mod"]._call_api(a["endpoint"], a["cfg"]["provider"],
                                                a["cfg"]["service"], a["cfg"]["version"],
                                                a["cfg"]["query_action"],
                                                {"JobId": job["job_id"]}, a["token"])
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
                    job["error"] = sanitize(result.get("ErrorMessage", "Generation failed."),
                                            api.get("token", ""))
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
                        saved = reuse_downloads(job, args.output_dir)
                        if saved and not args.force_download:
                            entry["saved"] = saved
                            entry["reused"] = True
                        else:
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
                                write_viewer(vp, os.path.basename(saved["glb"]))
                                saved["viewer"] = vp
                        job["downloaded"] = saved
                        job["state"] = "downloaded"
                        job.pop("error_stage", None)
                        job.pop("error", None)
                        write_jobs(args.jobs_file, data)
                        entry["saved"] = saved
                    except Exception as e:  # download-only failure: never regenerate
                        job["error_stage"] = "download-error"
                        job["error"] = sanitize(f"{type(e).__name__}: {e}",
                                                api.get("token", ""))
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
        # Accepted (and effectively required for network subcommands) so the
        # documented recipes work verbatim. The token itself is never an argv
        # value -- it is read from stdin, because Windows caps argv at ~32k
        # chars and tokens must not land in shell history / process listings.
        sp.add_argument("--token-stdin", action="store_true",
                        help="read the temp token from stdin (always how it is read)")
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
    cl.add_argument("--force-download", action="store_true",
                    help="re-download even if artifacts already exist (safe: no new credits)")
    cl.set_defaults(func=cmd_collect)

    rn = common(sub.add_parser("run", help="check -> submit -> collect --download"))
    rn.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    rn.add_argument("--force-download", action="store_true")
    rn.add_argument("--poll-interval", type=int, default=10)
    rn.add_argument("--max-poll-time", type=int, default=600)
    rn.add_argument("--force-retry", action="store_true")
    rn.set_defaults(func=cmd_run)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
