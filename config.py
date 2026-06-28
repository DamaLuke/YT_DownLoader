from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


LOCAL_TOKEN_PLACEHOLDER = "change-me-local-token"


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    value = _get_str(name, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def generate_local_token() -> str:
    token = _get_str("LOCAL_API_TOKEN")
    if token and token != LOCAL_TOKEN_PLACEHOLDER:
        return token
    return secrets.token_urlsafe(32)


LOCAL_TOKEN = generate_local_token()
MAX_CONCURRENT_WORKERS = _get_int("MAX_CONCURRENT_WORKERS", 1)
BACKEND_HOST = _get_str("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = _get_int("BACKEND_PORT", 5050)
IDLE_TIMEOUT_SECONDS = _get_int("IDLE_TIMEOUT_SECONDS", 90)
IDLE_CHECK_INTERVAL_SECONDS = _get_int("IDLE_CHECK_INTERVAL_SECONDS", 15)
LAUNCHD_SOCKET_NAME = _get_str("LAUNCHD_SOCKET_NAME", "Listeners")
DOWNLOAD_DIR = Path(_get_str("DOWNLOAD_DIR", "~/Downloads/YouTube")).expanduser().resolve()
JOB_STORE_PATH = Path(_get_str("JOB_STORE_PATH", "./.data/jobs.json")).expanduser().resolve()
YTDLP_COOKIES_FROM_BROWSER = _get_str("YTDLP_COOKIES_FROM_BROWSER")
YTDLP_COOKIES_FILE = _get_str("YTDLP_COOKIES_FILE")
YTDLP_USER_AGENT = _get_str("YTDLP_USER_AGENT")
YTDLP_ACCEPT_LANGUAGE = _get_str("YTDLP_ACCEPT_LANGUAGE")
YTDLP_REFERER = _get_str("YTDLP_REFERER")
YTDLP_REMOTE_COMPONENTS = _get_str("YTDLP_REMOTE_COMPONENTS", "ejs:github")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)