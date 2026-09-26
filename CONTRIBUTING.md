# 贡献指南

改动这个技能时，请附上**证据**，避免"看起来更合理、实际效果回退"。

## 提交 PR 需要附带

1. **一次真实生成的结果**：至少一张 `preview.png`（或对比拼图），说明输入是单图还是多视角
2. **成本与耗时**：本次 `ResultCreditConsumed`、从 submit 到 DONE 的时长
3. **改动脚本时**：贴出 `check` 的输出（含 `state` / `hints`），证明离线校验仍然工作
4. **改动文档时**：说明改的是哪条路由/哪节手册，以及对应的实测依据

## 原则

- **不要凭想象改 API 契约**：参数、视角、费用以实测 + 官方文档为准；文档里的配方表要标注校验日期
- **不要削弱防护**：`check` 的硬校验、错误分级、`submission-uncertain` 禁止自动重提，都是防止重复付费（30–40 点/次）的关键，删减需说明理由
- **不要把 token/签名链接写进任何文件或日志**：脚本已有 `sanitize()`，新增输出路径要走脱敏
- **产物不入库**：`.hy3d/` 已 gitignore，示例图请放到 `assets/` 并控制体积

## 检查清单（提交前）

- [ ] `python scripts/multiview_3d_driver.py check .hy3d/jobs.json` 正常返回
- [ ] `python scripts/multiview_3d_driver.py --help` 显示的配方仍可执行
- [ ] README 与 `references/` 的命令示例同步更新
- [ ] 无 token、无 COS 签名链接出现在 diff 中
