# YouTube Downloader (Local)

本项目包含两部分：

1. 前端油猴脚本 `youtube_downloader.user.js`
2. 本地后端服务 `app.py`（Flask + queue worker + yt-dlp）

升级能力：

1. 任务历史持久化（重启后可查看）
2. 下载模式支持：`video` / `audio`
3. 清晰度支持：`best` / `1080` / `720` / `480`
4. 任务列表查询与取消
5. 本地 Dashboard：`/dashboard?token=<LOCAL_API_TOKEN>`
6. 启动时自动生成本地 token，并通过 `/auth/bootstrap` 与油猴脚本自动对齐

## 1. AI agent 自动化配置

把下面提示词直接发给具备终端和文件编辑能力的 AI agent，即可让它完成首次配置。

### 目标

让 agent 完成以下事项：

1. 检查 macOS 是否已安装 `uv`、Python 3.11、`ffmpeg`
2. 缺失时给出最小安装动作，优先使用 Homebrew
3. 在仓库根目录执行 `uv sync`
4. 启动后端并验证 `http://127.0.0.1:5050/health`
5. 若后端启动失败，优先排查依赖、端口、Python 版本问题
6. 最后总结：哪些已完成，哪些需要我手工处理

### 可直接使用的提示词

```text
你现在在 YT_Downloader 项目根目录中工作，请自动完成本项目的首次配置，但要遵守以下约束：

1. 先检查是否存在 uv、Python 3.11、ffmpeg。
2. 如果缺失，先检查 Homebrew 是否可用；可用时使用最小必要命令安装。
3. 不要修改业务代码，除非是为了修复明显的本地配置路径问题。
4. 优先使用项目已有的 pyproject.toml 和 uv，同步依赖时运行 uv sync。
5. 启动后端前，先阅读 config.py 和 README.md 的相关配置说明。
6. 使用 uv run python app.py 启动后端，并验证 /health 是否返回正常 JSON。
7. 如果需要长期后台运行，再检查 install_launchagent.command 和 launchd/com.local.yt-downloader.plist 是否与当前仓库路径一致。
8. 如果发现 plist 中的 WorkingDirectory 或脚本路径与当前机器路径不一致，修改为当前仓库绝对路径。
9. 配置结束后，输出一份简明结果，包含：
  - 已执行命令
  - 健康检查结果
  - 是否还需要手工安装 Tampermonkey 并导入 youtube_downloader.user.js
  - 是否建议使用 launchd 常驻/按需唤醒

执行时保持最小改动，并在关键步骤后验证结果。
```

### 可选附加检查

1. 运行 `curl http://127.0.0.1:5050/health`，确认 `yt_dlp_found` 与 `ffmpeg_found`
2. 检查 `start_backend.command` 是否能在当前目录正常启动
3. 检查 `install_launchagent.command` 安装后的 plist 路径是否正确
4. 运行按钮烟雾测试，确认脚本最基本渲染能力

## 2. Manual 手动配置

不使用 AI agent 时，按下面顺序手动完成即可。

### 第 1 步：准备系统依赖

需要先安装：

1. Python 3.11（推荐 3.11.9）
2. `uv`（用于创建隔离环境和管理依赖）
3. `ffmpeg`（系统可执行命令）

macOS 示例：

```bash
brew install uv ffmpeg
uv python install 3.11
```

已安装 Python 3.11 可跳过最后一条。

### 第 2 步：安装项目依赖

本仓库已包含 `pyproject.toml`，不需要执行 `uv init`。

在仓库根目录运行：

```bash
uv sync
```

如果要显式指定 Python 3.11，也可以用：

```bash
uv venv --python 3.11 .venv
uv sync
```

### 第 3 步：确认后端配置策略

后端配置主要在 `config.py`。`.env` 仍兼容，但不是主要入口。

默认不需要手动设置 `LOCAL_API_TOKEN`。后端启动后会自动生成 token，油猴脚本会通过 `/auth/bootstrap` 自动对齐。

如果要固定 token 便于调试，可在 `config.py` 中修改，或继续用 `.env` 覆盖。

默认配置重点：

1. 监听地址：`http://127.0.0.1:5050`，除非你在 `config.py` 里改了 `BACKEND_PORT`
2. 下载目录：`~/Downloads/YouTube`
3. 任务存储：`./.data/jobs.json`（可通过 `JOB_STORE_PATH` 改）

