---
name: hunyuan-3d-multiview
description: 图生3D全链路技能（腾讯混元 HY-3D）：图片→3D模型生成（单图/多视角，base64直连免图床）→ Blender后处理（渲染/质检/转台动画）→ 3D打印前闸门（体检→自动修复→复检闭环→STL导出）。触发词：图生3D、照片变3D模型/手办、三视图生成模型、AI 3D生成、GLB/OBJ、模型体检、网格修复、水密、导出STL、3D打印前检查、转台动画。
agent_created: true
---

# Hunyuan 3D 全链路（图生3D → Blender → 3D打印）

**2026-09-27 合并版**：原 `ai-3d-blender-pipeline`、`stl-mesh-preflight`（vendored）、
`dfam-check`（vendored）已并入本技能，全链一个入口。旧技能目录已移除（备份在
`~/.workbuddy/skills/_backup_20260927_merge/`）。

## 链路总览（先看清自己在哪一段）

```
图片 ─裁剪→ 混元生成 GLB ─┬─ 展示向：viewer.html 交付 / Blender 渲染·转台
                          └─ 打印向：闸门（体检→修复→复检）→ STL → 切片软件
```

| 段 | 干什么 | 入口 |
|---|---|---|
| A 生成 | 裁图 → 提交混元 → 收 GLB | `scripts/multiview_3d_driver.py` + `references/workflow.md` |
| B 后处理 | 导入 Blender、渲染美图、多角度质检、转台动画 | 本文件 Step B + `references/print/pitfalls.md` |
| C 打印闸门 | 体检→L1/L2修复→复检→STL | `scripts/print/print_gate.py` + workflow.md 第 8 节 |
| D 可打印性测量 | 壁厚/悬垂/摆放角度（DfAM） | `vendor/dfam-check/scripts/dfam_tool.py` |
| E 结构预检 | 边界边/非流形/零面积面（独立复核） | `vendor/stl-mesh-preflight/scripts/check.py` |

## 路由：按输入条件只选一条路

| 条件 | 去向 |
|------|------|
| 有三视图/多视角图 | 段A 多视角模式（PBR 开）→ `references/workflow.md` |
| 只有一张图 | 段A 单图模式（省 10 点）→ `references/workflow.md` |
| 只要几何不要贴图 | 段A `Geometry` 白模 |
| 要渲染/转台/文章配图 | 段B → 本文件 Step B |
| 要出实体打印件 | 段C 打印闸门 → `references/workflow.md` 第 8 节 |
| 问"能不能打/壁厚/悬垂" | 段D dfam-check（managed venv 的 python 跑） |
| 查参数/费用/校验规则 | `references/api-params.md` |
| 命令报错/环境异常 | `references/env-pitfalls.md`（先读！） |

## 铁律（任何段都适用）

1. **工作目录**：生成段产物落 `.hy3d/`（gitignore）；打印闸门产物落 `.hy3d/gate/`。绝不散落工作区。
2. **绝不盲目重提**：生成一次 30–40 点。提交前先 `check`（离线免费）；失败按 workflow.md 错误分级处理。
3. **Token 只走 stdin**：`connect_cloud_service` → tempToken → `echo -n "$TOKEN" | python ...`，禁止 argv/环境变量明文。
4. **产物必须落地**：GLB/viewer.html/渲染图用 `present_files` 交付；禁止把远程 URL 直接丢给用户。
5. **闸门解释器必须 Python 3.11**（`C:/Users/hwdem/AppData/Local/Programs/Python/Python311/python.exe`）；
   3.13 在 Windows 上 stat 不一致，内嵌预检会报 `INPUT_CHANGED`。dfam-check 则用 managed venv python。
6. **闸门长任务放后台**：`--autofix` 全程起 4–8 次 Blender 无头，轻松超 5 分钟，别挂前台。

## 输入预处理（生成段默认流程，已实测）

拿到图**先裁再提**：① 按视角裁单视角；② 取主体 bbox（`min(R,G,B) < 235` 为非背景）裁空白；
③ 贴白色正方画布，主体较长边占 70% 居中。实测 A/B：质量提示 2→0，形态与贴图对齐更准。
**面数不是质量指标**，别为提质量调高 `--face-count`。完整代码见 workflow.md 第 1 节。

## 一行起步（生成段）

```bash
python scripts/multiview_3d_driver.py init --front .hy3d/inputs/front.jpg --back .hy3d/inputs/back.jpg --left .hy3d/inputs/left.jpg
python scripts/multiview_3d_driver.py check .hy3d/jobs.json
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py submit .hy3d/jobs.json --token-stdin
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py collect .hy3d/jobs.json --token-stdin
```

## 段C：打印闸门（要出实体件时，一条命令闭环）

