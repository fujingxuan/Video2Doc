from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Capability(StrEnum):
    ASR = "ASR"
    OCR = "OCR"
    TEXT_PROCESSING = "TEXT_PROCESSING"
    VISION = "VISION"


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[Capability] = Field(min_length=1)
    notes: str | None = None
    enabled: bool = True

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[Capability]) -> list[Capability]:
        return list(dict.fromkeys(value))


class ModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    capabilities: list[Capability] | None = None
    notes: str | None = None
    enabled: bool | None = None


class ModelRead(BaseModel):
    id: int
    name: str
    capabilities: list[Capability]
    notes: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ProfileFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    enabled: bool = True
    is_default: bool = False
    asr_model_id: int | None = None
    ocr_model_id: int | None = None
    text_model_id: int | None = None
    ocr_enabled: bool = False
    remove_fillers: bool = True
    fix_sentences: bool = True
    auto_paragraphs: bool = True
    generate_headings: bool = True
    keep_timestamps: bool = True
    keep_raw_transcript: bool = True
    keep_downloaded_media: bool = False
    ocr_frame_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    markdown_template: str | None = None
    output_directory: str | None = None
    data_directory: str | None = None


class ProfileCreate(ProfileFields):
    pass


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    asr_model_id: int | None = None
    ocr_model_id: int | None = None
    text_model_id: int | None = None
    ocr_enabled: bool | None = None
    remove_fillers: bool | None = None
    fix_sentences: bool | None = None
    auto_paragraphs: bool | None = None
    generate_headings: bool | None = None
    keep_timestamps: bool | None = None
    keep_raw_transcript: bool | None = None
    keep_downloaded_media: bool | None = None
    ocr_frame_interval_seconds: float | None = Field(default=None, gt=0, le=3600)
    markdown_template: str | None = None
    output_directory: str | None = None
    data_directory: str | None = None


class ProfileRead(ProfileFields):
    id: int
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


class SettingsUpdate(BaseModel):
    oneapi_base_url: str | None = None
    obsidian_vault_path: str | None = None
    inbox_path: str | None = None
    data_path: str | None = None
    ffmpeg_path: str | None = None
    ytdlp_path: str | None = None
    ocr_command: str | None = None
    last_profile_id: int | None = None
    default_markdown_template: str | None = None
    default_ocr_enabled: bool | None = None
    default_keep_raw_transcript: bool | None = None
    default_keep_timestamps: bool | None = None
    default_keep_downloaded_media: bool | None = None
    default_keep_frames: bool | None = None


class SettingsRead(BaseModel):
    id: int
    oneapi_base_url: str | None
    api_key_configured: bool
    obsidian_vault_path: str | None
    inbox_path: str | None
    data_path: str | None
    ffmpeg_path: str | None
    ytdlp_path: str | None
    ocr_command: str | None
    last_profile_id: int | None
    last_task_config: dict[str, Any] | None
    default_markdown_template: str | None
    default_ocr_enabled: bool
    default_keep_raw_transcript: bool
    default_keep_timestamps: bool
    default_keep_downloaded_media: bool
    default_keep_frames: bool
    updated_at: datetime


class TaskInput(BaseModel):
    source: str = Field(min_length=1)
    source_type: Literal["URL", "LOCAL_FILE"]
    title: str | None = Field(default=None, max_length=300)
    simulate_failure: Literal["never", "once", "always"] = "never"


class TaskBatchCreate(BaseModel):
    inputs: list[TaskInput] = Field(min_length=1, max_length=100)
    profile_id: int | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class SnapshotRead(BaseModel):
    schema_version: int
    snapshot_json: dict[str, Any]
    created_at: datetime


class StageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt: int
    stage: str
    status: str
    progress: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


class TaskRead(BaseModel):
    id: int
    source: str
    source_type: str
    title: str
    status: str
    current_stage: str
    progress: int
    cancel_requested: bool
    attempt: int
    error_code: str | None
    error_message: str | None
    workspace_path: str | None
    output_path: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskDetail(TaskRead):
    snapshot: SnapshotRead
    stages: list[StageRead]


class QueueStatus(BaseModel):
    max_concurrency: int = 1
    running: int
    queued: int
    completed: int
    failed: int
    cancelled: int
    interrupted: int
    running_task_id: int | None


class HealthRead(BaseModel):
    status: str
    service: str
    version: str
    database: str
    queue_worker: str
