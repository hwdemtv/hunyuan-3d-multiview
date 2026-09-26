---
name: hunyuan-3d-multiview
description: 图生3D手办/模型工作流（腾讯混元 HY-3D）。当用户提供图片（尤其是三视图/多视角图）要求生成 3D 模型、3D 手办、GLB/OBJ 文件时使用。支持单图与多视角（front+back/left/right 等）两种模式，全程 base64 直连、无需外部图床；通过任务文件（.hy3d/jobs.json）与 check/submit/collect 状态机保证可续跑、不重复付费。
agent_created: true
---

# Hunyuan 3D Multiview（图生3D 多视角工作流）

基于腾讯混元 3D（SubmitHunyuanTo3DProJob，复用 WorkBuddy 内置 `buddy-cloud.py` 的签名通道）把图片转成 3D 模型。核心价值：**多视角图片全程走 Base64（`ViewImageBase64`），无需公网图床**——内置脚本只封装了 `ViewImageUrl`。

## 路由：先选模式，再读对应手册

按输入条件**只选一条路**，读完再动手，不要同时套用两种模式：

| 条件 | 模式 | 后续 |
|------|------|------|
| 有三视图 / 多视角图（正 + 后/左/右） | 多视角模式，PBR 开 | 读 `references/workflow.md` |
| 只有一张图 | 单图模式（可省 10 点） | 读 `references/workflow.md` |
| 只要几何、不要贴图 | `Geometry` 白模 | 读 `references/workflow.md` |
| 需要查参数 / 费用 / 视角 / 校验规则 | — | 读 `references/api-params.md` |
| 命令报错或环境异常 | — | 读 `references/env-pitfalls.md` |

## 铁律（任何模式都适用）

1. **工作目录**：一切中间产物落在 `.hy3d/`（`jobs.json`、`outputs/`），并 gitignore，绝不散落工作区。
2. **绝不盲目重提**：生成一次 30–40 点。提交前先跑 `check`（离线、不要 token）；失败时按错误分级决定动作（见 workflow.md）。
3. **Token 只走 stdin**：`connect_cloud_service` → `tempToken` → `echo -n "$TOKEN" | python ...`，禁止命令行参数与环境变量明文。
4. **产物必须落地**：下载 GLB + 预览图，生成 viewer.html（相对路径引用 GLB），用 `present_files` 交付；禁止把远程 URL 直接丢给用户。

## 一行起步

```bash
# 1) 登记任务（离线）
python scripts/multiview_3d_driver.py init --front view_front.jpg --back view_back.jpg --left view_left.jpg
# 2) 离线自检（不花点数）
python scripts/multiview_3d_driver.py check .hy3d/jobs.json
# 3) 提交
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py submit .hy3d/jobs.json
# 4) 轮询 + 下载 + 生成预览页
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py collect .hy3d/jobs.json
```

（`run` 可一次走完 check → submit → collect。）

## Resources

- `scripts/multiview_3d_driver.py` — 任务文件驱动：`init` / `check` / `submit` / `collect` / `run`
- `references/workflow.md` — 完整步骤、任务文件结构、错误分级与恢复
- `references/api-params.md` — 参数、视角、费用、校验规则、成本档位
- `references/env-pitfalls.md` — Windows / WorkBuddy Desktop 环境坑
