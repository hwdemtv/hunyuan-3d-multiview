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
- 质量检查：单体、纯色背景、主体占画面 >50%、无文字；分辨率单边 128~5000px
- 存到 `.hy3d/inputs/`，然后登记：

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
| `download-error` | 结果已生成但下载失败 | 只重下（用 jobs.json 里保存的 url），**绝不重新生成** |
| URL 过期 | COS 签名 24h 失效 | 重新 `collect` 取新签名链接 |

## 7. 退出条件

- 单图约 3.5 分钟、多视角约 6 分钟；超过 600s 仍未 DONE：保留 job_id，隔几分钟再 `collect`，不要重提
- 同一需求最多允许 1 次主动重新生成；第 2 次必须先向用户说明点数成本