```bash
# 闭环：体检 → L1轻修复(保细节) → L2体素(保水密) → 复检 → 报告
"C:/Users/hwdem/AppData/Local/Programs/Python/Python311/python.exe" \
  scripts/print/print_gate.py \
  --workspace <项目绝对路径>/.hy3d/gate --glb .hy3d/outputs/xx.glb --height 180 --autofix
# 纯体检（只查不修）：--stl xx.stl 不带 --autofix
```

- 判定：`PASS`（final_file 可切片）/ `NEEDS_REVIEW`（有问题或体检没跑成，不放行）/ `FAIL`（两级修复都没救回，隔离 failed/）
- 产物：`.hy3d/gate/gate-out/gate-report.{json,md}`（各级前后对比表）
- 升级规则与限制详见 `references/workflow.md` 第 8 节（30000 面预检硬限→降面副本 caveat、L2 体素钝化警告）
- 问题归属路由：**AI 解剖缺陷（融合手指/五官穿插/悬空发束）→ 回生成段重生成**；**几何/水密/单位 → 闸门修**；壁厚数值 → dfam-check 出测量、切片软件终判

## Step B：Blender 后处理（渲染向）

- **导入**：`bpy.ops.import_scene.gltf`；glb 进来叫 `node_0`，重导为 `node_0.001`，先删旧的。
- **美图渲染**：引擎 `'BLENDER_EEVEE'`（不是 `BLENDER_EEVEE_NEXT`），`view_transform='Standard'`，
  删默认 1000W 灯，三点布光；镜头 50mm，1.2m 手办距离 2.0–2.6m 对半高。
- **多角度质检（必做）**：镜头 85、900×900 特写——脸正/3-4、双手、双脚侧/正、胯部接缝、全背。
  渲染 PNG 后用 Read 看图记录缺陷。完整 16+1 节体检清单（P0-P3 分级）用
  `references/print/inspection-prompt-mcp.md`。
- **转台动画**：`blender --background <blend> --python scripts/print/turntable_render.py`（72 帧，
  CLI 无超时），再 `python scripts/print/frames_to_anim.py <dir> --gif-out xx.gif`（微信规格 GIF<1MB）。
- 外观缺陷**重生成优于修补**（随机性一次修掉多数瑕疵）：收集缺陷清单 → 重跑生成 → 同清单复检。

## 段D：dfam-check 快速用法（可打印性测量，只读）

```bash
# managed venv python（trimesh/scipy 已装），先装 vendor/dfam-check/requirements.txt（已装可跳）
"C:/Users/hwdem/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
  vendor/dfam-check/scripts/dfam_tool.py measure part.stl --angle-limit 45
```

输出：watertight/euler/body_count、壁厚分布（p05/min/median）、悬垂面积与直方图、摆放角度候选、
单位可疑检测。对照 `vendor/dfam-check/references/process-limits.md` 选工艺限值；只出测量不做判定。

## 目录结构

```
hunyuan-3d-multiview/
├── SKILL.md                      # 本文件（全链路由）
├── scripts/
│   ├── multiview_3d_driver.py    # 段A：生成任务状态机
│   ├── prep_views.py             # 段A：输入裁剪
│   └── print/                    # 段B/C 脚本（原 ai-3d-blender-pipeline）
│       ├── print_gate.py         #   闸门编排（体检→修复→复检闭环）
│       ├── autofix_stl.py        #   L1轻修复 / L2体素
│       ├── probe_decimate.py     #   降面体检副本
│       ├── repair_for_print.py   #   diagnose/print/display 三模式
│       ├── run_3d_gen.py         #   单图生成（argv 注入绕 32KB 限制）
│       ├── blur_face.py          #   隐私打码
│       ├── turntable_render.py   #   转台渲染
│       └── frames_to_anim.py     #   帧序列→GIF/WebP
├── references/
│   ├── workflow.md               # 段A全流程+错误分级+第8节闸门
│   ├── api-params.md             # 参数/费用/校验
│   ├── env-pitfalls.md           # 环境坑（含双解释器规则）
│   └── print/                    # 打印段参考
│       ├── pitfalls.md           #   Blender 5.2 实测坑 22 条
│       └── inspection-prompt-mcp.md  # 16+1 节体检清单（P0-P3）
└── vendor/                       # 第三方技能（保留原 LICENSE 与出处）
    ├── stl-mesh-preflight/       #   结构预检（Python 3.11 跑）
    └── dfam-check/               #   DfAM 测量（managed venv 跑；源自 earthtojake/text-to-cad）
```

## Resources 速查

- 生成段：`references/workflow.md`（含 jobs.json 结构、错误分级恢复、第 8 节打印闸门）
- 打印段坑：`references/print/pitfalls.md`（**执行段B/C前必读**：引擎枚举名、签名URL、argv限制、
  `print3d_check_all` 真相——扩展未装而非算子删除、焊接阈值两套标准的设计意图）
- 体检规范：`references/print/inspection-prompt-mcp.md`
- 上游出处：stl-mesh-preflight（市场安装 v1.0.0）、dfam-check（github.com/earthtojake/text-to-cad）
