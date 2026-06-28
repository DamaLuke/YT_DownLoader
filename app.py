#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import secrets
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import time

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.serving import make_server


load_dotenv()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


APP = Flask(__name__)
CORS(
    APP,
    resources={
        r"/auth/bootstrap": {
            "origins": [
                "https://www.youtube.com",
                "https://m.youtube.com",
            ]
        },
        r"/jobs*": {
            "origins": [
                "https://www.youtube.com",
                "https://m.youtube.com",
            ]
        },
        r"/health": {
            "origins": [
                "https://www.youtube.com",
                "https://m.youtube.com",
            ]
        },
    },
)

LOCAL_TOKEN_PLACEHOLDER = "change-me-local-token"


def generate_local_token() -> str:
    token = os.getenv("LOCAL_API_TOKEN", "").strip()
    if token and token != LOCAL_TOKEN_PLACEHOLDER:
        return token
    return secrets.token_urlsafe(32)


LOCAL_TOKEN = generate_local_token()
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "1"))
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "5050"))
IDLE_TIMEOUT_SECONDS = int(os.getenv("IDLE_TIMEOUT_SECONDS", "900"))
IDLE_CHECK_INTERVAL_SECONDS = int(os.getenv("IDLE_CHECK_INTERVAL_SECONDS", "15"))
LAUNCHD_SOCKET_NAME = os.getenv("LAUNCHD_SOCKET_NAME", "Listeners")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "~/Downloads/YouTube")).expanduser().resolve()
JOB_STORE_PATH = Path(os.getenv("JOB_STORE_PATH", "./.data/jobs.json")).expanduser().resolve()
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()
YTDLP_USER_AGENT = os.getenv("YTDLP_USER_AGENT", "").strip()
YTDLP_ACCEPT_LANGUAGE = os.getenv("YTDLP_ACCEPT_LANGUAGE", "").strip()
YTDLP_REFERER = os.getenv("YTDLP_REFERER", "").strip()
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github").strip()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}

JOB_QUEUE: "queue.Queue[str]" = queue.Queue()
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
ACTIVE_PROCESSES: dict[str, subprocess.Popen[str]] = {}
ACTIVE_PROCESSES_LOCK = threading.Lock()
LAST_ACTIVITY_MONOTONIC = time.monotonic()
LAST_ACTIVITY_LOCK = threading.Lock()
IDLE_WATCHDOG_STOP = threading.Event()


def touch_activity() -> None:
    global LAST_ACTIVITY_MONOTONIC
    with LAST_ACTIVITY_LOCK:
        LAST_ACTIVITY_MONOTONIC = time.monotonic()


def seconds_since_last_activity() -> float:
    with LAST_ACTIVITY_LOCK:
        return time.monotonic() - LAST_ACTIVITY_MONOTONIC


def has_active_work() -> bool:
    with JOBS_LOCK:
        if any(job.get("status") in {"queued", "running", "merging"} for job in JOBS.values()):
            return True

    with ACTIVE_PROCESSES_LOCK:
        if ACTIVE_PROCESSES:
            return True

    return not JOB_QUEUE.empty()


def idle_watchdog_loop() -> None:
    while not IDLE_WATCHDOG_STOP.wait(IDLE_CHECK_INTERVAL_SECONDS):
        if has_active_work():
            continue

        if seconds_since_last_activity() < IDLE_TIMEOUT_SECONDS:
            continue

        try:
            save_jobs_to_disk()
        except Exception:
            pass

        os.kill(os.getpid(), signal.SIGTERM)
        return


def start_idle_watchdog() -> None:
    thread = threading.Thread(target=idle_watchdog_loop, name="idle-watchdog", daemon=True)
    thread.start()


