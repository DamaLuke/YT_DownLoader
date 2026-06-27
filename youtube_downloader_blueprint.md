# YouTube 高清下载系统 - 2026 升级版技术蓝图

本系统继续采用 "油猴前端 + 本地后端 + yt-dlp/ffmpeg" 架构，但升级为可观测、可恢复、可并发控制的本地任务系统。

目标是从 "能下载" 升级到 "长期可用且稳定"。

---

## 一、 2026 可行性结论

以下技术在 2026 年仍然可行：

1. Tampermonkey Userscript 在 YouTube 页面注入按钮。
2. `GM_xmlhttpRequest` 访问 `127.0.0.1` 本地 API。
3. Python 调用 `yt-dlp` + `ffmpeg` 进行音视频抓取和合并。
4. 本地文件保存到系统下载目录。

但需要增加以下工程化能力：

1. 任务队列与状态查询，避免前端盲等。
2. URL 白名单与 token 校验，降低本地接口被滥用风险。
3. 可观测日志与失败重试，提升长期可维护性。

---

## 二、 升级后系统架构

### 1. 架构组件

1. 前端 Userscript：注入下载入口、提交任务、轮询进度。
2. 本地 API 服务（Flask/FastAPI 均可，本文按 Flask）：接收任务、排队执行、暴露状态。
3. Worker 执行器：串行或限流并发执行 `yt-dlp`。
4. 媒体引擎：`yt-dlp` 负责获取流，`ffmpeg` 负责混流。
5. 任务存储：内存字典 + 可选 JSON 持久化（轻量场景）。

### 2. 数据流

1. 用户点击下载。
2. 前端调用 `POST /jobs`，提交 `url`、`title`。
3. 服务端返回 `job_id`。
4. 前端轮询 `GET /jobs/{job_id}`。
5. Worker 执行下载并持续更新状态。
6. 完成后返回本地文件路径和结果信息。

---

## 三、 前端 Userscript 规范 (2026)

### 1. 元数据与权限

1. `@match`: `https://www.youtube.com/watch*`
2. `@grant`: `GM_xmlhttpRequest`
3. `@connect`: `127.0.0.1`

### 2. SPA 兼容策略

YouTube 为 SPA，不可只依赖首次加载。

必须组合使用：

1. `MutationObserver` 监听关键容器出现。
2. URL 变化检测（定时或 history hook）防止切换视频后按钮丢失。
3. 幂等注入逻辑，确保同一页面只存在一个按钮实例。

### 3. 按钮状态机

建议状态：

1. `idle`: 可点击，显示 "本地最高画质下载"。
2. `submitting`: 显示 "正在提交任务"，禁用按钮。
3. `queued`/`running`: 显示 "后台下载中 xx%"（若可拿到进度）。
4. `success`: 显示 "下载完成"，短暂提示后回到 `idle`。
5. `error`: 显示错误摘要，允许重试。

### 4. 前端容错

1. API 超时（如 10 秒）后给出重试提示。
2. 区分 "服务未启动" 与 "任务失败" 两类错误。
3. 轮询间隔建议 1.0 到 1.5 秒，完成后立即停止轮询。

---

## 四、 后端 API 规范 (2026)

### 1. 运行与安全基线

1. 仅监听 `127.0.0.1`，禁止 `0.0.0.0`。
2. CORS 仅允许 `https://www.youtube.com`。
3. 请求头增加本地 token（例如 `X-Local-Token`）。
4. URL 严格白名单：仅允许 `youtube.com`、`www.youtube.com`、`youtu.be`。

### 2. API 设计

1. `POST /jobs`
2. `GET /jobs/<job_id>`
3. `GET /health`

`POST /jobs` 返回示例：

```json
{
  "job_id": "a1b2c3",
  "status": "queued"
}
```

`GET /jobs/<job_id>` 返回示例：

```json
{
  "job_id": "a1b2c3",
  "status": "running",
  "progress": 62,
  "message": "downloading",
  "output": null,
  "error": null
}
```

