# Workflow（多视角 / 单图通用）

## 0. 工作目录

一律在 `.hy3d/`（gitignore）：

```
.hy3d/
├── jobs.json            # 任务状态（唯一事实来源）
├── jobs.json.lock       # 并发锁（脚本自动创建/删除）
├── inputs/              # 裁剪后的视角图
└── outputs/             # glb / preview.png / viewer.html
```

不要对同一个 `jobs.json` 并行执行两条命令（脚本有锁会直接报错）。

## 1. 准备视角图

- 从整张三视图按 1/3 宽度裁出 front / back / left（去掉底部文字标注），jpg quality 92
- **再做一次标准化裁剪（实测有效，见 `api-params.md` 的 A/B）**：取主体 bbox 裁掉空白 → 贴回白色正方形画布 → 主体较长边占 70% 且居中
- 质量检查（清单见 `api-params.md`）：单体、纯色背景、主体占画面 >50%、无文字、三视图比例一致
- 脚本的 `check` 会给出 `hints`（背景是否纯色、主体占比），属软提示，按提示优化即可
- 存到 `.hy3d/inputs/`，然后登记：

标准化裁剪核心片段（Pillow）：

```python
from PIL import Image
TARGET_RATIO = 0.70          # 主体较长边占画布比例

bg = 235                      # min(R,G,B) >= 235 视为背景
im = Image.open(src).convert("RGB")
w, h = im.size
px = im.resize((64, 64)).load()
xs = [x for x in range(64) for y in range(64) if min(px[x, y]) < bg]
ys = [y for y in range(64) for x in range(64) if min(px[x, y]) < bg]
subj = im.crop((min(xs) * w // 64, min(ys) * h // 64,
                (max(xs) + 1) * w // 64, (max(ys) + 1) * h // 64))

sw, sh = subj.size
side = min(max(int(max(sw, sh) / TARGET_RATIO), 512), 1024)
canvas = Image.new("RGB", (side, side), "white")
scale = min(side * TARGET_RATIO / sw, side * TARGET_RATIO / sh)
subj = subj.resize((int(sw * scale), int(sh * scale)), Image.LANCZOS)
canvas.paste(subj, ((side - subj.width) // 2, (side - subj.height) // 2))
canvas.save(dst, quality=92)
```

```bash
python scripts/multiview_3d_driver.py init \
  --front .hy3d/inputs/front.jpg --back .hy3d/inputs/back.jpg --left .hy3d/inputs/left.jpg \
  --jobs-file .hy3d/jobs.json
```

## 2. 离线自检（必做，不花点数）

```bash
python scripts/multiview_3d_driver.py check .hy3d/jobs.json
```

输出每个任务的状态：`ready` / `waiting-for-image` / `invalid-image` / `invalid-params` / `submitted` / `submission-uncertain`。
出现 `invalid-*` 退出码为 1，**修好再提交**。

## 3. 提交

```bash
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py submit .hy3d/jobs.json
```

- 只有 `ready` 状态的任务会被提交；已提交任务自动跳过（幂等）
- 提交成功后 `jobs.json` 记录 `job_id`、`submitted_at`

## 4. 轮询 / 收集

```bash
echo -n "<tempToken>" | python scripts/multiview_3d_driver.py collect .hy3d/jobs.json --download
```

- 每 10s 查询一次状态，最长 600s；`--no-poll` 只查一次
- DONE 时把 `result_files` 写入 jobs.json，并下载 GLB/预览图到 `.hy3d/outputs/`，自动生成同目录 `*_viewer.html`（相对路径引用 GLB）
- 之后用 `present_files` 打开 viewer.html 交付；不要自起 http.server

## 5. jobs.json 结构

```json
{
  "jobs": [
    {
      "id": "figure-01",
      "views": { "front": ".hy3d/inputs/front.jpg", "back": ".hy3d/inputs/back.jpg" },
      "model": "3.1",
      "pbr": true,
      "face_count": 500000,
      "generate_type": "Normal",
      "state": "submitted",
      "job_id": "1490917023424708608",
      "submitted_at": "2026-09-26T23:20:11",
      "result_files": [],
      "downloaded": { "glb": ".hy3d/outputs/figure-01_20260926_232500.glb" }
    }
  ]
}
```

