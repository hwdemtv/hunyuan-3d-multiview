"""
repair_for_print.py — 混元 GLB（AI 生成）→ Blender 修复 / 打印预处理

设计原则：先体检、后动刀。默认 MODE="diagnose" 只读不写。

三种模式：
  diagnose  导入模型并输出体检报告（尺寸、面数、非流形边、碎块数、法线），不改模型
  print     打印向：清理碎块 → 统一法线 → 体素重网格（水密）→ 平滑 → 缩放到目标高度
            → 原点归底 → 导出 STL（**会丢失 UV / PBR 贴图**）
  display   展示向：保留 PBR 贴图，抽面 + 平滑 + 修法线，导出 GLB（不水密，不可直接打印）

用法一（交互，推荐）：Blender 5.2 → Scripting → 打开本文件 → 改 CONFIG → Run Script
用法二（无界面，长任务不超时）：
    "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --background ^
      --python repair_for_print.py -- --mode print --glb "<path>.glb" --height 180

Blender 5.2 已验证的 API 要点见 SKILL.md / references/pitfalls.md。
"""
import addon_utils
import bmesh
import bpy
import mathutils
import os
import sys

# ---------------------------------------------------------------- CONFIG --
CONFIG = dict(
    # 交互模式（Scripting 面板直接 Run）时的默认值；CLI 用 --glb/--out 覆盖
    GLB_PATH=None,              # None = 交互模式必须先填；CLI 不传 --glb 会报错退出
    OUT_DIR=None,               # None = 默认取 GLB 同目录
    MODE="diagnose",             # diagnose | print | display
    TARGET_HEIGHT_MM=180.0,      # 手办常见 1/7 比例约 180–220mm
    VOXEL_MM=None,               # 体素尺寸(mm)；None = 高度/250（兼顾细节与内存）
    DECIMATE_RATIO=0.35,         # display 模式抽面比例
    SMOOTH_ITERATIONS=6,         # 拉普拉斯平滑迭代（体素台阶感）
    SMOOTH_FACTOR=0.5,
    MIN_PART_VERTS=200,          # 小于此顶点数的碎块直接删
    DO_VOXEL_REMESH=True,        # print 模式是否做体素重网格（水密化）
    EXPORT_STL=True,
    EXPORT_GLB=False,
)


def parse_cli():
    """blender --background --python x.py -- --mode print --glb ... --height 180"""
    args = sys.argv
    if "--" not in args:
        return
    rest = args[args.index("--") + 1:]
    it = iter(rest)
    for a in it:
        if a == "--mode":
            CONFIG["MODE"] = next(it)
        elif a == "--glb":
            CONFIG["GLB_PATH"] = next(it)
        elif a == "--out":
            CONFIG["OUT_DIR"] = next(it)
        elif a == "--height":
            CONFIG["TARGET_HEIGHT_MM"] = float(next(it))
        elif a == "--voxel":
            CONFIG["VOXEL_MM"] = float(next(it))
        elif a == "--ratio":
            CONFIG["DECIMATE_RATIO"] = float(next(it))
        elif a == "--no-voxel":
            CONFIG["DO_VOXEL_REMESH"] = False


def log(*a):
    print("[repair]", *a)


# ------------------------------------------------------------------ 单位 --
def use_millimeters():
    """1 Blender 单位 = 1 mm，后续所有尺寸参数都以 mm 计。"""
    s = bpy.context.scene
    s.unit_settings.system = "METRIC"
    s.unit_settings.length_unit = "MILLIMETERS"
    s.unit_settings.scale_length = 0.001


# ------------------------------------------------------------------ 导入 --
def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o.name not in before and o.type == "MESH"]
    if not new:
        raise SystemExit("no mesh imported from " + path)
    log("imported:", [o.name for o in new])
    return new


