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

建议先设置本地 token（必须与油猴脚本"设置"里填写的 Token 保持一致）：

可先生成一个随机 token：

```bash
openssl rand -hex 24
```

```bash
export LOCAL_API_TOKEN="change-me-local-token"
uv run python app.py
```

也可以复制环境模板后加载：

```bash
cp .env.example .env
set -a && source .env && set +a
uv run python app.py
```

默认监听：

1. `http://127.0.0.1:5000`
2. 下载目录：`~/Downloads/YouTube`
3. 任务存储：`./.data/jobs.json`（可通过 `JOB_STORE_PATH` 改）

macOS 一键启动（双击文件）：

1. 双击 `start_backend.command`
2. 脚本会自动加载 `.env` 并执行 `uv run python app.py`
3. 终端窗口中按 `Ctrl+C` 停止服务

macOS 按需唤醒（推荐）：

1. 双击 `install_launchagent.command`
2. 脚本会把 `launchd/com.local.yt-downloader.plist` 安装到 `~/Library/LaunchAgents/`
3. 之后油猴脚本首次访问 `http://127.0.0.1:5000` 时会触发 launchd 自动拉起后端
4. 后端在没有任务后会等待 `IDLE_TIMEOUT_SECONDS`，默认 900 秒，然后自动退出
5. 如果你移动了仓库路径，记得同步修改 plist 里的 `WorkingDirectory`

卸载方式：执行 `launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.yt-downloader.plist`

## 4. 安装油猴脚本

1. 安装 Tampermonkey。
2. 新建脚本并粘贴 `youtube_downloader.user.js` 全部内容。
3. 打开任意 YouTube 播放页，标题区域将出现下载按钮。
4. 点击 "设置"，填写 Token（值必须等于 `LOCAL_API_TOKEN`），并设置下载模式/清晰度。
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
2. 脚本重装后忘记 token：重新点 "设置" 填写即可（脚本会保存到浏览器 localStorage）。
3. `yt_dlp_found=false`：先安装 yt-dlp（项目依赖安装后应自动可用）。
4. `ffmpeg_found=false`：确认 ffmpeg 已安装并在 PATH 中。
5. `invalid_or_unsupported_url`：当前只允许 YouTube 域名。
6. `invalid_mode` / `invalid_quality`：检查脚本设置值。

## 7. 合规提示

请仅在符合法律法规、平台条款与版权要求的前提下使用本项目。