### 第 4 步：启动一次后端并检查健康状态

先在仓库根目录运行：

```bash
uv run python app.py
```

看到服务启动后，另开一个终端检查：

```bash
curl http://127.0.0.1:5050/health
```

如果返回 JSON，且包含 `status`、`yt_dlp_found`、`ffmpeg_found`，说明后端已可用。

### 第 5 步：安装油猴脚本

1. 安装 Tampermonkey。
2. 新建脚本并粘贴 `youtube_downloader.user.js` 全部内容。
3. 打开任意 YouTube 播放页，标题区域应出现下载按钮。
4. 点击“设置”，脚本会自动从后端对齐 Token。
5. 点击“面板”可打开本地任务监控页。

### 第 6 步：选择你的启动方式

首次配置完成后，建议在下面两种方式中选一种：

macOS 一键启动（双击文件）：

1. 双击 `start_backend.command`
2. 脚本会自动加载 `.env` 并执行 `uv run python app.py`
3. 终端窗口中按 `Ctrl+C` 停止服务

macOS 按需唤醒（推荐）：

1. 双击 `install_launchagent.command`
2. 脚本会把 `launchd/com.local.yt-downloader.plist` 安装到 `~/Library/LaunchAgents/`
3. 之后油猴脚本会自动探测本机可用端口并触发 launchd 拉起后端
4. 后端在没有任务后会等待 `IDLE_TIMEOUT_SECONDS`，默认 900 秒，然后自动退出
5. 如果你移动了仓库路径，记得同步修改 plist 里的 `WorkingDirectory`

卸载：执行 `launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.yt-downloader.plist`

## 3. API

### `POST /jobs`

请求头：

1. `Content-Type: application/json`
2. `X-Local-Token: <your_token>`

请求体：

```json
{
  "url": "https://www.youtube.com/watch?v=xxxx",
  "title": "optional",
  "mode": "video",
  "quality": "best"
}
```

参数约束：

1. `mode`: `video` 或 `audio`
2. `quality`: `best` / `1080` / `720` / `480`

### `GET /jobs`

请求头：

1. `X-Local-Token: <your_token>`

查询参数：

1. `limit`（可选，默认 50，最大 200）

### `GET /jobs/<job_id>`

请求头：

1. `X-Local-Token: <your_token>`

### `POST /jobs/<job_id>/cancel`

请求头：

1. `X-Local-Token: <your_token>`

### `GET /health`

用于检查后端、yt-dlp、ffmpeg 是否可用。

### `GET /dashboard?token=<LOCAL_API_TOKEN>`

浏览器任务监控页面（仅本地）。

## 4. 常见问题

1. `unauthorized`：检查 token 是否一致。
2. 脚本重装后或后端重启：重新点 "设置" 即可自动重新对齐（脚本会保存到浏览器 localStorage）。
3. `yt_dlp_found=false`：先安装 yt-dlp（项目依赖安装后应自动可用）。
4. `ffmpeg_found=false`：确认 ffmpeg 已安装并在 PATH 中。
5. `invalid_or_unsupported_url`：当前只允许 YouTube 域名。
6. `invalid_mode` / `invalid_quality`：检查脚本设置值。

## 5. 合规提示

请仅在符合法律法规、平台条款与版权要求的前提下使用本项目。

## 6. 自动化按钮渲染测试

本仓库提供一个 Playwright 烟雾测试，用来检查脚本是否能在 YouTube watch 页渲染按钮。

先安装依赖：

```bash
uv sync
```

默认运行离线 fixture，验证按钮是否能在延迟出现的 watch DOM 上成功渲染：

```bash
uv run python tools/youtube_button_smoke_test.py
```

如果要直接测真实 YouTube 页面：

```bash
uv run python tools/youtube_button_smoke_test.py --mode live
```

可选参数：

1. `--url`：指定真实视频地址
2. `--headed`：显示浏览器窗口，便于观察页面状态
3. `CHROME_PATH`：手动指定 Chrome 可执行文件路径

脚本会自动优先使用 macOS 上已安装的 Google Chrome；如果没有找到，会提示下一步怎么处理。