def launchd_socket_fd(socket_name: str) -> int | None:
    try:
        libc = ctypes.CDLL(None)
        launch_activate_socket = libc.launch_activate_socket
    except AttributeError:
        return None

    launch_activate_socket.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    launch_activate_socket.restype = ctypes.c_int

    fds_ptr = ctypes.POINTER(ctypes.c_int)()
    fds_count = ctypes.c_size_t()
    error_code = launch_activate_socket(socket_name.encode("utf-8"), ctypes.byref(fds_ptr), ctypes.byref(fds_count))
    if error_code != 0 or fds_count.value == 0:
        return None

    try:
        sockets = [fds_ptr[index] for index in range(fds_count.value)]
    finally:
        try:
            libc.free.argtypes = [ctypes.c_void_p]
            libc.free.restype = None
            libc.free(fds_ptr)
        except Exception:
            pass

    return sockets[0]


def create_server() -> Any:
    launchd_fd = launchd_socket_fd(LAUNCHD_SOCKET_NAME)
    if launchd_fd is not None:
        print(f"Using launchd socket activation on {BACKEND_HOST}:{BACKEND_PORT}")
        return make_server(BACKEND_HOST, BACKEND_PORT, APP, threaded=True, fd=launchd_fd)

    print(f"Using direct bind on http://{BACKEND_HOST}:{BACKEND_PORT}")
    return make_server(BACKEND_HOST, BACKEND_PORT, APP, threaded=True)


def save_jobs_to_disk() -> None:
    with JOBS_LOCK:
        payload = {
            "version": 1,
            "jobs": list(JOBS.values()),
        }
    JOB_STORE_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def load_jobs_from_disk() -> None:
    if not JOB_STORE_PATH.exists():
        return

    try:
        payload = json.loads(JOB_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return

    loaded_count = 0
    with JOBS_LOCK:
        for job in jobs:
            if not isinstance(job, dict) or "job_id" not in job:
                continue
            state = job.get("status")
            if state in {"queued", "running", "merging"}:
                job["status"] = "failed"
                job["message"] = "stale_job_after_restart"
                job["error"] = {
                    "type": "stale_job",
                    "detail": "Job was interrupted by backend restart",
                }
                job["ended_at"] = now_iso()
            JOBS[job["job_id"]] = job
            loaded_count += 1

    if loaded_count > 0:
        save_jobs_to_disk()


def is_allowed_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_HOSTS


def normalize_youtube_video_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return None

    video_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query)
        video_id = (query.get("v") or [""])[0].strip()
    elif host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0].strip()

    if not video_id:
        return None

    return f"https://www.youtube.com/watch?v={video_id}"


def token_ok(req: Any) -> bool:
    return req.headers.get("X-Local-Token", "") == LOCAL_TOKEN


def dashboard_token_ok(req: Any) -> bool:
    return req.args.get("token", "") == LOCAL_TOKEN


