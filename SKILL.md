---
name: hunyuan-3d-multiview
description: 图生3D手办/模型工作流（腾讯混元 HY-3D）。当用户提供图片（尤其是三视图/多视角图）要求生成 3D 模型、3D 手办、GLB/OBJ 文件时使用。支持单图与多视角（front+back/left/right 等）两种模式，全程 base64 直连、无需外部图床；覆盖裁剪、提交、轮询、下载与 model-viewer 预览页生成的完整流程，并记录了 WorkBuddy Windows 环境的执行坑。
agent_created: true
---

# Hunyuan 3D Multiview（图生3D 多视角工作流）

## Overview

基于腾讯混元 3D（SubmitHunyuanTo3DProJob，经由 WorkBuddy 内置 `buddy-cloud.py` 的代理签名通道）将图片转为 3D 模型。核心价值：**多视角输入可全程走 Base64（`ViewImageBase64` 字段），不需要公网图床**——这是内置脚本不支持的（它只封装了 `ViewImageUrl`）。

## 能力与参数速查

| 项目 | 说明 |
|------|------|
| 主图 | `ImageBase64` 或 `ImageUrl`（即 front 视角），与 Prompt 互斥 |
| 多视角 | `MultiViewImages` 数组，每项 `{ViewType, ViewImageBase64 或 ViewImageUrl}` |
| 3.0 视角 | `left` / `right` / `back` |
| 3.1 视角 | 再加 `top` / `bottom` / `left_front` / `right_front`（八视图） |
| 图片限制 | jpg/png，单边 128~5000px；多视角图片 base64 总和 ≤6MB |
| 费用参考 | Normal 20 点 + MultiView 10 点 + PBR 10 点（3.1 版，2026-09 实测） |
| 生成时长 | 单图约 3.5 分钟，多视角约 6 分钟 |

多视角建议：至少 2 张且必须含 front（front 作主图）；质量要求高再追加 left/right。

## 工作流

### Step 1：准备输入图（本地三视图截图）

- 用 PIL 从整图中裁出各视角（典型三视图：正/后/左各占约 1/3 宽度，裁剪时去掉底部文字标注），存为 jpg（quality 92）
- 建议尺寸：单边 ≥128px、总 base64 ≤6MB

### Step 2：执行生成

优先使用本技能脚本（绕过内置脚本不支持 ViewImageBase64 的限制）：

```bash
echo -n "<token>" | "<python>" <本技能目录>/scripts/multiview_3d_driver.py \
  --front view_front.jpg --back view_back.jpg --left view_left.jpg \
  --model 3.1 --pbr
```

- token 获取：调用 `connect_cloud_service`，用返回的 `tempToken`，通过 stdin 传入，禁止命令行/环境变量明文
- python 使用：`C:/Users/hwdem/.workbuddy/binaries/python/versions/3.13.12/python.exe`（managed 版）
- 输出为 JSON：`job_id`、`status`、`result_files`（含 glb/obj 的 url 与 preview_image_url）
- 脚本内部已轮询（每 5s，最长 600s）；如工具超时中断，用 `buddy-cloud.py status <job_id> --type 3d` 查询，**不要重新提交**
- 同一请求最多重提 1 次

### Step 3：下载与预览（必须）

1. 用 Python（urllib）将 `result_files` 中 glb 与 preview_image_url 下载到工作目录，命名 `multiview_model_<timestamp>.glb` / `_preview.png`
2. 生成 `multiview_model_<timestamp>_viewer.html`：model-viewer 组件（CDN 引入），`src` 用**相对路径**引用同目录 glb，`camera-controls auto-rotate shadow-intensity="1"`，背景浅灰 `#f0f0f0`
3. 调用 `present_files` 传入 viewer.html（+glb、preview.png），由内置预览服务同源托管；禁止 `file://`、禁止自起 http.server

如需对比测试，可将输入各视角图与历史 preview 图以 base64 内嵌进 viewer.html 的对比区。

## 环境坑（Windows / WorkBuddy Desktop 实测）

1. **Git Bash shim 残缺**：沙箱 bash 缺 `tail`、`dirname` 等基础命令——避免用 shell 工具处理文件，改用 Python 一把梭
2. **managed venv 不存在**：`binaries/python/envs/default` 路径无效，直接用 `versions/3.13.12` 的 python
3. **命令行长度限制**：base64 作为 argv 传入会超 Windows 32767 字符限制——必须从文件或 stdin 读入（脚本已处理）
4. **`--token-stdin` 必须显式传**：内置脚本不传该参数时报 TOKEN_NOT_CONFIGURED
5. **内置脚本只能整文件 exec**：`python -c "exec(open(...).read())"` 方式可行但无法改 body；本技能驱动脚本用 importlib 加载模块后直调 `_call_api`/`_poll_job`，可自由构造请求体

## Resources

- `scripts/multiview_3d_driver.py` — 多视角/单图生成驱动（token 走 stdin，图片路径走 argv）
