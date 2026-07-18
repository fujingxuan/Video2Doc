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
    return TaskDetail(
        **base,
        snapshot=SnapshotRead(
            schema_version=item.snapshot.schema_version,
            snapshot_json=item.snapshot.snapshot_json,
            created_at=item.snapshot.created_at,
        ),
        stages=[StageRead.model_validate(stage) for stage in item.stages],
    )
