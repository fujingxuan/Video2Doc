from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = BACKEND_DIR.parent
load_dotenv(REPOSITORY_DIR / ".env", override=False)


def _resolve_path(value: str, default: Path) -> Path:
    candidate = Path(value) if value else default
    if not candidate.is_absolute():
        candidate = BACKEND_DIR / candidate
    return candidate.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    api_prefix: str
    database_url: str
    data_dir: Path
    inbox_dir: Path
    cors_origins: tuple[str, ...]
    log_level: str
    simulation_step_seconds: float
    process_poll_seconds: float
    media_process_timeout_seconds: float
    asr_request_timeout_seconds: float
    asr_max_retries: int
    audio_chunk_seconds: float
    asr_overlap_seconds: float


@lru_cache
def get_settings() -> Settings:
    raw_database_url = os.getenv("VIDEO2DOC_DATABASE_URL", "sqlite:///./data/video2doc.db")
    if raw_database_url.startswith("sqlite:///./"):
        relative = raw_database_url.removeprefix("sqlite:///./")
        raw_database_url = f"sqlite:///{(BACKEND_DIR / relative).resolve().as_posix()}"
    origins = os.getenv(
        "VIDEO2DOC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    return Settings(
        app_name="Video2Doc",
        api_prefix="/api/v1",
        database_url=raw_database_url,
        data_dir=_resolve_path(os.getenv("VIDEO2DOC_DATA_DIR", ""), BACKEND_DIR / "data/tasks"),
        inbox_dir=_resolve_path(os.getenv("VIDEO2DOC_INBOX_DIR", ""), BACKEND_DIR / "data/inbox"),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        log_level=os.getenv("VIDEO2DOC_LOG_LEVEL", "INFO").upper(),
        simulation_step_seconds=max(
            0.001, float(os.getenv("VIDEO2DOC_SIMULATION_STEP_SECONDS", "0.20"))
        ),
        process_poll_seconds=max(
            0.02, float(os.getenv("VIDEO2DOC_PROCESS_POLL_SECONDS", "0.20"))
        ),
        media_process_timeout_seconds=max(
            1.0, float(os.getenv("VIDEO2DOC_MEDIA_TIMEOUT_SECONDS", "3600"))
        ),
        asr_request_timeout_seconds=max(
            1.0, float(os.getenv("VIDEO2DOC_ASR_TIMEOUT_SECONDS", "300"))
        ),
        asr_max_retries=max(0, int(os.getenv("VIDEO2DOC_ASR_MAX_RETRIES", "2"))),
        audio_chunk_seconds=max(
            30.0, float(os.getenv("VIDEO2DOC_AUDIO_CHUNK_SECONDS", "60"))
        ),
        asr_overlap_seconds=max(
            0.0, float(os.getenv("VIDEO2DOC_ASR_OVERLAP_SECONDS", "5"))
        ),
    )
