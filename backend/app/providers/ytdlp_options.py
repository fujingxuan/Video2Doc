from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import REPOSITORY_DIR
from app.core.errors import PipelineError


def default_ytdlp_executable() -> str:
    bundle = getattr(sys, "_MEIPASS", None)
    candidates = ([Path(bundle) / "tools/yt-dlp.exe"] if bundle else []) + [
        REPOSITORY_DIR / "tools/media/yt-dlp.exe",
    ]
    return next((str(p.resolve()) for p in candidates if p.is_file()), "yt-dlp")


def authentication_options(source: str = "") -> list[str]:
    """Use only an explicitly configured cookie file; never read browser profiles."""
    value = os.getenv("VIDEO2DOC_YTDLP_COOKIES_FILE", "").strip()
    options = ["--proxy", ""] if urlparse(source).hostname in {"127.0.0.1", "localhost", "::1"} else []
    if not value:
        return options
    path = Path(value).expanduser()
    if not path.is_file():
        raise PipelineError("MEDIA_COOKIES_MISSING", "配置的登录 Cookie 文件不存在，请检查路径")
    return [*options, "--cookies", str(path.resolve())]


def translate_ytdlp_error(exc: PipelineError) -> PipelineError | None:
    if exc.code in {"PROCESS_CANCELLED", "PROCESS_TIMEOUT", "backend_shutdown", "PROCESS_UNAVAILABLE"}:
        return None
    detail = (exc.technical_detail or "").casefold()
    if any(marker in detail for marker in (
        "xiaohongshu.com/login", "login required", "sign in", "log in", "login_required",
    )):
        return PipelineError(
            "MEDIA_LOGIN_REQUIRED",
            "视频平台要求登录，当前链接无法匿名下载。请导入已下载的音视频或可访问的媒体直链；"
            "也可配置 VIDEO2DOC_YTDLP_COOKIES_FILE 后重试。",
            technical_detail=exc.technical_detail, log_path=exc.log_path,
        )
    if "unsupported url" in detail:
        return PipelineError(
            "MEDIA_URL_UNSUPPORTED", "下载器暂不支持此链接，请使用视频详情页链接或导入本地音视频",
            technical_detail=exc.technical_detail, log_path=exc.log_path,
        )
    return None
