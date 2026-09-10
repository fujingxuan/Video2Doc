from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.api import TaskBatchCreate, TaskConfiguration, TaskInput, TaskRead
from app.services.tasks import TaskService, WorkerNotifier

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".amr",
}
MAX_AUDIO_BYTES = 2 * 1024**3


def create_uploaded_audio_tasks(
    files: list[UploadFile],
    configuration: str,
    session: Session,
    worker: WorkerNotifier,
) -> list[TaskRead]:
    try:
        config = TaskConfiguration.model_validate_json(configuration)
    except ValidationError as exc:
        raise AppError("上传配置无效，请检查任务设置", status_code=422) from exc
    if not 1 <= len(files) <= 20:
        raise AppError("每次可上传 1 至 20 个音频文件", status_code=422)
    for file in files:
        if Path(file.filename or "").suffix.lower() not in AUDIO_EXTENSIONS:
            raise AppError(
                "不支持的音频格式，请上传 WAV、MP3、M4A、FLAC 等音频文件", status_code=422
            )
        if file.size is not None and file.size > MAX_AUDIO_BYTES:
            raise AppError("单个音频文件不能超过 2 GiB", status_code=413)
    staging_root = get_settings().data_dir / ".uploads"
    staging_root.mkdir(parents=True, exist_ok=True)
    service = TaskService(session, worker)
    try:
        with TemporaryDirectory(prefix="audio-", dir=staging_root) as temporary:
            uploads: dict[str, dict] = {}
            inputs = []
            for index, file in enumerate(files):
                original_name = (file.filename or "audio").replace("\\", "/").split("/")[-1]
                suffix = Path(original_name).suffix.lower()
                # A fixed prefix also handles Windows reserved device names safely.
                name = (
                    "audio_"
                    + re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(original_name).stem)[:100].rstrip(
                        " ."
                    )
                    + suffix
                )
                path = Path(temporary) / f"{index:02d}{suffix}"
                digest = hashlib.sha256()
                size = 0
                with path.open("wb") as destination:
                    while chunk := file.file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_AUDIO_BYTES:
                            raise AppError("单个音频文件不能超过 2 GiB", status_code=413)
                        digest.update(chunk)
                        destination.write(chunk)
                if not size:
                    raise AppError("不能上传空音频文件", status_code=422)
                uploads[str(path)] = {
                    "filename": name,
                    "original_name": original_name,
                    "size_bytes": size,
                    "sha256": digest.hexdigest(),
                }
                inputs.append(
                    TaskInput(source=str(path), source_type="LOCAL_FILE", title=original_name[:300])
                )
            return service.create_batch(
                TaskBatchCreate(**config.model_dump(), inputs=inputs),
                uploaded_files=uploads,
            )
    except Exception:
        session.rollback()
        for workspace in service.created_upload_workspaces:
            # Only directories created by this request are eligible for rollback.
            resolved = workspace.resolve(strict=True)
            if resolved == workspace.absolute() and (resolved / "task_config.json").is_file():
                shutil.rmtree(resolved)
        raise