def active_mesh(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    return objs[0]


def join_all(objs):
    if len(objs) < 2:
        return objs[0]
    a = active_mesh(objs)
    bpy.ops.object.join()
    return a


# ------------------------------------------------------------------ 体检 --
def mesh_report(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    tris = len(bm.faces)
    verts = len(bm.verts)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)      # 开放边=洞
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    # 连通块（碎块）统计
    seen = set()
    parts = []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, n = [v], 0
        while stack:
            cv = stack.pop()
            if cv.index in seen:
                continue
            seen.add(cv.index)
            n += 1
            for e in cv.link_edges:
                o = e.other_vert(cv)
                if o.index not in seen:
                    stack.append(o)
        parts.append(n)
    bm.free()
    parts.sort(reverse=True)
    dims = obj.dimensions
    return {
        "object": obj.name,
        "size_mm": tuple(round(x, 2) for x in dims),
        "verts": verts,
        "tris": tris,
        "open_edges": boundary,
        "non_manifold_edges": non_manifold,
        "parts": len(parts),
        "largest_parts": parts[:5],
    }


def weld(obj, dist=None):
    """按距离焊接顶点。

    AI 生成的 GLB 常沿 UV 接缝把顶点拆开（PBR 图集有很多岛），导入后表现为
    成千上万个"连通块"和海量"开放边"——**那不是真的破洞**。先看焊接后的数据
    再判断是否需要水密化，否则会被假阳性吓到去做毁贴图的体素重网格。
    """
    if dist is None:
        dist = max(obj.dimensions) * 1e-5
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    log(f"weld @ {dist:.6f}: {before} → {len(obj.data.vertices)} verts")
    return obj


def diagnose(obj):
    r = mesh_report(obj)
    log("=== 体检报告（原始导入） ===")
    for k, v in r.items():
        log(f"  {k}: {v}")

    # 焊接后复测：区分"UV 接缝假阳性"与"真破洞"
    probe = obj.copy()
    probe.data = obj.data.copy()
    bpy.context.collection.objects.link(probe)
    weld(probe)
    r2 = mesh_report(probe)
    bpy.data.objects.remove(probe, do_unlink=True)
    log("=== 焊接后（真实连通性） ===")
    for k in ("parts", "largest_parts", "open_edges", "non_manifold_edges"):
        log(f"  {k}: {r2[k]}")

    verdict = []
    seam_noise = r["parts"] - r2["parts"]
    if seam_noise > 20:
        log(f"  注：原始 {r['parts']} 块中有 {seam_noise} 块是 UV 接缝拆点造成的假象，"
            f"焊接后归并为 {r2['parts']} 块")
    if r2["open_edges"] or r2["non_manifold_edges"]:
        verdict.append("焊接后仍有洞/非流形边 → 切片会报错，打印前必须水密化"
                       "（print 模式体素重网格，代价：丢贴图）")
    if r2["parts"] > 1:
        verdict.append(f"焊接后仍有 {r2['parts']} 个连通块 → 浮空碎件，"
                       f"print 模式自动删除 <{CONFIG['MIN_PART_VERTS']} 顶点的小块")
    if r["tris"] > 800000:
        verdict.append("面数偏高 → 切片慢，建议 decimate 或体素重网格降面")
    log("=== 结论 ===")
    for v in verdict or ["基本健康，可直接 display 模式导出"]:
        log("  -", v)
    return r, r2


# ------------------------------------------------------------------ 修复 --
def edit_all(obj):
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def object_mode():
    bpy.ops.object.mode_set(mode="OBJECT")


def drop_small_parts(obj, min_verts, min_fraction=0.01):
    """分离松散块，删掉浮空碎件（发丝碎片、配饰残片）。

    阈值取 max(min_verts, min_fraction × 最大块)：绝对阈值会在大模型上漏判
    （实测：5 个 265–445 顶点的残片全部超过 200 的绝对阈值，但只占主体 0.2%）。
    """
    active_mesh([obj])
    edit_all(obj)
    bpy.ops.mesh.separate(type="LOOSE")
    object_mode()
    kept = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    sizes = sorted(((len(o.data.vertices), o) for o in kept), reverse=True)
    if not sizes:
        return obj
    threshold = max(min_verts, sizes[0][0] * min_fraction)
    # 先判定、后删除：删除后再访问 o.name 会抛
    # ReferenceError: StructRNA of type Object has been removed
    keep, drop = [], []
    for n, o in sizes:
        (keep if n >= threshold else drop).append((n, o))
    dropped_names = [(o.name, n) for n, o in drop[:10]]   # 删除前先取名字
    for _, o in drop:
        bpy.data.objects.remove(o, do_unlink=True)
    log(f"threshold {threshold:.0f} verts; dropped {len(drop)} part(s): {dropped_names}")
    return join_all([o for _, o in keep]) if keep else obj


def recalc_normals(obj):
    active_mesh([obj])
    edit_all(obj)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    object_mode()
    log("normals recalculated (outside)")


def scale_to_height(obj, target_mm):
    h = max(obj.dimensions)
    if h <= 0:
        return
    f = target_mm / h
    obj.scale = (obj.scale[0] * f, obj.scale[1] * f, obj.scale[2] * f)
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    log(f"scaled to height {target_mm} mm (factor {f:.3f}); size now "
        f"{tuple(round(x, 2) for x in obj.dimensions)}")


def origin_to_bottom(obj):
    """原点放到包围盒底面中心 —— 切片软件默认模型贴平台。"""
    active_mesh([obj])
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    zmin = min((obj.matrix_world @ mathutils.Vector(c))[2] for c in obj.bound_box)
    obj.location[2] -= zmin
    bpy.context.view_layer.update()
    log(f"origin set to bottom center (shift z by {-zmin:.2f} mm)")


def voxel_remesh(obj, voxel_mm):
    """水密化的核心：体素重网格。代价是 **UV 与贴图全部丢失**。"""
    obj.data.remesh_voxel_size = voxel_mm
    old_tris = len(obj.data.polygons)
    active_mesh([obj])
    bpy.ops.object.voxel_remesh()
    log(f"voxel remesh @ {voxel_mm} mm: {old_tris} → {len(obj.data.polygons)} tris "
        f"(UV/texture lost)")
    return obj


def laplacian_smooth(obj, iterations, factor):
    m = obj.modifiers.new("LapSmooth", "LAPLACIANSMOOTH")
    m.iterations = iterations
    m.lambda_factor = factor
    try:
        bpy.ops.object.modifier_apply(modifier=m.name)
    except Exception as e:            # 老版本 op 签名不同
        log("modifier_apply failed, keeping modifier unapplied:", e)
    log(f"laplacian smooth x{iterations} (factor {factor})")


def decimate(obj, ratio):
    m = obj.modifiers.new("Decimate", "DECIMATE")
    m.ratio = ratio
    old = len(obj.data.polygons)
    bpy.ops.object.modifier_apply(modifier=m.name)
    log(f"decimate {ratio}: {old} → {len(obj.data.polygons)} tris")


def solid_stats(obj):
    """自实现的实体检查：闭合体积 + 表面积 + 法线朝向。

    Blender 5.2 里 `bpy.ops.mesh.print3d_check_all` 已不可用（op 找不到），
    所以自己算。体积为负 = 法线整体朝内（内外翻转），切片会得到空壳。
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    mw = obj.matrix_world
    vol = area = 0.0
    for tri in mesh.loop_triangles:
        a, b, c = (mw @ mesh.vertices[i].co for i in tri.vertices)
        vol += a.dot(b.cross(c)) / 6.0
        area += (b - a).cross(c - a).length / 2.0
    vol_cm3 = vol / 1000.0        # 1 BU = 1 mm（见 use_millimeters）
    area_cm2 = area / 100.0
    log(f"volume {vol_cm3:.1f} cm³ | surface {area_cm2:.0f} cm²")
    if vol_cm3 < 0:
        log("  ! 体积为负：法线朝内，模型内外翻转 → 执行 recalc_normals 后再导出")
    elif vol_cm3 < 0.05:
        log("  ! 体积过小：可能没实体或缩放异常")
    return vol_cm3, area_cm2


def print3d_check(obj):
    """优先用 3D Print Toolbox 扩展（v1.4+，bl_ext 命名空间）；不可用则退回自实现检查。

    Blender 4.2+ 起 3D Print Toolbox 不再内置，须先安装扩展：
        blender --command extension install print3d_toolbox --enable
    （注意包 ID 是下划线 print3d_toolbox，不是 URL 里的连字符。）
    模块名也从旧 object_print3d_utils 变为 bl_ext.<repo>.print3d_toolbox。
    hasattr(bpy.ops.mesh, 'print3d_check_all') 恒为 True（RNA 存根），
    不能用它判断可用性——必须 enable 后实际调用。
    """
    active_mesh([obj])
    ok = False
    for mod in ("bl_ext.blender_org.print3d_toolbox", "object_print3d_utils"):
        try:
            addon_utils.enable(mod, default_set=False, persistent=False)
            bpy.ops.mesh.print3d_check_all()
            log("print3d_check_all 已执行（%s）：结果见扩展面板" % mod)
            ok = True
            break
        except Exception as e:
            log("3D-Print 工具箱不可用（%s）：%s" % (mod, repr(e)[:120]))
    if ok:
        try:
            from bl_ext.blender_org.print3d_toolbox import report
            for item in report.get():
                log("  %-24s %s" % (item.name, item.value))
        except Exception as e:
            log("读取报告失败（%s）→ 退回自实现检查" % repr(e)[:120])
    solid_stats(obj)
    return ok


def export(obj, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    active_mesh([obj])
    if CONFIG["EXPORT_STL"]:
        p = os.path.join(out_dir, name + ".stl")
        try:                                   # Blender 4.2+/5.x
            bpy.ops.wm.stl_export(filepath=p, export_selected_objects=True)
        except AttributeError:                 # 旧版
            bpy.ops.export_mesh.stl(filepath=p, use_selection=True)
        log("exported", p)
    if CONFIG["EXPORT_GLB"]:
        p = os.path.join(out_dir, name + ".glb")
        bpy.ops.export_scene.gltf(filepath=p, export_format="GLB", use_selection=True)
        log("exported", p)


# ------------------------------------------------------------------- main --
def main():
    parse_cli()
    mode = CONFIG["MODE"]
    glb = CONFIG["GLB_PATH"]
    if not glb:
        raise SystemExit("GLB_PATH 未设置：CLI 用 --glb 传路径；交互模式先改 CONFIG")
    if CONFIG["OUT_DIR"] is None:
        CONFIG["OUT_DIR"] = os.path.dirname(os.path.abspath(glb))
    if not os.path.exists(glb):
        raise SystemExit("GLB not found: " + glb)

    if bpy.app.background:          # CLI 模式清空启动场景，交互模式绝不清（会丢用户场景）
        bpy.ops.wm.read_factory_settings(use_empty=True)

    use_millimeters()
    objs = import_glb(glb)
    obj = join_all(objs)
    obj.name = "figurine"

    if mode == "diagnose":
        diagnose(obj)
        return

    weld(obj)                       # 先缝合 UV 接缝拆点，再谈碎块与破洞
    obj = drop_small_parts(obj, CONFIG["MIN_PART_VERTS"])
    recalc_normals(obj)

    if mode == "print":
        scale_to_height(obj, CONFIG["TARGET_HEIGHT_MM"])
        if CONFIG["DO_VOXEL_REMESH"]:
            v = CONFIG["VOXEL_MM"] or max(0.5, CONFIG["TARGET_HEIGHT_MM"] / 250.0)
            h = max(obj.dimensions)
            log(f"voxel grid ≈ {h / v:.0f} cells along the tallest axis "
                f"(>400 会很慢，可调大 --voxel)")
            voxel_remesh(obj, v)
            laplacian_smooth(obj, CONFIG["SMOOTH_ITERATIONS"], CONFIG["SMOOTH_FACTOR"])
            recalc_normals(obj)
        origin_to_bottom(obj)
        print3d_check(obj)
        export(obj, CONFIG["OUT_DIR"], "figurine_print")
    elif mode == "display":
        decimate(obj, CONFIG["DECIMATE_RATIO"])
        laplacian_smooth(obj, 2, 0.3)
        recalc_normals(obj)
        export(obj, CONFIG["OUT_DIR"], "figurine_display")
    else:
        raise SystemExit("unknown MODE: " + mode)

    log("=== 修复后 ===")
    diagnose(obj)


main()
