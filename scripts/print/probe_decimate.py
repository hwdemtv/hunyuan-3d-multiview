"""
probe_decimate.py — Blender 无头：把 STL 抽面到目标面数，产出「体检副本」。

用途：stl-mesh-preflight 有 30000 面 / 16MB 的硬限制，AI 生成的模型动辄几十万面，
必须先抽面才能过预检。产物只用于体检，不要拿去打印。

    blender --background --python probe_decimate.py -- <src.stl> <dst.stl> <target_faces>
"""
import bpy
import os
import sys

argv = sys.argv
if "--" not in argv:
    raise SystemExit("missing args after --")
src, dst, target = argv[argv.index("--") + 1:argv.index("--") + 4]
target = int(target)

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
print("[probe] start faces:", start)

# 循环逼近：DECIMATE 的 ratio 是近似值，一轮不一定压到位
for i in range(8):
    n = len(obj.data.polygons)
    if n <= target:
        break
    ratio = max(0.01, target / float(n) * 0.95)
    m = obj.modifiers.new("ProbeDecimate", "DECIMATE")
    m.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=m.name)
    print("[probe] pass %d ratio %.4f -> %d faces" % (i + 1, ratio, len(obj.data.polygons)))

end = len(obj.data.polygons)
os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
try:
    bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=True)
except AttributeError:
    bpy.ops.export_mesh.stl(filepath=dst, use_selection=True)
print("[probe] %d -> %d faces -> %s" % (start, end, dst))
if end > target:
    print("[probe] WARNING: still above target, preflight may reject the file")
