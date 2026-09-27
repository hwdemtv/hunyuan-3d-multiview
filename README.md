# Hunyuan 3D Multiview · 混元图生 3D 全链路工作流

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20WorkBuddy-lightgrey)](#环境要求)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green)](#环境要求)
[![Model](https://img.shields.io/badge/Model-Tencent%20Hunyuan3D%203.1-orange)](https://cloud.tencent.com/document/product/1770)
[![Blender](https://img.shields.io/badge/Blender-5.2%20tested-yellow)](#环境要求)

**A full-pipeline WorkBuddy agent skill: image → Hunyuan3D generation → Blender post-processing → print-gate (mesh inspect → auto-repair → re-inspect) → slicer-ready STL.**

一个 [WorkBuddy](https://www.workbuddy.cn) 智能体技能，覆盖**图生 3D 到 3D 打印的完整链路**：

```
图片 ─裁剪→ 混元生成 GLB ─┬─ 展示向：viewer 交互预览 / Blender 渲染美图 / 转台动画
                          └─ 打印向：闸门（体检 → L1/L2 自动修复 → 复检）→ 可切片 STL
```

2026-09-27 起本技能为全链路唯一入口，合并了原 `ai-3d-blender-pipeline`（Blender 后处理与打印闸门）、`stl-mesh-preflight`（结构预检，vendored）、`dfam-check`（DfAM 可打印性测量，vendored）。

![单图 vs 多视角生成效果对比](assets/comparison.png)

## ✨ 特性 / Features

### 生成段（混元 HY-3D）
- 🖼️ **多视角输入**：front + `back` / `left` / `right`（3.1 支持八视图），背面细节不再靠 AI"猜"
- 🔐 **免图床**：视角图以 Base64 内嵌请求体（官方 `ViewImageBase64` 字段），本地图直接用
- 🔁 **任务状态机**：`init → check → submit → collect`，可续跑、不重复付费；token 全程 stdin
- 📦 **标准产物**：GLB（含 PBR）+ model-viewer 交互预览页

### 打印段（Blender 5.2 + 独立预检）
- 🚪 **打印闸门一条命令**：体检 → 自动修复 → 复检闭环，出 `PASS / NEEDS_REVIEW / FAIL` 判定与前后对比报告
- 🩺 **两级修复策略**：L1 轻修复（焊接顶点/补洞/法线，保细节）→ L2 体素重网格（保水密），修不动自动隔离 `failed/`
- 🔍 **三双眼睛交叉验证**：Blender 自算体检 + stl-mesh-preflight 结构预检（沙箱化、只读）+ trimesh 全量复核
- 📏 **DfAM 测量**：壁厚分布 / 悬垂角度直方图 / 摆放角度候选 / 单位可疑检测（vendored dfam-check，trimesh 驱动）
- 📋 **16+1 节视觉体检清单**：形体/脸/发/手/脚/服装/配饰/几何/实体/薄壁/悬空/方向/重心/AI 专项，P0–P3 分级，只报不修

### 后处理段（渲染向）
- 🎬 Blender 渲染美图（EEVEE）、72 帧转台动画（CLI 无超时）、微信规格 GIF/WebP 输出

## 📊 实测数据

**生成质量（单图 vs 多视角，同角色、3.1 + PBR）**

| 指标 | 单图模式 | 多视角模式 |
|------|---------|-----------|
| 输入 | 仅正视图 | 正 + 后 + 左视图 |
| 耗时 | ~3.5 min | ~6 min |
| 消耗 | 30 点/次 | 40 点/次 |
| 背面还原度 | 依赖推断 | 按输入视图精确还原 |

**打印闸门闭环（81800 面测试件，人工挖 2 洞）**

| 级别 | 动作 | 边界边 | 判定 |
|---|---|---|---|
| L0 体检 | 降面副本结构预检 | 196 | FAIL |
| L1 轻修复 | 焊接 + 补洞 + 法线 | 6 | 仍有缺陷 |
| L2 体素 | 降面 + 体素重网格 | **0** | **PASS** |

全程 3m25s；最终件经 trimesh 全量复核 `watertight=True`。

**裁剪标准化 A/B（bbox + 白方画布 70% vs 三等分直裁）**：质量提示 2→0，形态与贴图对齐更准，面数还略低——收益来自重建精度而非堆面数。

## 🚀 快速开始 / Quick Start

### 1. 环境要求

- Python 3.10+（`requests`；WorkBuddy 环境内置）
- WorkBuddy Desktop（`connect_cloud_service` 临时凭证 + `buddy-cloud.py` 签名通道）
- **打印段**：Blender 4.2+（5.2 实测；体素重网格/闸门的无头批处理）；预检必须 Python 3.11（3.13 在 Windows 上 stat 不一致会报 `INPUT_CHANGED`）；dfam-check 需 `pip install trimesh scipy rtree networkx lxml`
- 三视图 / 多视角图片（白底最佳）

### 2. 安装技能（三选一）

**方式 A · 一行命令安装（推荐）**

Windows（PowerShell）：

```powershell
irm https://raw.githubusercontent.com/hwdemtv/hunyuan-3d-multiview/main/install.ps1 | iex
```

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/hwdemtv/hunyuan-3d-multiview/main/install.sh | bash
```

**方式 B · 直接把仓库地址丢给 WorkBuddy**

> 安装技能：https://github.com/hwdemtv/hunyuan-3d-multiview

**方式 C · 手动复制**

把 `SKILL.md`、`scripts/`、`references/`、`vendor/` 复制到 `~/.workbuddy/skills/hunyuan-3d-multiview/`。

### 3. 生成：裁剪 → 提交 → 收 GLB

```bash
# 标准化裁剪（bbox + 白方画布 70%）
python scripts/prep_views.py --front view_front.jpg --back view_back.jpg --left view_left.jpg \
  --out-dir .hy3d/inputs

# 登记任务 → 离线自检（不花点数）→ 提交 → 轮询下载
python scripts/multiview_3d_driver.py init \
  --front .hy3d/inputs/front.jpg --back .hy3d/inputs/back.jpg --left .hy3d/inputs/left.jpg
python scripts/multiview_3d_driver.py check .hy3d/jobs.json
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py submit .hy3d/jobs.json
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py collect .hy3d/jobs.json
```

### 4. 打印闸门：GLB → 可切片 STL（一条命令闭环）

```bash
# 体检 → L1轻修复(保细节) → L2体素(保水密) → 复检 → 报告；数分钟，建议放后台
python scripts/print/print_gate.py \
  --workspace <项目绝对路径>/.hy3d/gate \
  --glb .hy3d/outputs/figure-01_xxx.glb --height 180 --autofix
```

- 判定 `PASS` 时 `final_file` 即可切片的 STL；报告在 `.hy3d/gate/gate-out/gate-report.{json,md}`
- 只体检不修：去掉 `--autofix`；强制从体素级开始：`--start-level voxel`
- **问题归属路由**：AI 解剖缺陷（融合手指/五官穿插）→ 回生成段重生成；几何/水密/单位 → 闸门修；壁厚数值 → dfam-check 出测量、切片软件终判

### 5. DfAM 可打印性测量（可选，只读）

```bash
python vendor/dfam-check/scripts/dfam_tool.py measure part.stl --angle-limit 45
```

## 🧠 设计要点

- **闸门哲学**：把"看起来没问题"和"真能打印"分开。视觉体检（16+1 节清单）管前者，数值闸门（预检+自算+trimesh 三双眼睛）管后者，两者互补不替代。
- **修复分级**：L1 保细节优先，L2 体素保水密兜底——因为体素重网格会毁 UV/贴图、钝化细节，永远先试轻修复。
- **诚实边界**：预检硬限 3 万面/16MB，大模型走降面副本（副本结论 ≠ 原件结论，报告 caveat 标注）；L2 通过必附"人工过目"警告；壁厚/支撑不在闸门判定内，交给切片软件。
- **vendored 而非重写**：stl-mesh-preflight（沙箱化只读预检）与 dfam-check（trimesh 测量）整包引入、保留上游 LICENSE，改名 `SKILL.vendor.md` 防止被宿主误注册为独立技能。

## ❓ FAQ

**Q: 只有一张普通照片可以吗？**
可以，`--front` 单图模式（省 10 点），背面细节由模型推断。

**Q: 闸门 PASS 了就一定能打印吗？**
结构上水密可切片，但壁厚/支撑/摆放仍需切片软件复核——报告的"本报告不覆盖"清单写明了边界。

**Q: 为什么预检要用 Python 3.11？**
stl-mesh-preflight 的 TOCTOU 校验在 Windows Python 3.13 上会因 stat 行为不一致误报 `INPUT_CHANGED`，3.11 实测稳定。

**Q: 费用怎么算？**
3.1 版：多视角 40 点/次（Normal 20 + MultiView 10 + PBR 10），单图 30 点/次。`check` 离线自检免费。

**Q: 支持 Linux / macOS 吗？**
脚本跨平台；路径示例以 WorkBuddy Desktop Windows 环境为准，`BUDDY_CLOUD_SCRIPT` 环境变量可覆盖内置脚本位置。

## 📁 目录结构

```
hunyuan-3d-multiview/
├── SKILL.md                       # 全链路路由入口（A生成/B后处理/C闸门/D测量/E预检）
├── scripts/
│   ├── multiview_3d_driver.py     # 生成任务状态机 init/check/submit/collect/run
│   ├── prep_views.py              # 视角图标准化
│   └── print/                     # 打印段脚本
│       ├── print_gate.py          #   闸门编排（体检→修复→复检闭环）
│       ├── autofix_stl.py         #   L1 轻修复 / L2 体素重网格
│       ├── probe_decimate.py      #   降面体检副本
│       ├── repair_for_print.py    #   diagnose / print / display 三模式
│       ├── run_3d_gen.py          #   单图生成（argv 注入绕 32KB 限制）
│       ├── blur_face.py           #   隐私打码
│       ├── turntable_render.py    #   转台渲染（72 帧）
│       └── frames_to_anim.py      #   帧序列 → GIF/WebP
├── references/
│   ├── workflow.md                # 生成全流程 + 错误分级 + 第8节打印闸门
│   ├── api-params.md              # 参数/视角/费用/校验规则
│   ├── env-pitfalls.md            # 环境坑（双解释器规则等）
│   └── print/
│       ├── pitfalls.md            # Blender 5.2 实测坑 22 条
│       └── inspection-prompt-mcp.md  # 16+1 节视觉体检清单（P0–P3）
├── vendor/                        # 第三方（保留上游 LICENSE）
│   ├── stl-mesh-preflight/        #   STL 结构预检（SKILL.vendor.md）
│   └── dfam-check/                #   DfAM 测量（SKILL.vendor.md）
├── install.ps1  install.sh        # 一行安装
├── CONTRIBUTING.md
└── assets/                        # 示例预览
```

运行期产物落在 `.hy3d/`（`jobs.json` + `outputs/` + `gate/`），已 gitignore。

## 🏷️ 关键词

`腾讯混元` `Hunyuan3D` `图生3D` `image-to-3d` `multi-view 3D` `3D 手办` `figurine` `GLB` `OBJ` `PBR` `Blender` `mesh repair` `non-manifold` `watertight` `STL` `3D printing` `DfAM` `print gate` `MakerWorld` `WorkBuddy` `agent skill`

## 📄 License

[MIT](LICENSE)（vendor/ 内子项目保留各自上游 License 与出处）
