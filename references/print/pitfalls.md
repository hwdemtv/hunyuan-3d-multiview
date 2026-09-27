# Pitfalls & Field Notes (Blender 5.2 + Hunyuan 3D, tested)

> 2026-09-27 随 ai-3d-blender-pipeline 并入 hunyuan-3d-multiview，脚本位置变为
> `scripts/print/`（本文档内相对路径均指该目录）。

## Cloud 3D generation

1. **Bare COS URLs return 403.** The `ResultFile3Ds` URLs printed by a finished job are
   signed and expire (~24h). To re-download later, re-query:
   `printf '%s\n' '<token>' | python buddy-cloud.py status <job_id> --type 3d --token-stdin`
   and use the freshly signed URLs from the response.
2. **Token acquisition**: call the `connect_cloud_service` tool first, pass the
   `clientTempToken` via stdin (`--token-stdin`). Never put it on the command line.
3. **Windows argv limit**: `--image-base64` payloads exceed the ~32KB command-line limit.
   Always use `scripts/run_3d_gen.py` (importlib + sys.argv injection), not direct CLI.
4. **PBR costs extra credits**: EnablePBR roughly doubles cost (e.g. 20 -> 30 credits).
   Keep PBR on for figurines (better fabric/skin), off for quick tests.
5. **Image choice matters more than anything**: full-body, well-lit, plain-background,
   standing photos give the best results. Pose/props/hairstyle carry over; face detail
   does not (which is also why face blurring is nearly free quality-wise).
6. **Privacy**: blur the face before uploading real-person photos
   (`scripts/blur_face.py`; get the box by viewing the image first).

## Blender import & rendering

7. **glb imports as `node_0`** (or `node_0.001` on re-import). Delete the old model
   before importing a replacement; rename the new one to a clean name.
8. **Engine enum on Blender 5.2**: `'BLENDER_EEVEE'` — `'BLENDER_EEVEE_NEXT'` raises
   "enum not found". Workbench = `'BLENDER_WORKBENCH'`.
9. **Chinese UI pollutes API lookups**: Principled BSDF node is localized. Look up
   materials/nodes by type (`BSDF_PRINCIPLED`), modifiers via
   `obj.modifiers.new(name=..., type='BEVEL')`, never by English display name.
10. **Default 1000W point light overexposes everything.** Delete the default 'Light'
    object and set `view_transform = 'Standard'` (AgX washes colors out).
11. **Camera framing math**: with lens 50 / sensor 36 (vertical FOV ≈ 39.6°), visible
    height ≈ 0.72 × distance. A 1.2m figurine fits at ~1.9–2.6m distance targeting
    mid-height (z ≈ 0.5 × model height). Zero out any `shift_x/shift_y` first.
12. **MCP single-call timeout**: never run 72-frame animation renders through
    `execute_blender_code`. Write the script and run
    `blender --background <blend> --python script.py` (CLI has no timeout).
13. **No FFMPEG in this Blender build**: `image_settings.file_format = 'FFMPEG'` fails
    (not in enum). Render a PNG sequence, then compose GIF/WebP with
    `scripts/frames_to_anim.py`.
14. **Blender 5.x removed `Action.fcurves`**: wrap interpolation tweaks in try/except.

## Quality inspection checklist (multi-angle, never trust one shot)

Render close-ups at lens 85, 900×900, for each zone and compare against the source photo:

- Face front + 3/4 (look for: UV seam lines across nose/mouth, asymmetric eyes)
- Right hand, left hand (look for: broken cuffs, fused fingers, see-through holes)
- Feet side + front (look for: interpenetrating shoe tips, missing heel)
- Hip/side seams (look for: floating skin-colored fragments at clothing seams)
- Full back (look for: hair blobs, stray geometry)

## Print preparation (mesh repair, Blender 5.2)

15. **导入即"假残疾"**：AI 生成的 GLB 沿 UV 接缝拆点，未焊接时连通块/开放边数量爆炸
    （实测 6,433 块 / 155,736 开放边 → 焊接后 6 块 / 0）。**任何判断都必须在
    Merge by Distance（`bmesh.ops.remove_doubles`）之后做**，焊接距离取 `max(dim) * 1e-5`。
16. **体素重网格会毁掉 UV 与 PBR 贴图**，只在焊接后确有破洞时使用。它同时是降面和
    消除自交的手段，代价是细节台阶化（需 Laplacian 平滑补偿）。
17. **`bpy.ops.mesh.print3d_check_all` 裸装 5.2 上不可用**（RNA 存根残留：`hasattr` 恒 True，
    真调用报 "could not be found"）。根因：4.2 起 3D Print Toolbox 移出内置，须装扩展
    `blender --command extension install print3d_toolbox --enable`（包 ID 下划线）。
    装后模块名 `bl_ext.blender_org.print3d_toolbox`，结果读 `from ... import report; report.get()`。
    `repair_for_print.print3d_check()` 已做双模块名探测；不可用时自实现替代：闭合体积
    （`Σ a·(b×c)/6`，负值 = 法线朝内、模型内外翻转）+ 表面积 + 非流形/开放边计数。
18. **`bpy.data.objects.remove()` 之后再访问 `o.name` 会抛
    `ReferenceError: StructRNA of type Object has been removed`**。删碎块时"先判定、
    先取名字，后删除"。
19. **碎块阈值用比例，不用绝对数**：主体 24 万顶点时，5 个 258–445 顶点的残片全都
    超过 200 的绝对阈值，但只占主体 0.2%。取 `max(200, 最大块 × 1%)`。
20. **单位约定**：`scale_length = 0.001` + `length_unit = 'MILLIMETERS'` 让
    1 BU = 1 mm，之后所有尺寸参数（目标高度、体素）都用 mm 说话。导出 STL 前确认
    `dimensions.z` 等于目标高度。
21. **距离焊接是钝刀，别用来接发丝**。把手办比例下 >0.1mm 的焊接距离会把面部/手部
    细节一起熔掉（实测 0.5mm 焊接：240k → 52k 顶点，发丝仍没接上）。悬空发丝残片的
    正确处置：display 产物保留（贴近头发表面，渲染看不出）；print 产物直接丢弃。
    想保发丝就去雕刻模式手动桥接，别指望自动焊接。
22. **从源头重建优于原地修补**。MCP 会话里把网格改坏后，与其 undo（Python 数据层
    编辑不进 undo 栈），不如删掉重导 GLB，把标准流水线（焊接→分离→缩放→归底→法线）
    重放一遍——全程 <5s，且状态确定。

## Fix strategy: regenerate beats repair

AI 3D generation has per-run randomness. A defect list + one regeneration (with the
same or re-blurred source photo) usually fixes all artifacts and costs less time than
manual mesh surgery. Workflow: collect defect list -> regenerate -> re-inspect the
same checklist. Only hand-edit in Blender for trivial fixes (delete stray geometry).
