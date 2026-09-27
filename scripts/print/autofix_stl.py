"""
autofix_stl.py — Blender 无头：对 STL 做两级自动修复，供 print_gate.py 调用。

    blender --background --python autofix_stl.py -- <src.stl> <dst.stl> <light|voxel>

light  焊接重复顶点 → 删松散/退化 → 补洞 → 法线朝外。保细节，治标（洞、碎点、绕向）。
voxel  在 light 基础上再体素重网格 + 平滑。治本（保证水密），代价是细节被重建成
       等距网格、内部结构丢失。产物是结构上可打印的"钝化版"。

输出一行 AUTOFIX_FACES <n> 供编排脚本解析。
"""
import bmesh
import bpy
import os
import sys

argv = sys.argv
if "--" not in argv:
    raise SystemExit("missing args after --")
rest = argv[argv.index("--") + 1:]
if len(rest) < 3:
    raise SystemExit("usage: -- <src.stl> <dst.stl> <light|voxel> [voxel_mm]")
src, dst, level = rest[0], rest[1], rest[2]
if level not in ("light", "voxel"):
    raise SystemExit("level must be light or voxel")


def log(*a):
    print("[autofix]", *a)


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=src)

objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit("no mesh in " + src)
if len(objs) > 1:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
start = len(obj.data.polygons)
log("start faces:", start)

# 焊接阈值：包围盒最长边的万分之一，保底 1e-5，防止把刻意的小间隙焊死
max_dim = max(obj.dimensions) or 1.0
weld = max(max_dim * 1e-4, 1e-5)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=weld)
log("welded (threshold %.5f)" % weld)
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.delete_loose()
bpy.ops.mesh.dissolve_degenerate(threshold=0.0)
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.fill_holes(sides=0)
log("filled holes")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")

if level == "voxel":
    # 高面数直接开体素又慢又吃内存：先压到 6 万面以内
    n = len(obj.data.polygons)
    if n > 60000:
        m0 = obj.modifiers.new("PreDecimate", "DECIMATE")
        m0.ratio = max(0.01, 60000.0 / n)
        bpy.ops.object.modifier_apply(modifier=m0.name)
        log("pre-decimate %d -> %d faces" % (n, len(obj.data.polygons)))
    v = float(rest[3]) if len(rest) > 3 else max(max_dim / 150.0, 0.15)
    m = obj.modifiers.new("VoxelRemesh", "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = v
    bpy.ops.object.modifier_apply(modifier=m.name)
    log("voxel remesh %.3f mm -> %d faces" % (v, len(obj.data.polygons)))
    m2 = obj.modifiers.new("LapSmooth", "LAPLACIANSMOOTH")
    m2.iterations = 2
    m2.lambda_factor = 0.5
    bpy.ops.object.modifier_apply(modifier=m2.name)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

end = len(obj.data.polygons)

bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
try:
    bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=True)
except AttributeError:
    bpy.ops.export_mesh.stl(filepath=dst, use_selection=True)
print("AUTOFIX_FACES %d" % end)
log("done: %d -> %d faces -> %s" % (start, end, dst))

# 修复后自检一遍开放边，直接给出结构判定
bm = bmesh.new()
bm.from_mesh(obj.data)
open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
nm_edges = sum(1 for e in bm.edges if len(e.link_faces) > 2)
bm.free()
print("AUTOFIX_OPEN_EDGES %d" % open_edges)
print("AUTOFIX_NONMANIFOLD_EDGES %d" % nm_edges)