def job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "url": job["url"],
        "title": job["title"],
        "mode": job["mode"],
        "quality": job["quality"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "output": job["output"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "ended_at": job["ended_at"],
    }


def update_job(job_id: str, **fields: Any) -> None:
    with JOBS_LOCK:
        if job_id not in JOBS:
            return
        JOBS[job_id].update(fields)
    save_jobs_to_disk()
    touch_activity()


def classify_error(stderr_text: str) -> str:
    lowered = stderr_text.lower()
    if "sign in to confirm you" in lowered or "not a bot" in lowered:
        return "auth_required"
    if "cookies-from-browser" in lowered or "use --cookies" in lowered:
        return "cookies_required"
    if "http error 429" in lowered or "too many requests" in lowered:
        return "rate_limited"
    if "private video" in lowered or "members-only" in lowered:
        return "access_restricted"
    if "copyright" in lowered:
        return "copyright_restricted"
    if "unable to extract" in lowered or "extractor" in lowered:
        return "extractor_broken"
    if "timed out" in lowered or "network" in lowered:
        return "network_error"
    return "unknown_error"


def resolve_format(mode: str, quality: str) -> tuple[str, str]:
    normalized_mode = (mode or "video").strip().lower()
    normalized_quality = (quality or "best").strip().lower()

    if normalized_mode not in {"video", "audio"}:
        normalized_mode = "video"

    if normalized_mode == "audio":
        if normalized_quality not in {"best", "320", "256", "128"}:
            normalized_quality = "best"
        return "ba/bestaudio", normalized_quality

    if normalized_quality not in {"best", "1080", "720", "480", "360", "240", "144"}:
        normalized_quality = "best"

    if normalized_quality == "1080":
        return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/bv*+ba/b", normalized_quality
    if normalized_quality == "720":
        return "bestvideo[height<=720]+bestaudio/best[height<=720]/bv*+ba/b", normalized_quality
    if normalized_quality == "480":
        return "bestvideo[height<=480]+bestaudio/best[height<=480]/bv*+ba/b", normalized_quality
    if normalized_quality == "360":
        return "bestvideo[height<=360]+bestaudio/best[height<=360]/bv*+ba/b", normalized_quality
    if normalized_quality == "240":
        return "bestvideo[height<=240]+bestaudio/best[height<=240]/bv*+ba/b", normalized_quality
    if normalized_quality == "144":
        return "bestvideo[height<=144]+bestaudio/best[height<=144]/bv*+ba/b", normalized_quality
    return "bv*+ba/b", normalized_quality


def yt_dlp_cmd_prefix() -> list[str]:
    ytdlp_bin = shutil_which("yt-dlp")
    if ytdlp_bin:
        return [ytdlp_bin]
    return [sys.executable, "-m", "yt_dlp"]


def build_download_attempts() -> list[tuple[str, list[str]]]:
    attempts: list[tuple[str, list[str]]] = []
    base_extra: list[str] = []

    if YTDLP_COOKIES_FROM_BROWSER:
        base_extra.extend(["--cookies-from-browser", YTDLP_COOKIES_FROM_BROWSER])
    elif YTDLP_COOKIES_FILE:
        base_extra.extend(["--cookies", YTDLP_COOKIES_FILE])

    if YTDLP_REMOTE_COMPONENTS:
        base_extra.extend(["--remote-components", YTDLP_REMOTE_COMPONENTS])

    attempts.append(("default", base_extra))

    # If no cookie config is provided, try common macOS browser profiles automatically.
    if not YTDLP_COOKIES_FROM_BROWSER and not YTDLP_COOKIES_FILE:
        attempts.append(("auto_chrome", ["--cookies-from-browser", "chrome", "--remote-components", "ejs:github"]))
        attempts.append(("auto_safari", ["--cookies-from-browser", "safari", "--remote-components", "ejs:github"]))

    deduped: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for label, extra in attempts:
        key = tuple(extra)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, extra))
    return deduped


