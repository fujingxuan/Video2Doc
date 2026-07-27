from __future__ import annotations

from app.models import AppSetting, ModelConfig, ProcessingProfile, Task
from app.schemas.api import (
    ModelRead,
    ProfileRead,
    SettingsRead,
    SnapshotRead,
    StageRead,
    TaskDetail,
    TaskRead,
)
from app.storage.workspace import list_artifacts, read_json
from pathlib import Path


def model_read(item: ModelConfig) -> ModelRead:
    return ModelRead(
        id=item.id,
        name=item.name,
        capabilities=[cap.capability for cap in item.capabilities],
        notes=item.notes,
        enabled=item.enabled,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def profile_read(item: ProcessingProfile) -> ProfileRead:
    return ProfileRead.model_validate(
        {column.name: getattr(item, column.name) for column in item.__table__.columns}
    )


def settings_read(item: AppSetting) -> SettingsRead:
    return SettingsRead(
        id=item.id,
        oneapi_base_url=item.oneapi_base_url,
        api_key_configured=False,
        obsidian_vault_path=item.obsidian_vault_path,
        inbox_path=item.inbox_path,
        data_path=item.data_path,
        ffmpeg_path=item.ffmpeg_path,
        ytdlp_path=item.ytdlp_path,
        ocr_command=item.ocr_command,
        last_profile_id=item.last_profile_id,
        last_task_config=item.last_task_config,
        default_markdown_template=item.default_markdown_template,
        default_ocr_enabled=item.default_ocr_enabled,
        default_keep_raw_transcript=item.default_keep_raw_transcript,
        default_keep_timestamps=item.default_keep_timestamps,
        default_keep_downloaded_media=item.default_keep_downloaded_media,
        default_keep_frames=item.default_keep_frames,
        updated_at=item.updated_at,
    )


def task_read(item: Task) -> TaskRead:
    return TaskRead(**{field: getattr(item, field) for field in TaskRead.model_fields})


def task_detail(item: Task) -> TaskDetail:
    base = task_read(item).model_dump()
    workspace = Path(item.workspace_path) if item.workspace_path else None
    media_info = read_json(Path(item.media_info_path)) if item.media_info_path else None
    preview_path = workspace / "02_asr/preview.json" if workspace else None
    transcript_preview = read_json(preview_path) if preview_path else None
    frame_manifest = (
        read_json(Path(item.frames_manifest_path)) if item.frames_manifest_path else None
    )
    frame_summary = (
        {
            key: frame_manifest.get(key)
            for key in (
                "candidate_count",
                "kept_count",
                "dropped_count",
                "drop_reasons",
                "config",
            )
        }
        if frame_manifest
        else None
    )
    ocr_preview = read_json(workspace / "03_ocr/preview.json") if workspace else None
    fusion_preview = (
        read_json(workspace / "04_fusion/preview.json") if workspace else None
    )
    return TaskDetail(
        **base,
        snapshot=SnapshotRead(
            schema_version=item.snapshot.schema_version,
            snapshot_json=item.snapshot.snapshot_json,
            created_at=item.snapshot.created_at,
        ),
        stages=[StageRead.model_validate(stage) for stage in item.stages],
        media_info=media_info,
        transcript_preview=transcript_preview,
        frame_summary=frame_summary,
        ocr_preview=ocr_preview,
        fusion_preview=fusion_preview,
        artifacts=list_artifacts(workspace) if workspace else [],
    )
