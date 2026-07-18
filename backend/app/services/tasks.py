from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.models import AppSetting, ModelConfig, ProcessingProfile, Task, TaskConfigSnapshot
from app.models.entities import utcnow
from app.repositories.tasks import TaskRepository
from app.schemas.api import QueueStatus, TaskBatchCreate, TaskDetail, TaskRead
from app.services.serializers import task_detail, task_read
from app.storage.workspace import create_workspace, list_artifacts, read_json


class WorkerNotifier(Protocol):
    def notify(self) -> None: ...


class TaskService:
    def __init__(self, session: Session, worker: WorkerNotifier | None = None) -> None:
        self.session = session
        self.repo = TaskRepository(session)
        self.worker = worker

    def create_batch(self, payload: TaskBatchCreate) -> list[TaskRead]:
        settings = self._get_or_create_settings()
        profile = self._resolve_profile(payload.profile_id, settings)
        base_snapshot = self._build_snapshot(
            profile,
            settings,
            payload.config_overrides,
            payload.pipeline_mode,
            payload.asr_model_id,
        )
        tasks: list[Task] = []
        for task_input in payload.inputs:
            title = task_input.title or Path(task_input.source).name or task_input.source[:100]
            snapshot = copy.deepcopy(base_snapshot)
            snapshot["input"] = {
                "source": task_input.source,
                "source_type": task_input.source_type,
                "title": title,
            }
            snapshot["simulation"] = {
                "enabled": payload.pipeline_mode == "SIMULATED",
                "failure_mode": task_input.simulate_failure,
            }
            item = Task(
                source=task_input.source,
                source_type=task_input.source_type,
                title=title,
                status="QUEUED",
                current_stage="QUEUED",
            )
            self.session.add(item)
            self.session.flush()
            item.snapshot = TaskConfigSnapshot(schema_version=1, snapshot_json=snapshot)
            data_root = Path(settings.data_path) if settings.data_path else get_settings().data_dir
            item.workspace_path = str(
                create_workspace(data_root, task_id=item.id, title=title, snapshot=snapshot)
            )
            tasks.append(item)
        settings.last_profile_id = profile.id if profile else None
        settings.last_task_config = copy.deepcopy(base_snapshot)
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return [task_read(item) for item in tasks]

    def list(self, status: str | None = None) -> list[TaskRead]:
        return [task_read(item) for item in self.repo.list(status)]

    def get(self, task_id: int) -> TaskDetail:
        item = self.repo.get(task_id, detail=True)
        if not item:
            raise NotFoundError("Task", task_id)
        return task_detail(item)

    def snapshot(self, task_id: int) -> dict[str, Any]:
        item = self.repo.get(task_id, detail=True)
        if not item:
            raise NotFoundError("Task", task_id)
        return copy.deepcopy(item.snapshot.snapshot_json)

    def media(self, task_id: int) -> dict[str, Any]:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        payload = read_json(Path(item.media_info_path)) if item.media_info_path else None
        if payload is None:
            raise NotFoundError("Task media information", task_id)
        return payload

    def transcript(self, task_id: int) -> dict[str, Any]:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        payload = read_json(Path(item.transcript_path)) if item.transcript_path else None
        if payload is None:
            raise NotFoundError("Task transcript", task_id)
        return payload

    def artifacts(self, task_id: int) -> list[dict[str, Any]]:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        return list_artifacts(Path(item.workspace_path)) if item.workspace_path else []

    def cancel(self, task_id: int) -> TaskRead:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.status == "QUEUED":
            item.status = "CANCELLED"
            item.current_stage = "CANCELLED"
            item.error_code = "user_cancelled"
            item.error_message = "Task was cancelled before processing started"
            item.completed_at = utcnow()
        elif item.status == "PROCESSING":
            result = self.session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "PROCESSING")
                .values(cancel_requested=True)
            )
            if result.rowcount != 1:
                self.session.rollback()
                self.session.refresh(item)
                raise ConflictError(f"Task in status {item.status} cannot be cancelled")
        else:
            raise ConflictError(f"Task in status {item.status} cannot be cancelled")
        self.session.commit()
        if self.worker:
            self.worker.notify()
        self.session.refresh(item)
        return task_read(item)

    def retry(self, task_id: int) -> TaskRead:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.status not in {"FAILED", "INTERRUPTED"}:
            raise ConflictError("Only failed or interrupted tasks can be retried")
        item.status = "QUEUED"
        item.current_stage = "QUEUED"
        item.progress = 0
        item.cancel_requested = False
        item.error_code = None
        item.error_message = None
        item.started_at = None
        item.completed_at = None
        item.output_path = None
        item.attempt += 1
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return task_read(item)

    def queue_status(self) -> QueueStatus:
        counts = self.repo.counts()
        running_task = self.session.scalar(
            select(Task).where(Task.status == "PROCESSING").order_by(Task.started_at).limit(1)
        )
        return QueueStatus(
            running=counts.get("PROCESSING", 0),
            queued=counts.get("QUEUED", 0),
            completed=counts.get("COMPLETED", 0),
            failed=counts.get("FAILED", 0),
            cancelled=counts.get("CANCELLED", 0),
            interrupted=counts.get("INTERRUPTED", 0),
            running_task_id=running_task.id if running_task else None,
        )

    def _resolve_profile(
        self, requested_id: int | None, settings: AppSetting
    ) -> ProcessingProfile | None:
        profile_id = requested_id or settings.last_profile_id
        if profile_id:
            profile = self.session.scalar(
                select(ProcessingProfile)
                .where(ProcessingProfile.id == profile_id)
                .options(
                    selectinload(ProcessingProfile.asr_model),
                    selectinload(ProcessingProfile.ocr_model),
                    selectinload(ProcessingProfile.text_model),
                )
            )
            if not profile:
                raise NotFoundError("Processing profile", profile_id)
            return profile
        return self.session.scalar(
            select(ProcessingProfile)
            .where(ProcessingProfile.is_default.is_(True), ProcessingProfile.enabled.is_(True))
            .options(
                selectinload(ProcessingProfile.asr_model),
                selectinload(ProcessingProfile.ocr_model),
                selectinload(ProcessingProfile.text_model),
            )
            .limit(1)
        )

    def _build_snapshot(
        self,
        profile: ProcessingProfile | None,
        settings: AppSetting,
        overrides: dict[str, Any],
        pipeline_mode: str,
        asr_model_id: int | None,
    ) -> dict[str, Any]:
        processing = {
            "ocr_enabled": settings.default_ocr_enabled,
            "remove_fillers": True,
            "fix_sentences": True,
            "auto_paragraphs": True,
            "generate_headings": True,
            "keep_timestamps": settings.default_keep_timestamps,
            "keep_raw_transcript": settings.default_keep_raw_transcript,
            "keep_downloaded_media": settings.default_keep_downloaded_media,
            "ocr_frame_interval_seconds": 5.0,
            "markdown_template": settings.default_markdown_template,
            "audio_chunk_seconds": get_settings().audio_chunk_seconds,
            "asr_language": None,
        }
        models: dict[str, str | None] = {"asr": None, "ocr": None, "text_processing": None}
        profile_data: dict[str, Any] | None = None
        if profile:
            profile_data = {
                column.name: getattr(profile, column.name)
                for column in profile.__table__.columns
                if column.name not in {"created_at", "updated_at"}
            }
            for key in processing:
                if hasattr(profile, key):
                    processing[key] = getattr(profile, key)
            models = {
                "asr": profile.asr_model.name if profile.asr_model else None,
                "ocr": profile.ocr_model.name if profile.ocr_model else None,
                "text_processing": profile.text_model.name if profile.text_model else None,
            }
        if asr_model_id is not None:
            asr_model = self.session.get(ModelConfig, asr_model_id)
            if (
                asr_model is None
                or not asr_model.enabled
                or not any(item.capability == "ASR" for item in asr_model.capabilities)
            ):
                raise ConflictError("Selected ASR model is unavailable or lacks the ASR capability")
            models["asr"] = asr_model.name
        allowed_overrides = set(processing)
        unknown = set(overrides) - allowed_overrides
        if unknown:
            raise ConflictError(f"Unsupported task config overrides: {', '.join(sorted(unknown))}")
        processing.update(copy.deepcopy(overrides))
        return {
            "schema_version": 2,
            "profile": profile_data,
            "models": models,
            "processing": processing,
            "paths": {
                "inbox": settings.inbox_path or str(get_settings().inbox_dir),
                "data": settings.data_path or str(get_settings().data_dir),
            },
            "pipeline": pipeline_mode,
            "tools": {
                "ffmpeg": settings.ffmpeg_path or "ffmpeg",
                "ffprobe": _ffprobe_for(settings.ffmpeg_path),
                "ytdlp": settings.ytdlp_path or "yt-dlp",
            },
            "asr": {
                "provider": "oneapi-openai-compatible",
                "base_url": settings.oneapi_base_url or os.getenv("ONEAPI_BASE_URL", ""),
                "model": models["asr"],
                "language": processing.get("asr_language"),
                "timeout_seconds": get_settings().asr_request_timeout_seconds,
                "max_retries": get_settings().asr_max_retries,
            },
        }

    def _get_or_create_settings(self) -> AppSetting:
        item = self.session.get(AppSetting, 1)
        if item is None:
            item = AppSetting(id=1)
            self.session.add(item)
            self.session.flush()
        return item


def _ffprobe_for(ffmpeg_path: str | None) -> str:
    if not ffmpeg_path:
        return "ffprobe"
    path = Path(ffmpeg_path)
    if path.parent == Path("."):
        return "ffprobe"
    suffix = path.suffix or (".exe" if os.name == "nt" else "")
    return str(path.with_name(f"ffprobe{suffix}"))
