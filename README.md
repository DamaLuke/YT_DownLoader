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

## 1. 环境要求

1. Python 3.11（推荐 3.11.9）
2. `uv`（用于创建隔离环境和管理依赖）
3. `ffmpeg`（系统可执行命令）

macOS 安装 ffmpeg 示例：

```bash
brew install ffmpeg
```

## 2. 使用 uv 创建隔离环境

本仓库已经包含 `pyproject.toml`，通常不需要再次 `uv init`。

推荐方式（当前仓库）：

```bash
uv python install 3.11
uv sync
```

如果你是在新目录从零创建项目，可使用：

```bash
uv init --python 3.11
uv sync
```

如果你不想重新初始化项目，也可以使用手动方式：

```bash
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

## 3. 启动后端

后端配置现在集中在 `config.py`，`app.py` 会直接导入这些变量。`.env` 仍然会被兼容读取，但它不再是主要的配置入口。

默认情况下不需要手动设置 `LOCAL_API_TOKEN`，后端启动后会自动生成随机 token，油猴脚本会通过 `/auth/bootstrap` 自动对齐。

如果你想固定 token 便于调试，也可以在 `config.py` 对应项里改，或者继续用 `.env` 覆盖。

默认监听：

1. `http://127.0.0.1:5050`，除非你在 `config.py` 里改了 `BACKEND_PORT`
2. 下载目录：`~/Downloads/YouTube`
3. 任务存储：`./.data/jobs.json`（可通过 `JOB_STORE_PATH` 改）

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

卸载方式：执行 `launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.yt-downloader.plist`

## 4. 安装油猴脚本

1. 安装 Tampermonkey。
2. 新建脚本并粘贴 `youtube_downloader.user.js` 全部内容。
3. 打开任意 YouTube 播放页，标题区域将出现下载按钮。
4. 点击 "设置"，脚本会自动从后端对齐 Token；如后端使用固定 `LOCAL_API_TOKEN`，脚本也会自动同步。
5. 使用 "面板" 按钮可打开本地任务监控页。

## 5. API

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

## 6. 常见问题

1. `unauthorized`：检查 token 是否一致。
2. 脚本重装后或后端重启：重新点 "设置" 即可自动重新对齐（脚本会保存到浏览器 localStorage）。
3. `yt_dlp_found=false`：先安装 yt-dlp（项目依赖安装后应自动可用）。
4. `ffmpeg_found=false`：确认 ffmpeg 已安装并在 PATH 中。
5. `invalid_or_unsupported_url`：当前只允许 YouTube 域名。
6. `invalid_mode` / `invalid_quality`：检查脚本设置值。

## 7. 合规提示

请仅在符合法律法规、平台条款与版权要求的前提下使用本项目。

## 8. 自动化按钮渲染测试

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