from __future__ import annotations

import copy
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.credentials import credential_is_configured, credential_source
from app.models import (
    AppSetting,
    GatewayModel,
    MarkdownExport,
    ModelConfig,
    ProcessingProfile,
    Task,
    TaskConfigSnapshot,
)
from app.models.entities import utcnow
from app.repositories.tasks import TaskRepository
from app.schemas.api import QueueStatus, TaskBatchCreate, TaskDetail, TaskInput, TaskPage, TaskRead, TaskUpdate
from app.services.gateways import sanitize_gateway_metadata
from app.services.serializers import task_detail, task_read
from app.storage.workspace import (
    append_task_log,
    create_workspace,
    list_artifacts,
    read_json,
    write_json,
)


class WorkerNotifier(Protocol):
    def notify(self) -> None: ...


class TaskService:
    def __init__(self, session: Session, worker: WorkerNotifier | None = None) -> None:
        self.session = session
        self.repo = TaskRepository(session)
        self.worker = worker
        self.created_upload_workspaces: list[Path] = []

    def create_batch(
        self, payload: TaskBatchCreate, *, uploaded_files: dict[str, dict] | None = None,
    ) -> list[TaskRead]:
        settings = self._get_or_create_settings()
        profile = self._resolve_profile(payload.profile_id, settings)
        base_snapshot = self._build_snapshot(
            profile,
            settings,
            payload.config_overrides,
            payload.pipeline_mode,
            payload.asr_model_id,
            payload.asr_gateway_model_id,
            payload.ocr_gateway_model_id,
            payload.text_model_id,
            payload.export_directory,
            payload.export_vault_path,
            payload.export_inbox_relative_path,
            require_asr=any(item.text_source in {"ASR", "SUBTITLE"} for item in payload.inputs),
        )
        tasks: list[Task] = []
        for task_input in payload.inputs:
            title = task_input.title or Path(task_input.source).name or task_input.source[:100]
            snapshot = _task_snapshot(
                base_snapshot,
                task_input,
                title=title,
                pipeline_mode=payload.pipeline_mode,
            )
            item = Task(
                source=task_input.source,
                source_type=task_input.source_type,
                title=title,
                status="QUEUED",
                current_stage="QUEUED",
            )
            self.session.add(item)
            self.session.flush()
            data_root = Path(settings.data_path) if settings.data_path else get_settings().data_dir
            workspace = create_workspace(data_root, task_id=item.id, title=title, snapshot=snapshot)
            item.workspace_path = str(workspace)
            if uploaded_files and task_input.source in uploaded_files:
                self.created_upload_workspaces.append(workspace)
                upload = uploaded_files[task_input.source]
                destination = workspace / "00_source" / "upload" / upload["filename"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(task_input.source, destination)
                item.source = str(destination)
                snapshot["input"]["source"] = str(destination)
                snapshot["input"]["upload"] = {
                    **upload, "path": str(destination), "owned_copy": True,
                }
                _sync_workspace_snapshot(workspace, snapshot)
            item.snapshot = TaskConfigSnapshot(schema_version=6, snapshot_json=snapshot)
            tasks.append(item)
        settings.last_profile_id = profile.id if profile else None
        settings.last_task_config = copy.deepcopy(tasks[-1].snapshot.snapshot_json)
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return [task_read(item) for item in tasks]

    def list(self, status: str | None = None) -> list[TaskRead]:
        return [task_read(item) for item in self.repo.list(status)]

    def page(
        self, *, offset: int = 0, limit: int = 100, status: str | None = None,
        search: str = "", trashed: bool = False,
    ) -> TaskPage:
        items, total, actual_offset = self.repo.page(
            offset=offset, limit=limit, status=status, search=search, trashed=trashed,
        )
        return TaskPage(items=[task_read(item) for item in items], total=total,
                        offset=actual_offset, limit=limit)

    def get(self, task_id: int) -> TaskDetail:
        item = self.repo.get(task_id, detail=True)
        if not item:
            raise NotFoundError("Task", task_id)
        return task_detail(item)

    def update(self, task_id: int, payload: TaskUpdate) -> TaskRead:
        item = self.repo.get(task_id, detail=True)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.status != "QUEUED":
            raise ConflictError("Only queued tasks can be edited")
        if item.review_submission_path or (item.snapshot.snapshot_json.get("input") or {}).get("upload"):
            raise ConflictError("已上传文件或已确认稿件的任务不能更换输入，请新建任务")

        settings = self._get_or_create_settings()
        profile = self._resolve_profile(payload.profile_id, settings)
        base_snapshot = self._build_snapshot(
            profile,
            settings,
            payload.config_overrides,
            payload.pipeline_mode,
            payload.asr_model_id,
            payload.asr_gateway_model_id,
            payload.ocr_gateway_model_id,
            payload.text_model_id,
            payload.export_directory,
            payload.export_vault_path,
            payload.export_inbox_relative_path,
            require_asr=payload.input.text_source in {"ASR", "SUBTITLE"},
        )
        title = payload.input.title or Path(payload.input.source).name or payload.input.source[:100]
        snapshot = _task_snapshot(
            base_snapshot,
            payload.input,
            title=title,
            pipeline_mode=payload.pipeline_mode,
        )
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "QUEUED", Task.deleted_at.is_(None))
            .values(
                source=payload.input.source,
                source_type=payload.input.source_type,
                title=title,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ConflictError("Task started before the edit could be saved")
        workspace = _validated_workspace(item)
        if workspace is None:
            self.session.rollback()
            raise ConflictError("Task workspace no longer exists; the queued task cannot be edited")
        item.snapshot.schema_version = 6
        item.snapshot.snapshot_json = snapshot
        settings.last_profile_id = profile.id if profile else None
        settings.last_task_config = copy.deepcopy(snapshot)
        _sync_workspace_snapshot(workspace, snapshot)
        append_task_log(workspace, "Queued task settings were edited before processing")
        self.session.commit()
        updated_item = self.repo.get(task_id)
        if updated_item is None:
            raise NotFoundError("Task", task_id)
        if self.worker:
            self.worker.notify()
        return task_read(updated_item)

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

    def frames(self, task_id: int) -> dict[str, Any]:
        return self._artifact_json(task_id, "frames_manifest_path", "Task frame manifest")

    def ocr(self, task_id: int) -> dict[str, Any]:
        return self._artifact_json(task_id, "ocr_timeline_path", "Task OCR timeline")

    def fusion(self, task_id: int) -> dict[str, Any]:
        return self._artifact_json(task_id, "fusion_timeline_path", "Task fusion timeline")

    def document(self, task_id: int) -> dict[str, Any]:
        return self._artifact_json(task_id, "document_path", "Task document")

    def markdown(self, task_id: int) -> str:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        value = item.markdown_path or item.export_path
        if not value:
            raise NotFoundError("Task Markdown", task_id)
        try:
            path = Path(value).resolve(strict=True)
            if not path.is_file():
                raise OSError("not a file")
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise NotFoundError("Task Markdown", task_id) from exc

    def open_target(self, task_id: int, target: str) -> dict[str, str]:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        if target == "workspace":
            value = item.workspace_path
        elif target == "export_directory":
            value = str(Path(item.export_path).parent) if item.export_path else None
        elif target == "markdown":
            value = item.export_path or item.markdown_path
        else:
            raise NotFoundError("Task open target", target)
        if not value:
            raise NotFoundError("Task open target", target)
        try:
            path = Path(value).resolve(strict=True)
        except OSError as exc:
            raise NotFoundError("Task open target", target) from exc
        if os.name != "nt":
            raise ConflictError("Opening task paths is supported only on Windows")
        os.startfile(str(path))  # type: ignore[attr-defined]
        return {"target": target, "path": str(path)}

    def artifact_path(self, task_id: int, relative_path: str) -> Path:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        if not item.workspace_path:
            raise NotFoundError("Task workspace", task_id)
        try:
            workspace = Path(item.workspace_path).resolve(strict=True)
            candidate = (workspace / relative_path).resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise NotFoundError("Task artifact", relative_path) from exc
        if not candidate.is_relative_to(workspace) or not candidate.is_file():
            raise NotFoundError("Task artifact", relative_path)
        return candidate

    def _artifact_json(self, task_id: int, field: str, resource_name: str) -> dict[str, Any]:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        value = getattr(item, field)
        payload = read_json(Path(value)) if value else None
        if payload is None:
            raise NotFoundError(resource_name, task_id)
        return payload

    def cancel(self, task_id: int) -> TaskRead:
        item = self.repo.get(task_id)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.status in {"QUEUED", "WAITING_REVIEW"}:
            result = self.session.execute(
                update(Task).where(Task.id == task_id, Task.status == item.status,
                                   Task.deleted_at.is_(None))
                .values(status="CANCELLED", current_stage="CANCELLED",
                        error_code="user_cancelled",
                        error_message="Task was cancelled before processing started",
                        completed_at=utcnow())
            )
            if result.rowcount != 1:
                self.session.rollback()
                raise ConflictError("任务状态已变化，请刷新后重试取消")
        elif item.status == "PROCESSING":
            result = self.session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "PROCESSING", Task.deleted_at.is_(None))
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
        result = self.session.execute(
            update(Task).where(Task.id == task_id, Task.status.in_(["FAILED", "INTERRUPTED"]),
                               Task.deleted_at.is_(None), Task.attempt == item.attempt)
            .values(status="QUEUED", current_stage="QUEUED", progress=0,
                    cancel_requested=False, error_code=None, error_message=None,
                    started_at=None, completed_at=None, output_path=None,
                    document_path=None, markdown_path=None, export_path=None,
                    attempt=Task.attempt + 1)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ConflictError("任务状态已变化，请刷新后重试")
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return task_read(item)

    def delete(self, task_id: int) -> TaskRead:
        """Move to trash without moving or deleting any generated files."""
        item = self.repo.get(task_id, include_deleted=True)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.deleted_at is not None:
            return task_read(item)
        now = utcnow()
        result = self.session.execute(
            update(Task).where(
                Task.id == task_id, Task.status.not_in(["PROCESSING", "DELETING"]),
                Task.deleted_at.is_(None),
            ).values(deleted_at=now, trash_expires_at=now + timedelta(days=30))
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ConflictError("运行中的任务需先取消，才能移入回收站")
        self.session.commit()
        self.session.refresh(item)
        if self.worker:
            self.worker.notify()
        return task_read(item)

    def restore(self, task_id: int) -> TaskRead:
        item = self.repo.get(task_id, include_deleted=True)
        if item is None or item.deleted_at is None:
            raise NotFoundError("Trashed task", task_id)
        result = self.session.execute(
            update(Task).where(
                Task.id == task_id, Task.deleted_at.is_not(None), Task.status != "DELETING",
            ).values(deleted_at=None, trash_expires_at=None)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ConflictError("任务正在永久删除，无法恢复")
        self.session.commit()
        self.session.refresh(item)
        if self.worker:
            self.worker.notify()
        return task_read(item)

    def purge_expired(self) -> dict[str, Any]:
        cutoff = utcnow()
        ids = list(self.session.scalars(
            select(Task.id).where(Task.deleted_at.is_not(None), Task.trash_expires_at <= cutoff,
                                  Task.status != "DELETING")
            .order_by(Task.trash_expires_at, Task.id).limit(100)
        ))
        deleted, skipped = [], []
        for task_id in ids:
            try:
                deleted.append(self.purge(task_id, expired_before=cutoff))
            except (ConflictError, NotFoundError) as exc:
                skipped.append({"task_id": task_id, "reason": exc.message})
        return {"deleted": deleted, "skipped": skipped}

    def purge(self, task_id: int, *, expired_before: datetime | None = None) -> dict[str, Any]:
        item = self.repo.get(task_id, detail=True, include_deleted=True)
        if not item:
            raise NotFoundError("Task", task_id)
        if item.deleted_at is None:
            raise ConflictError("请先将任务移入回收站，再确认永久删除")
        if item.status in {"PROCESSING", "DELETING"}:
            raise ConflictError("任务正在执行或删除，请稍后重试")

        previous_status = item.status
        previous_stage = item.current_stage
        claim = update(Task).where(
            Task.id == task_id, Task.status == previous_status,
            Task.deleted_at == item.deleted_at,
        )
        if expired_before is not None:
            claim = claim.where(Task.trash_expires_at <= expired_before)
        claimed = self.session.execute(
            claim.values(status="DELETING", current_stage="DELETING")
            .execution_options(synchronize_session="fetch")
        )
        if claimed.rowcount != 1:
            self.session.rollback()
            raise ConflictError("Task state changed before deletion could start")
        self.session.commit()
        try:
            result = _delete_task_files(item, protected_paths=self._other_task_paths(task_id))
        except (OSError, ValueError, ConflictError) as exc:
            self.session.rollback()
            current = self.session.get(Task, task_id)
            if current is not None and current.status == "DELETING":
                current.status = previous_status if isinstance(exc, ConflictError) else "FAILED"
                current.current_stage = previous_stage if isinstance(exc, ConflictError) else "FAILED"
                if not isinstance(exc, ConflictError):
                    current.error_code = "delete_failed"
                    current.error_message = "永久删除未完成，部分文件可能已删除；可检查后重新永久删除"
                self.session.commit()
            message = exc.message if isinstance(exc, ConflictError) else str(exc)
            raise ConflictError(f"Task files could not be deleted safely: {message}") from exc

        self.session.delete(item)
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return {
            "task_id": task_id,
            "files_removed": result["files_removed"],
            "bytes_freed": result["bytes_freed"],
            "deleted_paths": result["deleted_paths"],
        }

    def _other_task_paths(self, task_id: int) -> list[Path]:
        """Do not purge files referenced by another task, including trashed tasks."""
        paths: list[Path] = []
        rows = self.session.execute(select(
            Task.source, Task.source_type, Task.workspace_path, Task.output_path,
            Task.export_path, Task.markdown_path,
        ).where(Task.id != task_id))
        for source, source_type, *outputs in rows:
            values = outputs + ([source] if source_type == "LOCAL_FILE" else [])
            paths.extend(Path(value).resolve(strict=False) for value in values if value)
        rows = self.session.execute(select(
            MarkdownExport.markdown_path, MarkdownExport.attachment_directory,
            MarkdownExport.data_directory,
        ).where(MarkdownExport.task_id != task_id, MarkdownExport.status == "COMPLETED"))
        for row in rows:
            paths.extend(Path(value).resolve(strict=False) for value in row if value)
        return paths

    def queue_status(self) -> QueueStatus:
        counts = self.repo.counts()
        running_task = self.session.scalar(
            select(Task.id).where(Task.status == "PROCESSING", Task.deleted_at.is_(None))
            .order_by(Task.started_at).limit(1)
        )
        return QueueStatus(
            running=counts.get("PROCESSING", 0),
            queued=counts.get("QUEUED", 0),
            waiting_review=counts.get("WAITING_REVIEW", 0),
            completed=counts.get("COMPLETED", 0),
            failed=counts.get("FAILED", 0),
            cancelled=counts.get("CANCELLED", 0),
            interrupted=counts.get("INTERRUPTED", 0),
            running_task_id=running_task,
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
                    selectinload(ProcessingProfile.asr_gateway_model).selectinload(
                        GatewayModel.gateway
                    ),
                    selectinload(ProcessingProfile.ocr_gateway_model).selectinload(
                        GatewayModel.gateway
                    ),
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
                selectinload(ProcessingProfile.asr_gateway_model).selectinload(
                    GatewayModel.gateway
                ),
                selectinload(ProcessingProfile.ocr_gateway_model).selectinload(
                    GatewayModel.gateway
                ),
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
        asr_gateway_model_id: int | None,
        ocr_gateway_model_id: int | None,
        text_model_id: int | None,
        export_directory: str | None,
        export_vault_path: str | None,
        export_inbox_relative_path: str | None,
        *,
        require_asr: bool,
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
            "frame_sampling_strategy": "hybrid",
            "scene_change_threshold": 0.35,
            "scene_min_gap_seconds": 1.5,
            "frame_similarity_hamming_threshold": 4,
            "frame_similarity_difference_threshold": 0.025,
            "frame_transition_burst_seconds": 3.0,
            "frame_normalized_max_width": 1600,
            "max_candidate_frames": 240,
            "ocr_language": "ch",
            "ocr_batch_size": 1,
            "ocr_text_similarity_threshold": 0.88,
            "ocr_incremental_similarity_threshold": 0.55,
            "ocr_event_gap_seconds": 20.0,
            "fusion_window_seconds": 8.0,
            "markdown_template": settings.default_markdown_template,
            "audio_chunk_seconds": get_settings().audio_chunk_seconds,
            "asr_language": None,
            "text_edit_enabled": settings.default_text_edit_enabled,
            "embedded_subtitle_ocr_fallback": False,
            "embedded_subtitle_interval_seconds": 1.0,
            "prompt_version": settings.text_prompt_version,
            "faithfulness_min_length_ratio": settings.faithfulness_min_length_ratio,
            "faithfulness_formula_symbol_ratio": (settings.faithfulness_formula_symbol_ratio),
            "markdown_template_version": settings.markdown_template_version,
            "image_insertion_policy": settings.image_insertion_policy,
            "image_min_gap_seconds": settings.image_min_gap_seconds,
            "export_filename_template": settings.export_filename_template,
            "export_attachment_directory_template": (settings.export_attachment_directory_template),
            "export_data_directory_template": settings.export_data_directory_template,
            "export_conflict_policy": settings.export_conflict_policy,
            "export_keep_json_sidecar": settings.export_keep_json_sidecar,
            "export_copy_transcript": settings.export_copy_transcript,
            "export_copy_subtitles": settings.export_copy_subtitles,
        }
        models: dict[str, str | None] = {"asr": None, "ocr": None, "text_processing": None}
        text_model_config: ModelConfig | None = None
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
            text_model_config = profile.text_model
        if asr_model_id is not None:
            asr_model = self.session.get(ModelConfig, asr_model_id)
            if (
                asr_model is None
                or not asr_model.enabled
                or not any(item.capability == "ASR" for item in asr_model.capabilities)
            ):
                raise ConflictError("Selected ASR model is unavailable or lacks the ASR capability")
            models["asr"] = asr_model.name
        if text_model_id is not None:
            text_model = self.session.get(ModelConfig, text_model_id)
            if (
                text_model is None
                or not text_model.enabled
                or not any(item.capability == "TEXT_PROCESSING" for item in text_model.capabilities)
            ):
                raise ConflictError("Selected text model is unavailable or lacks TEXT_PROCESSING")
            models["text_processing"] = text_model.name
            text_model_config = text_model
        elif settings.default_text_model_name and not models["text_processing"]:
            models["text_processing"] = settings.default_text_model_name
            text_model_config = self.session.scalar(
                select(ModelConfig).where(
                    ModelConfig.name == settings.default_text_model_name,
                    ModelConfig.enabled.is_(True),
                )
            )
        asr_gateway_model = self._resolve_gateway_model(
            asr_gateway_model_id
            if asr_gateway_model_id is not None
            else (profile.asr_gateway_model_id if profile else None),
            "ASR",
        )
        ocr_gateway_model = self._resolve_gateway_model(
            ocr_gateway_model_id
            if ocr_gateway_model_id is not None
            else (profile.ocr_gateway_model_id if profile else None),
            "OCR",
        )
        allowed_overrides = set(processing)
        unknown = set(overrides) - allowed_overrides
        if unknown:
            raise ConflictError(f"Unsupported task config overrides: {', '.join(sorted(unknown))}")
        processing.update(copy.deepcopy(overrides))
        processing["manual_review_enabled"] = (
            pipeline_mode == "REAL" and not processing.get("text_edit_enabled", False)
        )
        if pipeline_mode == "REAL" and require_asr and asr_gateway_model is None:
            raise ConflictError("A real task must select an independent ASR gateway model")
        if (
            processing.get("ocr_enabled") or processing.get("embedded_subtitle_ocr_fallback")
        ) and ocr_gateway_model is None:
            raise ConflictError("An OCR-enabled task must select an independent OCR gateway model")
        if processing.get("text_edit_enabled"):
            if not models["text_processing"]:
                raise ConflictError("启用文本整理前必须配置并选择 TEXT_PROCESSING 模型")
            text_base_url = (
                (text_model_config.base_url if text_model_config else None)
                or settings.oneapi_base_url
                or os.getenv("ONEAPI_BASE_URL")
            )
            if not text_base_url:
                raise ConflictError("启用文本整理前必须在 LLM 页面配置 Base URL")
            if text_model_config and not credential_is_configured(text_model_config):
                if text_model_config.secret_env_name:
                    raise ConflictError(
                        f"缺少 LLM 密钥环境变量 {text_model_config.secret_env_name}"
                    )
                raise ConflictError("无法读取 LLM 页面中直接保存的密钥")
            if text_model_config is None and not os.getenv("ONEAPI_API_KEY", "").strip():
                raise ConflictError("缺少兼容配置使用的 ONEAPI_API_KEY 环境变量")
        if asr_gateway_model:
            models["asr"] = asr_gateway_model.model_name
        if ocr_gateway_model:
            models["ocr"] = ocr_gateway_model.model_name
        asr_gateway_snapshot = _gateway_model_snapshot(
            asr_gateway_model,
            timeout_seconds=get_settings().asr_request_timeout_seconds,
            max_retries=get_settings().asr_max_retries,
            requested_chunk_seconds=float(processing["audio_chunk_seconds"]),
            overlap_seconds=get_settings().asr_overlap_seconds,
        )
        ocr_gateway_snapshot = _gateway_model_snapshot(
            ocr_gateway_model,
            timeout_seconds=get_settings().asr_request_timeout_seconds,
            max_retries=get_settings().asr_max_retries,
            requested_chunk_seconds=None,
            overlap_seconds=0.0,
        )
        if asr_gateway_snapshot:
            processing["audio_chunk_seconds"] = asr_gateway_snapshot["chunking"]["chunk_seconds"]
        vault_path, inbox_relative_path = _export_target(settings)
        if export_directory is not None:
            vault_path = export_directory
            inbox_relative_path = "."
        elif export_vault_path is not None:
            vault_path = export_vault_path
        if export_inbox_relative_path is not None:
            inbox_relative_path = export_inbox_relative_path
        return {
            "schema_version": 6,
            "profile": profile_data,
            "models": models,
            "processing": processing,
            "paths": {
                "inbox": str(Path(vault_path) / inbox_relative_path),
                "data": settings.data_path or str(get_settings().data_dir),
            },
            "export": {
                "vault_path": vault_path,
                "inbox_relative_path": inbox_relative_path,
                "filename_template": processing["export_filename_template"],
                "attachment_directory_template": (
                    processing["export_attachment_directory_template"]
                ),
                "data_directory_template": processing["export_data_directory_template"],
                "conflict_policy": processing["export_conflict_policy"],
                "keep_json_sidecar": bool(processing["export_keep_json_sidecar"]),
                "copy_transcript": bool(processing["export_copy_transcript"]),
                "copy_subtitles": bool(processing["export_copy_subtitles"]),
            },
            "pipeline": pipeline_mode,
            "tools": {
                "ffmpeg": settings.ffmpeg_path or "ffmpeg",
                "ffprobe": _ffprobe_for(settings.ffmpeg_path),
                "ytdlp": settings.ytdlp_path or "yt-dlp",
            },
            "gateways": {"asr": asr_gateway_snapshot, "ocr": ocr_gateway_snapshot},
            "asr": (
                {
                    "gateway_id": asr_gateway_snapshot["gateway_id"],
                    "provider": asr_gateway_snapshot["protocol"],
                    "base_url": asr_gateway_snapshot["base_url"],
                    "secret_env_name": asr_gateway_snapshot["secret_env_name"],
                    "secret_source": asr_gateway_snapshot["secret_source"],
                    "model": asr_gateway_snapshot["model_name"],
                    "language": processing.get("asr_language"),
                    "timeout_seconds": asr_gateway_snapshot["timeout_seconds"],
                    "max_retries": asr_gateway_snapshot["max_retries"],
                    "overlap_seconds": asr_gateway_snapshot["chunking"]["overlap_seconds"],
                    "streaming": asr_gateway_snapshot["capabilities"]["supports_streaming"],
                }
                if asr_gateway_snapshot
                else None
            ),
            "ocr": (
                {
                    "gateway_id": ocr_gateway_snapshot["gateway_id"],
                    "provider": ocr_gateway_snapshot["protocol"],
                    "base_url": ocr_gateway_snapshot["base_url"],
                    "secret_env_name": ocr_gateway_snapshot["secret_env_name"],
                    "secret_source": ocr_gateway_snapshot["secret_source"],
                    "model": ocr_gateway_snapshot["model_name"],
                    "language": processing.get("ocr_language") or "ch",
                    "timeout_seconds": ocr_gateway_snapshot["timeout_seconds"],
                    "max_retries": ocr_gateway_snapshot["max_retries"],
                    "batch_size": int(processing.get("ocr_batch_size") or 1),
                    "capabilities": ocr_gateway_snapshot["capabilities"],
                }
                if ocr_gateway_snapshot
                else None
            ),
            "text_processing": {
                "provider": (
                    text_model_config.provider
                    if text_model_config and text_model_config.provider
                    else "oneapi"
                ),
                "protocol": "openai-chat-completions",
                "base_url": (
                    (text_model_config.base_url if text_model_config else None)
                    or settings.oneapi_base_url
                    or os.getenv("ONEAPI_BASE_URL", "")
                ),
                "model_config_id": text_model_config.id if text_model_config else None,
                "secret_env_name": (
                    text_model_config.secret_env_name if text_model_config else "ONEAPI_API_KEY"
                ),
                "secret_source": (
                    credential_source(text_model_config) if text_model_config else "ENVIRONMENT"
                ),
                "model": models["text_processing"],
                "enabled": bool(processing["text_edit_enabled"]),
                "timeout_seconds": get_settings().text_request_timeout_seconds,
                "prompt_version": processing["prompt_version"],
                "minimum_length_ratio": processing["faithfulness_min_length_ratio"],
                "formula_symbol_ratio": processing["faithfulness_formula_symbol_ratio"],
            },
        }

    def _resolve_gateway_model(
        self, gateway_model_id: int | None, gateway_type: str
    ) -> GatewayModel | None:
        if gateway_model_id is None:
            return None
        item = self.session.scalar(
            select(GatewayModel)
            .where(GatewayModel.id == gateway_model_id)
            .options(selectinload(GatewayModel.gateway))
        )
        if item is None:
            raise NotFoundError("Gateway model", gateway_model_id)
        if item.gateway.gateway_type != gateway_type:
            raise ConflictError(f"Selected gateway model is not an {gateway_type} model")
        if not item.enabled or not item.gateway.enabled:
            raise ConflictError("Selected gateway model or gateway is disabled")
        return item

    def _get_or_create_settings(self) -> AppSetting:
        item = self.session.get(AppSetting, 1)
        if item is None:
            item = AppSetting(id=1)
            self.session.add(item)
            self.session.flush()
        return item


def _task_snapshot(
    base_snapshot: dict[str, Any],
    task_input: TaskInput,
    *,
    title: str,
    pipeline_mode: str,
) -> dict[str, Any]:
    snapshot = copy.deepcopy(base_snapshot)
    snapshot["processing"]["text_source"] = task_input.text_source
    snapshot["input"] = {
        "source": task_input.source,
        "source_type": task_input.source_type,
        "title": title,
        "text_source": task_input.text_source,
    }
    if task_input.text_source == "SUBTITLE":
        snapshot["subtitle"] = {
            "mode": "URL_SUBTITLE_FETCH",
            "provider": "yt-dlp-subtitle",
            "source_url": task_input.source,
            "language_priority": [
                "zh-Hans",
                "zh-CN",
                "zh-Hant",
                "zh-TW",
                "zh-HK",
                "zh",
                "en",
                "en-orig",
            ],
            "embedded_ocr_fallback": bool(
                snapshot["processing"].get("embedded_subtitle_ocr_fallback")
            ),
            "embedded_ocr_interval_seconds": float(
                snapshot["processing"].get("embedded_subtitle_interval_seconds", 1.0)
            ),
        }
        snapshot["input"]["subtitle"] = copy.deepcopy(snapshot["subtitle"])
    snapshot["simulation"] = {
        "enabled": pipeline_mode == "SIMULATED",
        "failure_mode": task_input.simulate_failure,
    }
    return snapshot


def _validated_workspace(item: Task) -> Path | None:
    if not item.workspace_path:
        return None
    workspace = Path(item.workspace_path)
    if not workspace.exists():
        return None
    _reject_generated_links(workspace)
    try:
        workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise ConflictError("Task workspace cannot be resolved") from exc
    snapshot = item.snapshot.snapshot_json if item.snapshot else {}
    data_path = (snapshot.get("paths") or {}).get("data")
    if not isinstance(data_path, str) or not data_path.strip():
        raise ConflictError("Task snapshot does not contain its data root")
    data_root = Path(data_path).resolve(strict=False)
    if workspace == data_root or not workspace.is_relative_to(data_root):
        raise ConflictError("Task workspace is outside its recorded data root")
    if not workspace.name.endswith(f"_{item.id:06d}"):
        raise ConflictError("Task workspace name does not match the task ID")
    state = read_json(workspace / "task_state.json")
    if not state or state.get("task_id") != item.id:
        raise ConflictError("Task workspace identity check failed")
    return workspace


def _sync_workspace_snapshot(workspace: Path, snapshot: dict[str, Any]) -> None:
    write_json(workspace / "task_config.json", snapshot)
    disabled_marker = workspace / "03_ocr/DISABLED.txt"
    processing = snapshot.get("processing") or {}
    ocr_disabled = not processing.get("ocr_enabled") and not processing.get(
        "embedded_subtitle_ocr_fallback"
    )
    if ocr_disabled:
        disabled_marker.write_text(
            "OCR is disabled for this task snapshot. No OCR processing was performed.\n",
            encoding="utf-8",
        )
    else:
        disabled_marker.unlink(missing_ok=True)


def _delete_task_files(item: Task, *, protected_paths: list[Path] | None = None) -> dict[str, Any]:
    workspace = _validated_workspace(item)
    snapshot = item.snapshot.snapshot_json if item.snapshot else {}
    targets: list[Path] = []
    if workspace is not None:
        targets.append(workspace)

    source_path = None
    if item.source_type == "LOCAL_FILE":
        try:
            source_path = Path(item.source).resolve(strict=False)
        except OSError:
            source_path = None
        upload = (snapshot.get("input") or {}).get("upload")
        if (
            workspace is not None and source_path is not None and isinstance(upload, dict)
            and upload.get("owned_copy") is True and upload.get("path") == item.source
            and source_path.is_relative_to(workspace / "00_source" / "upload")
        ):
            source_path = None

    for export in item.markdown_exports:
        if export.target_type == "WORKSPACE" or export.status != "COMPLETED":
            continue
        export_root = _export_root(export.config_json)
        for value in (
            export.markdown_path,
            export.attachment_directory,
            export.data_directory,
        ):
            if value:
                targets.append(_validated_generated_target(Path(value), export_root, source_path))

    fallback_root_value = (snapshot.get("paths") or {}).get("inbox")
    fallback_root = (
        Path(fallback_root_value).resolve(strict=False)
        if isinstance(fallback_root_value, str) and fallback_root_value.strip()
        else None
    )
    for value in (item.output_path, item.export_path):
        if not value:
            continue
        candidate = Path(value)
        if workspace is not None and candidate.resolve(strict=False).is_relative_to(workspace):
            continue
        if fallback_root is None:
            raise ConflictError("Task output root is missing from its snapshot")
        targets.append(_validated_generated_target(candidate, fallback_root, source_path))

    unique_targets: list[Path] = []
    for target in targets:
        resolved = target.resolve(strict=False)
        if any(resolved == existing for existing in unique_targets):
            continue
        if workspace is not None and resolved != workspace and resolved.is_relative_to(workspace):
            continue
        unique_targets.append(resolved)

    files_removed = 0
    bytes_freed = 0
    deleted_paths: list[str] = []
    protected = list(protected_paths or [])
    if source_path is not None:
        protected.append(source_path)
    # Validate every target before the first deletion; protect nested source
    # files, cross-task outputs and Windows junctions as well as symlinks.
    from app.services.cache import _is_link

    for target in unique_targets:
        if any(target == path or path.is_relative_to(target) or target.is_relative_to(path)
               for path in protected):
            raise ConflictError("文件仍被原始输入或其他任务引用，不能永久删除")
        if target.exists() and _is_link(target):
            raise ConflictError("不能永久删除包含目录链接的生成物")
        if target.is_dir():
            def walk_error(error: OSError) -> None:
                raise error
            for directory, subdirectories, filenames in os.walk(target, followlinks=False,
                                                                onerror=walk_error):
                if any(_is_link(Path(directory) / name) for name in (*subdirectories, *filenames)):
                    raise ConflictError("不能永久删除包含目录链接的生成物")
    for target in sorted(unique_targets, key=lambda value: len(value.parts), reverse=True):
        if not target.exists():
            continue
        if target.is_symlink():
            raise ConflictError(f"Refusing to delete generated symlink: {target}")
        if target.is_dir():
            files = [path for path in target.rglob("*") if path.is_file() and not path.is_symlink()]
            if any(path.is_symlink() for path in target.rglob("*")):
                raise ConflictError(f"Refusing to delete a generated directory containing links: {target}")
            files_removed += len(files)
            bytes_freed += sum(_file_size(path) for path in files)
            shutil.rmtree(target)
        elif target.is_file():
            files_removed += 1
            bytes_freed += _file_size(target)
            target.unlink()
        else:
            raise ConflictError(f"Generated path is not a regular file or directory: {target}")
        deleted_paths.append(str(target))
    return {
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "deleted_paths": deleted_paths,
    }


def _export_root(config: dict[str, Any] | None) -> Path:
    config = config or {}
    vault = config.get("vault_path")
    relative = config.get("inbox_relative_path", ".")
    if not isinstance(vault, str) or not vault.strip():
        raise ConflictError("Completed export does not record its output root")
    if not isinstance(relative, str):
        raise ConflictError("Completed export has an invalid relative output path")
    root = (Path(vault) / relative).resolve(strict=False)
    vault_root = Path(vault).resolve(strict=False)
    if root != vault_root and not root.is_relative_to(vault_root):
        raise ConflictError("Completed export root escapes its recorded Vault")
    return root


def _validated_generated_target(
    candidate: Path,
    output_root: Path,
    source_path: Path | None,
) -> Path:
    _reject_generated_links(candidate)
    resolved = candidate.resolve(strict=False)
    root = output_root.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise ConflictError(f"Generated path is outside its recorded output root: {candidate}")
    if source_path is not None and resolved == source_path:
        raise ConflictError("Refusing to delete the original input file")
    return resolved


def _reject_generated_links(path: Path) -> None:
    from app.services.cache import _is_link

    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and _is_link(candidate):
            raise ConflictError("生成物路径包含符号链接或目录联接，无法安全永久删除")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _ffprobe_for(ffmpeg_path: str | None) -> str:
    if not ffmpeg_path:
        return "ffprobe"
    path = Path(ffmpeg_path)
    if path.parent == Path("."):
        return "ffprobe"
    suffix = path.suffix or (".exe" if os.name == "nt" else "")
    return str(path.with_name(f"ffprobe{suffix}"))


def _export_target(settings: AppSetting) -> tuple[str, str]:
    configured_inbox = settings.inbox_path
    if settings.obsidian_vault_path:
        vault = Path(settings.obsidian_vault_path)
        if configured_inbox and Path(configured_inbox).is_absolute():
            inbox = Path(configured_inbox)
            try:
                relative = inbox.relative_to(vault)
            except ValueError as exc:
                raise ConflictError(
                    "Absolute Inbox path must be inside the configured Obsidian Vault"
                ) from exc
            return str(vault), relative.as_posix() or "."
        relative = Path(configured_inbox or "Inbox")
        if relative.is_absolute() or ".." in relative.parts:
            raise ConflictError("Inbox path must be relative to the configured Vault")
        return str(vault), relative.as_posix()
    inbox = Path(configured_inbox) if configured_inbox else get_settings().inbox_dir
    if not inbox.is_absolute():
        raise ConflictError("A relative Inbox path requires a configured Obsidian Vault")
    return str(inbox.parent), inbox.name


def _gateway_model_snapshot(
    item: GatewayModel | None,
    *,
    timeout_seconds: float,
    max_retries: int,
    requested_chunk_seconds: float | None,
    overlap_seconds: float,
) -> dict[str, Any] | None:
    if item is None:
        return None
    gateway_settings = item.gateway.settings or {}
    try:
        effective_timeout_seconds = max(
            1.0, min(float(gateway_settings.get("timeout_seconds", timeout_seconds)), 3600.0)
        )
    except (TypeError, ValueError):
        effective_timeout_seconds = timeout_seconds
    try:
        effective_max_retries = max(
            0, min(int(gateway_settings.get("max_retries", max_retries)), 10)
        )
    except (TypeError, ValueError):
        effective_max_retries = max_retries
    native_continuous_stream = bool(
        item.supports_streaming and (item.capabilities or {}).get("continuous_session")
    )
    if native_continuous_stream:
        chunk_seconds = None
        effective_overlap_seconds = 0.0
        mode = "continuous_stream"
    else:
        requested = requested_chunk_seconds or item.max_chunk_seconds or 60.0
        if item.max_chunk_seconds is not None:
            chunk_seconds = min(requested, item.max_chunk_seconds)
        elif item.supports_native_long_audio:
            chunk_seconds = requested
        else:
            chunk_seconds = min(requested, 60.0)
        effective_overlap_seconds = min(overlap_seconds, max(0.0, chunk_seconds - 0.001))
        mode = "offline_overlap"
    if item.supports_word_timestamps:
        timestamp_strategy = "word"
    elif item.supports_segment_timestamps:
        timestamp_strategy = "segment"
    else:
        timestamp_strategy = "none"
    return {
        "gateway_id": item.gateway_id,
        "gateway_name": item.gateway.name,
        "gateway_type": item.gateway.gateway_type,
        "base_url": item.gateway.base_url,
        "protocol": item.gateway.protocol,
        "secret_env_name": item.gateway.secret_env_name,
        "secret_source": credential_source(item.gateway),
        "model_id": item.id,
        "model_name": item.model_name,
        "display_name": item.display_name,
        "capabilities": {
            **sanitize_gateway_metadata(item.capabilities or {}),
            "max_chunk_seconds": item.max_chunk_seconds,
            "max_alignment_seconds": item.max_alignment_seconds,
            "supports_native_long_audio": item.supports_native_long_audio,
            "supports_streaming": item.supports_streaming,
            "supports_segment_timestamps": item.supports_segment_timestamps,
            "supports_word_timestamps": item.supports_word_timestamps,
            "supports_confidence": item.supports_confidence,
            "supports_language_detection": item.supports_language_detection,
            "timestamp_granularity": item.timestamp_granularity,
            "languages": item.languages,
        },
        "gateway_settings": sanitize_gateway_metadata(item.gateway.settings or {}),
        "model_settings": sanitize_gateway_metadata(item.settings or {}),
        "chunking": {
            "mode": mode,
            "chunk_seconds": chunk_seconds,
            "overlap_seconds": effective_overlap_seconds,
            "step_seconds": (
                chunk_seconds - effective_overlap_seconds if chunk_seconds is not None else None
            ),
        },
        "timestamp_strategy": timestamp_strategy,
        "timeout_seconds": effective_timeout_seconds,
        "max_retries": effective_max_retries,
    }
