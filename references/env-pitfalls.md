# 环境坑（Windows / WorkBuddy Desktop 实测）

## 执行环境

1. **Git Bash shim 残缺**：沙箱 bash 缺 `tail`、`dirname`、`head`，`rm` 走 safe-bin 也会失败。 → 文件操作一律用 Python（`os.remove` / `shutil`），不要用 shell 工具。
2. **managed venv 路径**：`binaries/python/envs/default` 现已存在（2026-09-27 建，含 trimesh/scipy）。生成链脚本对 3.11/3.13 均可；**但打印闸门（print_gate.py 内嵌的 stl-mesh-preflight）必须用系统 Python 3.11**：`C:/Users/hwdem/AppData/Local/Programs/Python/Python311/python.exe`——3.13 在 Windows 上 stat 不一致会报 `INPUT_CHANGED`。
3. **命令行长度限制**：base64 作为 argv 传入会超 Windows 32767 字符。 → 脚本内部读文件编码，绝不把 base64 放命令行。
4. **`--token-stdin` 必须显式传**：内置脚本读取 token 的开关由该参数控制，不传会报 TOKEN_NOT_CONFIGURED。driver 的所有子命令都接受该参数（token 本身只从 stdin 读入，绝不进 argv）。
5. **内置脚本只能整文件 exec**：`python -c "exec(open(...).read())"` 可行但无法改请求体。 → 本技能用 `importlib` 加载模块后直调 `_call_api`，可自由构造 body。
6. **`_call_api` 出错会 `sys.exit(1)`**：它属于 `SystemExit`，脚本已捕获并转成 `submission-uncertain` / `query-error`，不要当成崩溃忽略。
7. **内置脚本会被上游改名**：`buddy-cloud.py` 已改名为 `buddy-multimodal-generation.py`。 → driver 用 `find_builtin_script()` 按候选名探测，最后兜底 `glob` 扫描含 `_PROVIDER_MAP` 且带 `"3d"` 的脚本；可用环境变量 `BUDDY_CLOUD_SCRIPT` 手动指定。
8. **HTML 模板里别用 `str.format`**：viewer 模板含 CSS `body { margin: 0; ... }`，`format(glb=...)` 会 `KeyError: 'margin'`（实测踩坑：GLB 已下载成功、viewer 写入失败，状态卡在 download-error）。 → 用 `str.replace("{glb}", name)` 填占位符。

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
