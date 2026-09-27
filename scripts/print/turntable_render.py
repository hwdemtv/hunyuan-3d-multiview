# -*- coding: utf-8 -*-
"""Turntable render for an imported character/figurine model in Blender.

Run via CLI (no UI needed):
    blender --background <file.blend> --python turntable_render.py

Renders a 360-degree turntable (72 frames, PNG sequence) of the figurine.
Compatible with Blender 5.x: no FFMPEG output (compose with frames_to_anim.py),
fcurves API wrapped in try/except.
"""
import bpy
import math
from mathutils import Vector

OUT_DIR = bpy.path.abspath("//")  # blend file directory

# ---- locate the figurine object(s) ----
scene = bpy.context.scene
fig_objs = [o for o in scene.objects
            if o.type == 'MESH' and o.name.startswith(('Mesh_', 'model', 'figurine', 'character', 'node_'))]
if not fig_objs:
    known = {'Ground', 'Coffee_Surface', 'Mug_Body', 'Mug_Handle', 'Camera', 'Backdrop'}
    fig_objs = [o for o in scene.objects if o.type == 'MESH' and o.name not in known]
# keep only visible ones (old versions may be hidden in the same file)
fig_objs = [o for o in fig_objs if not o.hide_render]
print('FIGURINE OBJECTS:', [o.name for o in fig_objs])

# ---- parent to an empty pivot and center on origin ----
root_name = 'Figurine_Root'
root = bpy.data.objects.get(root_name)
if root is None:
    root = bpy.data.objects.new(root_name, None)
    scene.collection.objects.link(root)
    root.empty_display_size = 0.1
    for o in fig_objs:
        o.parent = root

zs, xs, ys = [], [], []
for o in fig_objs:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        xs.append(w.x); ys.append(w.y); zs.append(w.z)
cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
zmin = min(zs)
root.location = (0, 0, 0)
for o in fig_objs:
    if o.parent == root:
        o.matrix_parent_inverse.identity()
        o.location = (o.location.x - cx, o.location.y - cy, o.location.z - zmin)

# ---- camera: 3/4 view, auto-framed by model height ----
cam = bpy.data.objects['Camera']
h = max(zs) - zmin
cam.data.lens = 55
cam.location = (h * 2.2, -h * 2.2, h * 1.3)
cam.rotation_euler = (Vector((0, 0, h * 0.5)) - cam.location).to_track_quat('-Z', 'Y').to_euler()

# ---- rotation keyframes: 72 frames = 360 deg (30 fps -> 2.4 s) ----
scene.frame_start = 1
scene.frame_end = 72
scene.render.fps = 30
root.rotation_euler = (0, 0, 0)
root.keyframe_insert(data_path='rotation_euler', frame=1)
root.rotation_euler = (0, 0, math.radians(360))
root.keyframe_insert(data_path='rotation_euler', frame=72)
try:  # Blender 5.x removed Action.fcurves; fall back to default interpolation
    for fc in root.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
except Exception:
    pass

# ---- render PNG sequence (this build has no FFMPEG) ----
scene.render.engine = 'BLENDER_EEVEE'  # NOT 'BLENDER_EEVEE_NEXT' on Blender 5.2
scene.render.resolution_x = 720
scene.render.resolution_y = 960
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUT_DIR + r"\turntable_"
bpy.ops.render.render(animation=True)
print('TURNTABLE RENDER DONE')