def run_download_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        url = job["url"]
        mode = job.get("mode", "video")
        quality = job.get("quality", "best")

    if job.get("status") == "canceled":
        return

    fmt, normalized_quality = resolve_format(mode, quality)
    update_job(
        job_id,
        status="running",
        progress=0,
        message=f"downloading ({mode}/{normalized_quality})",
        quality=normalized_quality,
        started_at=now_iso(),
    )

    output_template = "%(title).180B [%(id)s].%(ext)s"
    base_cmd = [
        *yt_dlp_cmd_prefix(),
        "--newline",
        "--no-color",
        "--retries",
        "1",
        "-f",
        fmt,
        "--paths",
        str(DOWNLOAD_DIR),
        "-o",
        output_template,
    ]

    if YTDLP_USER_AGENT:
        base_cmd.extend(["--user-agent", YTDLP_USER_AGENT])
    if YTDLP_ACCEPT_LANGUAGE:
        base_cmd.extend(["--add-header", f"Accept-Language:{YTDLP_ACCEPT_LANGUAGE}"])
    if YTDLP_REFERER:
        base_cmd.extend(["--add-header", f"Referer:{YTDLP_REFERER}"])

    if mode == "video":
        base_cmd.extend(["--merge-output-format", "mp4"])
    else:
        base_cmd.extend(["--extract-audio", "--audio-format", "mp3"])
        if normalized_quality in {"320", "256", "128"}:
            base_cmd.extend(["--audio-quality", f"{normalized_quality}K"])

    progress_re = re.compile(r"(\d{1,3}(?:[\.,]\d+)?)%")
    merge_re = re.compile(r'Merging formats into\s+"(.+?)"')
    merged_path: str | None = None
    attempts = build_download_attempts()
    final_error_type = "unknown_error"
    final_error_text = "yt-dlp failed"

    try:
        for attempt_idx, (attempt_label, attempt_extra) in enumerate(attempts, start=1):
            tail = deque(maxlen=30)
            cmd = [*base_cmd, *attempt_extra, url]
            update_job(
                job_id,
                status="running",
                message=f"downloading ({mode}/{normalized_quality}) [try {attempt_idx}]",
            )

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            with ACTIVE_PROCESSES_LOCK:
                ACTIVE_PROCESSES[job_id] = process

            assert process.stdout is not None
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                tail.append(text)

                with JOBS_LOCK:
                    current = JOBS.get(job_id)
                    if current and current.get("status") == "canceled":
                        process.terminate()
                        break

                if "Merging formats into" in text:
                    update_job(job_id, status="merging", message="merging")
                    match = merge_re.search(text)
                    if match:
                        merged_path = match.group(1)

                match = progress_re.search(text)
                if match:
                    pct = match.group(1).replace(",", ".")
                    value = min(100, max(0, int(float(pct))))
                    update_job(job_id, progress=value, message="downloading")

            return_code = process.wait()
            with ACTIVE_PROCESSES_LOCK:
                ACTIVE_PROCESSES.pop(job_id, None)

            with JOBS_LOCK:
                final_state = JOBS.get(job_id, {}).get("status")
            if final_state == "canceled":
                update_job(
                    job_id,
                    progress=0,
                    message="canceled",
                    ended_at=now_iso(),
                )
                return

            if return_code == 0:
                update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    message="done",
                    output=merged_path,
                    error=None,
                    ended_at=now_iso(),
                )
                return

            err_text = "\n".join(tail) or "yt-dlp failed"
            err_type = classify_error(err_text)
            final_error_type = err_type
            final_error_text = err_text
            should_retry = err_type in {"auth_required", "extractor_broken", "unknown_error"} and attempt_idx < len(attempts)
            if should_retry:
                update_job(
                    job_id,
                    status="running",
                    message=f"retrying with {attempt_label}",
                    error={"type": err_type, "detail": err_text},
                )
                continue
            break

        update_job(
            job_id,
            status="failed",
            progress=0,
            message="failed",
            error={
                "type": final_error_type,
                "detail": final_error_text,
            },
            ended_at=now_iso(),
        )
        return
    except FileNotFoundError as exc:
        missing = "yt_dlp_or_dependency" if "yt_dlp" in str(exc) or "yt-dlp" in str(exc) else "ffmpeg_or_dependency"
        update_job(
            job_id,
            status="failed",
            progress=0,
            message="failed",
            error={
                "type": missing,
                "detail": str(exc),
            },
            ended_at=now_iso(),
        )
    except Exception as exc:  # noqa: BLE001
        update_job(
            job_id,
            status="failed",
            progress=0,
            message="failed",
            error={
                "type": "internal_error",
                "detail": str(exc),
            },
            ended_at=now_iso(),
        )
    finally:
        with ACTIVE_PROCESSES_LOCK:
            ACTIVE_PROCESSES.pop(job_id, None)


def worker_loop() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            run_download_job(job_id)
        finally:
            JOB_QUEUE.task_done()


@APP.get("/health")
def health() -> Any:
    return jsonify(
        {
            "status": "ok",
            "download_dir": str(DOWNLOAD_DIR),
            "job_store_path": str(JOB_STORE_PATH),
            "backend_host": BACKEND_HOST,
            "backend_port": BACKEND_PORT,
            "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
            "yt_dlp_found": bool(shutil_which("yt-dlp")),
            "ffmpeg_found": bool(shutil_which("ffmpeg")),
            "workers": MAX_CONCURRENT_WORKERS,
            "total_jobs": len(JOBS),
            "cookies_from_browser": bool(YTDLP_COOKIES_FROM_BROWSER),
            "cookies_file": YTDLP_COOKIES_FILE,
            "ua_override": bool(YTDLP_USER_AGENT),
            "accept_language_override": bool(YTDLP_ACCEPT_LANGUAGE),
            "referer_override": bool(YTDLP_REFERER),
            "remote_components": YTDLP_REMOTE_COMPONENTS,
        }
    )