状态枚举建议：`queued`, `running`, `merging`, `completed`, `failed`, `canceled`。

### 3. Worker 与并发控制

1. 不在请求线程里直接跑下载。
2. 使用 `queue.Queue` + 常驻 worker 线程。
3. 默认并发 `1`，高级模式可允许 `2`，防止带宽抢占与磁盘抖动。
4. 每个 job 存储开始时间、结束时间、错误摘要。

---

## 五、 yt-dlp 与 ffmpeg 调用规范

### 1. 命令参数建议

1. 格式：`-f "bv*+ba/b"`
2. 容器：`--merge-output-format mp4`
3. 路径模板：`%(title).180B [%(id)s].%(ext)s`
4. 输出目录：系统下载目录下专用子目录（例如 `Downloads/YouTube`）

### 2. 安全调用

1. 必须使用参数列表调用 subprocess，禁止拼接 shell 字符串。
2. 对标题做文件名清洗（移除非法字符、控制长度）。
3. 捕获返回码、stderr，并回写到 job 错误字段。

### 3. 稳定性建议

1. `yt-dlp` 定期更新（例如每周检测一次）。
2. 下载失败可重试 1 次（仅网络错误类型）。
3. 对常见失败分类：网络中断、受限视频、版权限制、提取器失效。

---

## 六、 依赖与版本要求（升级）

### 1. Python 版本（明确）

1. 后端统一使用 Python 3.11。
2. 推荐固定到 Python 3.11.9（兼容性与稳定性更好）。
3. 不建议继续使用 3.8/3.9 作为新项目基线。

### 2. 包与工具版本

1. Flask: 3.x
2. flask-cors: 最新稳定版
3. yt-dlp: 最新稳定版
4. ffmpeg: 6.x 或更新版本
5. uv: 最新稳定版（用于虚拟环境与依赖管理）

### 3. 使用 uv 管理隔离环境（必选）

建议在 README 中提供以下标准流程：

1. 安装 uv。
2. 在项目根目录创建并固定 Python 3.11 虚拟环境。
3. 通过 uv 安装依赖并生成锁文件。
4. 使用 uv 运行后端服务，避免系统 Python 污染。

参考命令（macOS/Linux）：

```bash
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -U flask flask-cors yt-dlp
uv pip freeze > requirements.lock.txt
uv run python app.py
```

建议补充：

1. 保留 `requirements.txt`（人类可读）和 `requirements.lock.txt`（可复现安装）。
2. `README` 写明 macOS/Windows/Linux 的 ffmpeg 安装方式。

---

## 七、 可观测性与日志

最小日志字段：

1. `timestamp`
2. `job_id`
3. `phase`（submit/download/merge/done/error）
4. `message`
5. `exit_code`（失败时）

建议保存最近 200 条任务元信息，方便追踪问题。

---

## 八、 合规与边界

技术可行不等于任何场景都可用。

请在项目文档中明确：

1. 用户需自行确保下载行为符合当地法律法规。
2. 用户需遵守平台条款与版权要求。

---

## 九、 给 AI 助手的升级版 Prompt

> 请根据本蓝图生成两个完整文件：
> 1) `youtube_downloader.user.js`：支持 YouTube SPA 注入、按钮状态机、提交任务与轮询任务状态。
> 2) `app.py`：基于 Flask，实现 `POST /jobs`、`GET /jobs/<job_id>`、`GET /health`，采用 queue + worker 模型、安全调用 yt-dlp、严格 URL 白名单、仅监听 127.0.0.1，并返回结构化任务状态。
>
> 额外要求：
> - 使用 `subprocess` 参数列表调用，不得使用 shell 拼接。
> - 提供错误分类与日志输出。
> - 默认输出到 `~/Downloads/YouTube`。
> - 后端固定 Python 3.11，并使用 uv 创建和管理 `.venv` 隔离环境。
> - 代码可直接运行，并附带最小 README。