`collect` 可反复执行：已 `downloaded` 的任务会被跳过。

## 6. 错误分级与恢复（照此执行，不要凭感觉重试）

| error_stage | 含义 | 恢复动作 |
|---|---|---|
| `waiting-for-image` / `invalid-image` / `invalid-params` | 本地输入有问题 | 修输入，重跑 `check` → `submit`。**未产生费用** |
| `rejected` | 服务端在返回 job id 前就拒绝 | 读 sanitize 后的报错，改 endpoint/参数/凭证后再提交 |
| `submission-uncertain` / `submitting` | 是否已受理未知 | **禁止自动重提**。先 `collect` 核对；确需重提须显式 `--force-retry`，并接受可能二次计费 |
| `query-error` | 查状态失败（网络/API） | 原样重试 `collect`，job_id 不变 |
| `result-error` | 任务结束但取结果失败或为空 | 用同一 job_id 重跑 `collect`；仍失败才考虑新建任务（新 id） |
| `download-error` | 结果已生成但下载失败 | 只重下（用 jobs.json 里保存的 url），**绝不重新生成**。已落盘的产物会被 `collect` 自动复用（幂等），补齐缺失的 viewer；确要整批重下加 `--force-download`（免费，不产生新计费） |
| URL 过期 | COS 签名 24h 失效 | 重新 `collect` 取新签名链接 |

## 7. 退出条件

- 单图约 3.5 分钟、多视角约 6 分钟；超过 600s 仍未 DONE：保留 job_id，隔几分钟再 `collect`，不要重提
- 同一需求最多允许 1 次主动重新生成；第 2 次必须先向用户说明点数成本

## 8. 生成之后的打印链路（可选，要出实体件时）

GLB 交付不是终点。混元产物直接切片大概率翻车（UV 接缝拆点假开放边、单位、破洞），走 **打印闸门** 一条命令闭环：体检 → 自动修复（L1 轻修复保细节 / L2 体素保水密）→ 复检 → 报告。

**闸门脚本在本技能 `scripts/print/` 里**（2026-09-27 合并，原 ai-3d-blender-pipeline）：

```bash
# 用 jobs.json 里 downloaded.glb 的实际路径；全程数分钟，放后台跑
"C:/Users/hwdem/AppData/Local/Programs/Python/Python311/python.exe" \
  "C:/Users/hwdem/.workbuddy/skills/hunyuan-3d-multiview/scripts/print/print_gate.py" \
  --workspace <项目绝对路径>/.hy3d/gate \
  --glb .hy3d/outputs/figure-01_xxx.glb --height 180 --autofix
```

判定与产物：`PASS/NEEDS_REVIEW/FAIL` + `.hy3d/gate/gate-out/gate-report.{json,md}`（各级前后对比表）。PASS 时 `final_file` 即可切片的 STL。

### 路由决策（生成侧问题 vs 打印侧问题）

| 现象 | 归属 | 动作 |
|---|---|---|
| 融合手指、五官穿插、悬空发束、左右不对称 | **生成侧** | 回第 1 步改输入/重新生成（闸门修不了解剖学） |
| 破洞、非流形、单位、原点、碎块 | **打印侧** | `--autofix` 闭环处理（L1→L2） |
| L2 体素后仍 FAIL | 双方都难救 | 隔离在 `failed/`，人工回 Blender 或重生成 |
| 壁厚/悬垂数值 | 切片软件 | 本链不判，dfam-check 可出测量值 |

### 必须记住的坑

- **解释器必须是 Python 3.11**（上面的绝对路径就是）；3.13 跑内嵌的 stl-mesh-preflight 会报 `INPUT_CHANGED`
- 预检硬限 3 万面/16MB，大模型走降面副本——**副本结论 ≠ 原件结论**，PASS 后可用 trimesh 对原件做秒级全量复核（`mesh.is_watertight`）
- L2 体素会钝化细节、丢内部结构——PASS 带 caveat，上架前人工过目
- 完整规则见 SKILL.md 段C 与 `references/print/pitfalls.md`、`references/print/inspection-prompt-mcp.md`（16+1 节视觉体检清单）
