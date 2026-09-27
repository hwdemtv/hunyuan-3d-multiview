"""
print_gate.py — 「图生 3D → 打印」链路末端的体检 + 自动修复闭环闸门。

流程（--autofix 时）：
    体检 → 有结构缺陷 → L1 轻修复（焊接/补洞/法线，保细节）→ 复检
         → 仍有缺陷 → L2 体素重网格（保水密，毁细节）→ 复检
         → 仍有缺陷 → FAIL，隔离 failed/ 目录人工处理

不带 --autofix 时退化为纯体检闸门（只查不修）。

用法：
    python print_gate.py --workspace <授权目录> --stl figurine_print.stl
    python print_gate.py --workspace <授权目录> --glb model.glb --height 180 --autofix

注意：跑 stl-mesh-preflight 必须用 Python 3.11 —— Python 3.13 在 Windows 上
stat 不一致，check.py 的 TOCTOU 校验会报 INPUT_CHANGED。
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PREFLIGHT = os.path.normpath(
    os.path.join(HERE, "..", "..", "vendor", "stl-mesh-preflight", "scripts", "check.py"))
PROBE_SCRIPT = os.path.join(HERE, "probe_decimate.py")
REPAIR_SCRIPT = os.path.join(HERE, "repair_for_print.py")
AUTOFIX_SCRIPT = os.path.join(HERE, "autofix_stl.py")

DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

MAX_FACES = 30000          # stl-mesh-preflight 硬限
MAX_BYTES = 16 * 1024 * 1024

ISSUE_KEYS = ("boundary_edges", "over_two_face_edges",
              "same_direction_two_face_edges", "numerical_zero_area_faces")


BLENDER_TIMEOUT = 600     # 单次 Blender 调用上限(秒)，防止体素重网格卡死整条流水线


def log(*a):
    print("[gate]", *a, flush=True)


def run(cmd, timeout=BLENDER_TIMEOUT, **kw):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT after %ss: %s" % (timeout, " ".join(cmd[:6]))
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def stl_stats(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(84)
    if len(head) == 84:
        n = struct.unpack_from("<I", head, 80)[0]
        if size == 84 + 50 * n:
            return "binary", n, size
    try:
        with open(path, "r", encoding="ascii") as f:
            head_txt = f.read(4096)
    except (UnicodeDecodeError, OSError):
        return "unknown", None, size
    if not head_txt.lstrip().lower().startswith("solid"):
        return "unknown", None, size
    with open(path, "r", encoding="ascii", errors="replace") as f:
        full = f.read()
    return "ascii", full.count("facet normal"), size


def unique_dir(root, base):
    name, i = base, 1
    while os.path.exists(os.path.join(root, name)):
        i += 1
        name = "%s-%d" % (base, i)
    return name


def unique_name(root, base):
    stem, ext = os.path.splitext(base)
    name, i = base, 1
    while os.path.exists(os.path.join(root, name)):
        i += 1
        name = "%s-%d%s" % (stem, i, ext)
    return name


def ensure_in_ws(ws, path):
    """原件必须在 workspace 内（预检沙箱要求），外部文件复制进来。"""
    path = os.path.abspath(path)
    if os.path.dirname(path) == ws:
        return os.path.basename(path), path
    dst = os.path.join(ws, unique_name(ws, os.path.basename(path)))
    shutil.copy2(path, dst)
    return os.path.basename(dst), dst


def build_print_stl(blender, glb, out_dir, height, no_voxel):
    os.makedirs(out_dir, exist_ok=True)
    cmd = [blender, "--background", "--python", REPAIR_SCRIPT, "--",
           "--mode", "print", "--glb", glb, "--out", out_dir, "--height", str(height)]
    if no_voxel:
        cmd.append("--no-voxel")
    code, out, err = run(cmd)
    if code != 0:
        raise SystemExit("repair_for_print.py 失败 (exit %d)\n%s\n%s" % (code, out, err))
    p = os.path.join(out_dir, "figurine_print.stl")
    if not os.path.exists(p):
        raise SystemExit("未产出 figurine_print.stl，检查 Blender 输出：\n" + out[-2000:])
    return p


def make_probe(blender, src, dst, target):
    code, out, err = run([blender, "--background", "--python", PROBE_SCRIPT, "--",
                          src, dst, str(target)])
    if code != 0 or not os.path.exists(dst):
        raise SystemExit("抽面失败 (exit %d)\n%s\n%s" % (code, out, err))
    return dst


def autofix(blender, src, dst, level, voxel_mm=None):
    cmd = [blender, "--background", "--python", AUTOFIX_SCRIPT, "--", src, dst, level]
    if level == "voxel" and voxel_mm:
        cmd.append(str(voxel_mm))
    code, out, err = run(cmd)
    if code != 0 or not os.path.exists(dst):
        return {"error": (err or out)[-800:]}
    info = {}
    for line in out.splitlines():
        for key in ("AUTOFIX_FACES", "AUTOFIX_OPEN_EDGES", "AUTOFIX_NONMANIFOLD_EDGES"):
            if line.startswith(key):
                info[key.replace("AUTOFIX_", "").lower()] = int(line.split()[1])
    return info


def preflight_check(python, ws, rel_input):
    out_base = "preflight-report"
    stats, state = None, None
    for attempt in (1, 2):          # 偶发 INPUT_CHANGED（刚写完的文件 stat 抖动）重试一次
        # 注意：check.py 连 --check 干跑都要求 --out 目录不存在，必须用独占名
        dry_out = unique_dir(ws, out_base + "-dryrun")
        code, out, err = run([python, PREFLIGHT, "--workspace", ws,
                              "--input", rel_input, "--out", dry_out, "--check"])
        if code != 0:
            state = {"state": "FAILED", "error": (out or err)[-500:]}
            log("预检干跑失败(第%d次)：%s" % (attempt, state["error"]))
            time.sleep(1.5)
            continue
        stats = json.loads(out).get("stats", {})
        out_dir = unique_dir(ws, out_base)
        code, out2, err2 = run([python, PREFLIGHT, "--workspace", ws,
                                "--input", rel_input, "--out", out_dir])
        if code == 0:
            return stats, {"state": "REPORT_COMPLETE", "report_dir": out_dir}
        state = {"state": "FAILED", "error": (out2 or err2)[-500:]}
        log("预检出报告失败(第%d次)：%s" % (attempt, state["error"]))
        time.sleep(1.5)
    return stats, state


def inspect(python, blender, ws, abs_stl, max_probe):
    """体检一个 STL；超预检上限就先抽面产副本。返回 (stats, state, meta)。"""
    rel, abs_path = ensure_in_ws(ws, abs_stl)
    fmt, faces, size = stl_stats(abs_path)
    need_probe = (faces is None) or (faces > MAX_FACES) or (size > MAX_BYTES)
    probed = False
    if need_probe:
        rel_probe = unique_name(ws, "probe.stl")
        make_probe(blender, abs_path, os.path.join(ws, rel_probe),
                   min(max_probe, MAX_FACES))
        _, faces, size = stl_stats(os.path.join(ws, rel_probe))
        rel = rel_probe
        probed = True
        log("超预检上限 → 已抽面产副本：%s 面" % faces)
    stats, state = preflight_check(python, ws, rel)
    meta = {"file": rel, "format": fmt, "faces": faces, "bytes": size,
            "probed_copy": probed, "preflight": state}
    return stats, state, meta


def findings_of(stats):
    v = []
    if not stats:
        return ["体检未执行，见 preflight 字段"]
    if stats.get("boundary_edges", 0) > 0:
        v.append("有破洞：%d 条边界边 → 切片会漏层/乱飞丝" % stats["boundary_edges"])
    if stats.get("over_two_face_edges", 0) > 0:
        v.append("非流形：%d 条边被 >2 个面共用 → 体积歧义" % stats["over_two_face_edges"])
    if stats.get("same_direction_two_face_edges", 0) > 0:
        v.append("绕向冲突：%d 条两面同向边 → 内外判定错误" % stats["same_direction_two_face_edges"])
    if stats.get("numerical_zero_area_faces", 0) > 0:
        v.append("退化面：%d 个数值零面积面 → 应清掉" % stats["numerical_zero_area_faces"])
    if stats.get("duplicate_face_groups", 0) > 0:
        v.append("重复面：%d 组（多余 %d 个）" % (
            stats["duplicate_face_groups"], stats.get("duplicate_extra_faces", 0)))
    if stats.get("opposed_stored_normals", 0) > 0:
        v.append("法向量反向：%d 个面与顶点绕向不符" % stats["opposed_stored_normals"])
    if not v:
        v.append("预检项全部为 0 —— 但仍需在切片软件复核自交、壁厚、单位与实际摆放")
    return v


def has_issues(stats):
    return bool(stats) and any(stats.get(k, 0) > 0 for k in ISSUE_KEYS)


def write_report(ws, out_name, report):
    out_dir = os.path.join(ws, out_name)
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "gate-report.json")
    mp = os.path.join(out_dir, "gate-report.md")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    s0 = report["initial"]
    lines = [
        "# 打印前闸门报告",
        "",
        "- 原件：`%s`（%s，%s 面，%.1f MB）" % (
            s0["file"], s0["format"], s0["faces"], (s0["bytes"] or 0) / 1048576.0),
        "- 自动修复：%s" % ("开启" if report.get("autofix") else "关闭（纯体检）"),
        "- 最终判定：**%s**" % report["status"],
        "- 最终文件：`%s`" % report.get("final_file", "无"),
        "",
        "## 各级结果",
        "",
        "| 级别 | 文件 | 面数 | 边界边 | >2面边 | 同向边 | 零面积面 | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lv in report["levels"]:
        st = lv.get("stats") or {}
        lines.append("| %s | `%s` | %s | %s | %s | %s | %s | %s |" % (
            lv["level"], lv.get("file", "-"), lv.get("faces", "-"),
            st.get("boundary_edges", "-"), st.get("over_two_face_edges", "-"),
            st.get("same_direction_two_face_edges", "-"),
            st.get("numerical_zero_area_faces", "-"), lv.get("verdict", "-")))
    lines += ["", "## 结论", ""]
    lines += ["- " + s for s in report["findings"]]
    if report.get("caveat"):
        lines += ["", "> " + report["caveat"]]
    lines += ["", "## 本报告不覆盖", "",
              "- 自交、顶点流形、壁厚、支撑、单位推断、材料与受力（预检工具与体检副本的固有盲区）"]
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return jp, mp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--glb", help="混元产物 GLB，给了就先跑 print 模式导出 STL")
    ap.add_argument("--stl", help="已有 STL，跳过导出")
    ap.add_argument("--height", type=float, default=180.0)
    ap.add_argument("--no-voxel", action="store_true", help="GLB 阶段跳过体素重网格")
    ap.add_argument("--autofix", action="store_true", help="发现问题自动修复并复检")
    ap.add_argument("--start-level", choices=["light", "voxel"], default="light",
                    help="强制从某级开始修（测试/人工干预用）")
    ap.add_argument("--voxel-mm", type=float, default=None,
                    help="L2 体素重网格的体素尺寸(mm)，默认包围盒/150")
    ap.add_argument("--max-probe-faces", type=int, default=25000)
    ap.add_argument("--blender", default=DEFAULT_BLENDER)
    ap.add_argument("--python", default=sys.executable, help="跑预检的解释器，必须 3.11")
    ap.add_argument("--out", default="gate-out")
    a = ap.parse_args()

    if sys.version_info >= (3, 13):
        log("警告：当前解释器 %d.%d 跑 stl-mesh-preflight 会报 INPUT_CHANGED，"
            "请用 --python 指向 Python 3.11" % sys.version_info[:2])
    if not os.path.exists(PREFLIGHT):
        raise SystemExit("找不到 stl-mesh-preflight：%s" % PREFLIGHT)
    ws = os.path.abspath(a.workspace)
    if not os.path.isdir(ws):
        raise SystemExit("workspace 不是目录：" + ws)

    # 1) 拿到打印 STL
    if a.glb:
        stl = build_print_stl(a.blender, os.path.abspath(a.glb), ws, a.height, a.no_voxel)
        log("已导出", stl)
    elif a.stl:
        stl = os.path.abspath(a.stl)
        if not os.path.exists(stl):
            raise SystemExit("STL 不存在：" + stl)
    else:
        raise SystemExit("必须给 --glb 或 --stl")
    stl = os.path.join(ws, ensure_in_ws(ws, stl)[0])

    # 2) 初始体检
    stats0, state0, meta0 = inspect(a.python, a.blender, ws, stl, a.max_probe_faces)
    log("初始体检：", state0.get("state"))
    checkable = state0.get("state") == "REPORT_COMPLETE" and stats0 is not None
    levels = [{"level": "L0 体检", **meta0, "stats": stats0,
               "verdict": ("PASS" if checkable and not has_issues(stats0)
                           else ("体检失败" if not checkable else "FAIL"))}]

    report = {
        "autofix": a.autofix,
        "initial": meta0,
        "levels": levels,
        "status": "UNKNOWN",
        "final_file": None,
        "findings": [],
        "caveat": None,
    }

    # 3) 闭环：修 → 检 → 升级 → 再检
    if not checkable:
        # 体检本身失败：没有证据就不放行，也不盲修
        report["status"] = "NEEDS_REVIEW"
        report["caveat"] = ("预检未能完成（见 levels 里 preflight.error），"
                            "没有体检证据就不放行；先解决预检环境再重跑")
        report["findings"] = findings_of(None)
    elif not a.autofix:
        report["status"] = "PASS" if not has_issues(stats0) else "NEEDS_REVIEW"
        report["findings"] = findings_of(stats0)
    elif not has_issues(stats0):
        report["status"] = "PASS"
        report["final_file"] = os.path.basename(stl)
        report["findings"] = findings_of(stats0)
    else:
        cur_abs = stl
        plan = ["light", "voxel"] if a.start_level == "light" else ["voxel"]
        for i, level in enumerate(plan, 1):
            tag = "L%d(%s)" % (i, level)
            fixed_abs = os.path.join(ws, unique_name(
                ws, os.path.splitext(os.path.basename(cur_abs))[0] + "_fixed.stl"))
            info = autofix(a.blender, cur_abs, fixed_abs, level, a.voxel_mm)
            if "error" in info:
                levels.append({"level": tag, "verdict": "修复执行失败",
                               "error": info["error"]})
                log(tag, "修复执行失败")
                break
            stats_i, state_i, meta_i = inspect(
                a.python, a.blender, ws, fixed_abs, a.max_probe_faces)
            checkable_i = state_i.get("state") == "REPORT_COMPLETE" and stats_i is not None
            clean = checkable_i and not has_issues(stats_i)
            levels.append({"level": tag, **meta_i, "fix_info": info,
                           "stats": stats_i,
                           "verdict": ("PASS" if clean else
                                       ("体检失败" if not checkable_i else "仍有缺陷"))})
            log("%s → %s 面，复检 %s" % (tag, meta_i["faces"],
                                        "通过" if clean else
                                        ("体检失败" if not checkable_i else "仍有缺陷")))
            cur_abs = fixed_abs
            if clean:
                report["status"] = "PASS"
                report["final_file"] = os.path.basename(fixed_abs)
                if level == "voxel":
                    report["caveat"] = ("体素重网格重建过几何：细节被钝化、内部结构丢失，"
                                        "打印前人工过目一遍再上架")
                break
        if report["status"] == "UNKNOWN":
            report["status"] = "FAIL"
            failed_dir = os.path.join(ws, "failed")
            os.makedirs(failed_dir, exist_ok=True)
            shutil.copy2(cur_abs, os.path.join(failed_dir, os.path.basename(cur_abs)))
            report["caveat"] = ("两级修复都没能闭合，最后产物已复制到 failed/ 目录，"
                                "回 Blender 手工处理或重新生成")
        report["findings"] = findings_of(levels[-1].get("stats"))

    jp, mp = write_report(ws, a.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    log("报告：%s / %s" % (jp, mp))


if __name__ == "__main__":
    main()
