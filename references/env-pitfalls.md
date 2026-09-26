# 环境坑（Windows / WorkBuddy Desktop 实测）

## 执行环境

1. **Git Bash shim 残缺**：沙箱 bash 缺 `tail`、`dirname`、`head`，`rm` 走 safe-bin 也会失败。 → 文件操作一律用 Python（`os.remove` / `shutil`），不要用 shell 工具。
2. **managed venv 路径不存在**：`binaries/python/envs/default` 无效。 → 用 `C:/Users/hwdem/.workbuddy/binaries/python/versions/3.13.12/python.exe`。
3. **命令行长度限制**：base64 作为 argv 传入会超 Windows 32767 字符。 → 脚本内部读文件编码，绝不把 base64 放命令行。
4. **`--token-stdin` 必须显式传**：内置 `buddy-cloud.py` 不传该参数会报 TOKEN_NOT_CONFIGURED。
5. **内置脚本只能整文件 exec**：`python -c "exec(open(...).read())"` 可行但无法改请求体。 → 本技能用 `importlib` 加载模块后直调 `_call_api`，可自由构造 body。
6. **`_call_api` 出错会 `sys.exit(1)`**：它属于 `SystemExit`，脚本已捕获并转成 `submission-uncertain` / `query-error`，不要当成崩溃忽略。

## 内置脚本关键符号（供调试）

- `mod._PROVIDER_MAP["3d"]` → `provider` / `service` / `version` / `submit_action` / `query_action`
- `mod._DEFAULT_ENDPOINT` → `https://copilot.tencent.com/agenttool/v1/tcproxy`
- `mod._ACTIVE_TOKEN` → 设置后输出自动脱敏
- 状态值：`WAIT` → `RUN` → `DONE` / `FAIL`

## 交付预览

- viewer.html 的 `<model-viewer src>` 必须相对路径引用同目录 GLB，由 `present_files` 同源托管
- 禁止 `file://`（fetch 跨域失败）、禁止自起 `python -m http.server` / `npx serve`（会绑 0.0.0.0 暴露局域网）

## 其他

- COS 结果链接带签名，有效期 24h；过期重新 `collect` 取新链接
- GLB 通常 40–60MB，不要 base64 内联进 HTML