@APP.get("/auth/bootstrap")
def auth_bootstrap() -> Any:
    # Local bootstrap endpoint for userscript token auto-alignment.
    client = (request.args.get("client") or "").strip()
    if client != "yt-userscript-v1":
        return jsonify({"error": "invalid_client"}), 400
    return jsonify({"token": LOCAL_TOKEN})


@APP.post("/jobs")
def create_job() -> Any:
    if not token_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    mode = (data.get("mode") or "video").strip().lower()
    quality = (data.get("quality") or "best").strip().lower()

    if mode not in {"video", "audio"}:
        return jsonify({"error": "invalid_mode"}), 400
    if quality not in {"best", "1080", "720", "480", "360", "240", "144", "320", "256", "128"}:
        return jsonify({"error": "invalid_quality"}), 400

    if not url:
        return jsonify({"error": "missing_url"}), 400
    if not is_allowed_youtube_url(url):
        return jsonify({"error": "invalid_or_unsupported_url"}), 400

    normalized_url = normalize_youtube_video_url(url)
    if not normalized_url:
        return jsonify({"error": "invalid_or_unsupported_url"}), 400

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "url": normalized_url,
        "title": title,
        "mode": mode,
        "quality": quality,
        "status": "queued",
        "progress": 0,
        "message": "queued",
        "output": None,
        "error": None,
        "created_at": now_iso(),
        "started_at": None,
        "ended_at": None,
    }

    with JOBS_LOCK:
        JOBS[job_id] = job
    save_jobs_to_disk()
    touch_activity()

    JOB_QUEUE.put(job_id)
    return jsonify({"job_id": job_id, "status": "queued", "mode": mode, "quality": quality}), 202


@APP.get("/jobs")
def list_jobs() -> Any:
    if not token_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    limit_raw = request.args.get("limit", "50")
    try:
        limit = min(200, max(1, int(limit_raw)))
    except ValueError:
        limit = 50

    with JOBS_LOCK:
        jobs = list(JOBS.values())

    jobs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return jsonify({"jobs": [job_snapshot(job) for job in jobs[:limit]]})


@APP.get("/jobs/<job_id>")
def get_job(job_id: str) -> Any:
    if not token_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "not_found"}), 404
        return jsonify(job_snapshot(job))


@APP.post("/jobs/<job_id>/cancel")
def cancel_job(job_id: str) -> Any:
        if not token_ok(request):
                return jsonify({"error": "unauthorized"}), 401

        with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                        return jsonify({"error": "not_found"}), 404
                if job["status"] in {"completed", "failed", "canceled"}:
                        return jsonify({"job_id": job_id, "status": job["status"]})

        update_job(job_id, status="canceled", message="cancel_requested", ended_at=now_iso())

        with ACTIVE_PROCESSES_LOCK:
                process = ACTIVE_PROCESSES.get(job_id)
                if process and process.poll() is None:
                        process.terminate()

        return jsonify({"job_id": job_id, "status": "canceled"})


@APP.get("/dashboard")
def dashboard() -> Response:
        if not dashboard_token_ok(request):
                return Response("dashboard unauthorized: append ?token=<LOCAL_API_TOKEN>", status=401)

        html = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>YT Downloader Dashboard</title>
    <style>
        :root { --bg:#f7f7f2; --card:#ffffff; --ink:#1d1f21; --accent:#0b6e4f; --warn:#d00000; }
        body { margin:0; padding:24px; background:linear-gradient(135deg,#fefae0,#e9edc9); color:var(--ink); font-family: ui-monospace, Menlo, Consolas, monospace; }
        .card { background:var(--card); border-radius:14px; padding:16px; box-shadow:0 8px 30px rgba(0,0,0,0.08); }
        h1 { margin:0 0 12px 0; font-size:22px; }
        .row { display:flex; gap:10px; margin-bottom:12px; }
        button { border:0; border-radius:10px; padding:9px 12px; cursor:pointer; font-weight:700; background:#003049; color:#fff; }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        th, td { text-align:left; border-bottom:1px solid #e7e7e7; padding:8px 6px; vertical-align:top; }
        .ok { color:var(--accent); font-weight:700; }
        .err { color:var(--warn); font-weight:700; }
        .muted { color:#666; }
    </style>
</head>
<body>
    <div class="card">
        <h1>YT Downloader Dashboard</h1>
        <div class="row">
            <button id="refresh">Refresh</button>
            <button id="auto">Auto: ON</button>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Job</th><th>Status</th><th>Mode</th><th>Progress</th><th>Message</th><th>Output</th><th>Action</th>
                </tr>
            </thead>
            <tbody id="rows"></tbody>
        </table>
    </div>
    <script>
        const token = new URLSearchParams(location.search).get('token');
        let auto = true;
        let timer = null;
        const rows = document.getElementById('rows');
        const btnAuto = document.getElementById('auto');

        async function api(path, options={}) {
            const resp = await fetch(path, {
                ...options,
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': token,
                    ...(options.headers || {}),
                },
            });
            if (!resp.ok) throw new Error(await resp.text());
            return await resp.json();
        }

        function row(job) {
            const statusClass = job.status === 'completed' ? 'ok' : (job.status === 'failed' ? 'err' : '');
            const output = job.output || '';
            const canCancel = ['queued', 'running', 'merging'].includes(job.status);
            const errDetail = job.error && job.error.detail ? String(job.error.detail).slice(0, 120) : '';
            const msg = job.status === 'failed' && errDetail
                ? `${job.message || 'failed'} (${job.error?.type || 'error'}): ${errDetail}`
                : (job.message || '');
            return `<tr>
                <td><div>${job.job_id}</div><div class="muted">${(job.title || '').slice(0, 50)}</div></td>
                <td class="${statusClass}">${job.status}</td>
                <td>${job.mode}/${job.quality}</td>
                <td>${job.progress ?? 0}%</td>
                <td>${msg}</td>
                <td class="muted">${output}</td>
                <td>${canCancel ? `<button data-cancel="${job.job_id}">Cancel</button>` : ''}</td>
            </tr>`;
        }

        async function render() {
            const result = await api('/jobs?limit=100');
            rows.innerHTML = result.jobs.map(row).join('');
            document.querySelectorAll('[data-cancel]').forEach((el) => {
                el.addEventListener('click', async () => {
                    const id = el.getAttribute('data-cancel');
                    await api(`/jobs/${id}/cancel`, { method: 'POST' });
                    await render();
                });
            });
        }

        async function tick() {
            try { await render(); } catch (e) { console.error(e); }
            if (auto) timer = setTimeout(tick, 1500);
        }

        document.getElementById('refresh').addEventListener('click', () => { render(); });
        btnAuto.addEventListener('click', () => {
            auto = !auto;
            btnAuto.textContent = `Auto: ${auto ? 'ON' : 'OFF'}`;
            if (auto) { tick(); } else if (timer) { clearTimeout(timer); timer = null; }
        });

        tick();
    </script>
</body>
</html>
"""
        return Response(html, mimetype="text/html")


def shutil_which(bin_name: str) -> str | None:
    # Local helper to avoid importing more modules in hot paths.
    from shutil import which

    return which(bin_name)


def start_workers() -> None:
    for idx in range(MAX_CONCURRENT_WORKERS):
        thread = threading.Thread(target=worker_loop, name=f"worker-{idx+1}", daemon=True)
        thread.start()


def run_backend() -> None:
    load_jobs_from_disk()
    start_workers()
    start_idle_watchdog()
    touch_activity()

    server = create_server()
    try:
        server.serve_forever()
    finally:
        IDLE_WATCHDOG_STOP.set()
        server.server_close()


if __name__ == "__main__":
    run_backend()