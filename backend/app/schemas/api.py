from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Capability(StrEnum):
    ASR = "ASR"
    OCR = "OCR"
    TEXT_PROCESSING = "TEXT_PROCESSING"
    VISION = "VISION"


class GatewayType(StrEnum):
    LLM = "LLM"
    ASR = "ASR"
    OCR = "OCR"


class GatewayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    gateway_type: GatewayType
    protocol: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1)
    secret_env_name: str | None = Field(default=None, max_length=200)
    secret_value: str | None = Field(default=None, max_length=20_000, repr=False)
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return _validate_gateway_base_url(value)

    @field_validator("secret_env_name")
    @classmethod
    def valid_secret_env_name(cls, value: str | None) -> str | None:
        return _validate_secret_env_name(value)

    @field_validator("settings")
    @classmethod
    def settings_do_not_contain_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_sensitive_metadata(value, "settings")
        return value

    @model_validator(mode="after")
    def one_secret_source(self) -> GatewayCreate:
        if self.secret_env_name and self.secret_value:
            raise ValueError("Choose an environment variable or a direct credential, not both")
        return self


class GatewayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    gateway_type: GatewayType | None = None
    protocol: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1)
    secret_env_name: str | None = Field(default=None, max_length=200)
    secret_value: str | None = Field(default=None, max_length=20_000, repr=False)
    clear_secret: bool = False
    enabled: bool | None = None
    settings: dict[str, Any] | None = None

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        return _validate_gateway_base_url(value) if value is not None else None

    @field_validator("secret_env_name")
    @classmethod
    def valid_secret_env_name(cls, value: str | None) -> str | None:
        return _validate_secret_env_name(value)

    @field_validator("settings")
    @classmethod
    def settings_do_not_contain_secrets(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is not None:
            _reject_sensitive_metadata(value, "settings")
        return value

    @model_validator(mode="after")
    def one_secret_source(self) -> GatewayUpdate:
        if self.secret_env_name and self.secret_value:
            raise ValueError("Choose an environment variable or a direct credential, not both")
        return self


class GatewayRead(BaseModel):
    id: int
    name: str
    gateway_type: GatewayType
    protocol: str
    base_url: str
    secret_env_name: str | None
    secret_source: Literal["NONE", "ENVIRONMENT", "DIRECT"]
    secret_configured: bool
    enabled: bool
    health_status: str
    last_checked_at: datetime | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GatewayModelFields(BaseModel):
    gateway_id: int
    model_name: str = Field(min_length=1, max_length=300)
    display_name: str = Field(min_length=1, max_length=300)
    enabled: bool = True
    capabilities: dict[str, Any] = Field(default_factory=dict)
    max_chunk_seconds: float | None = Field(default=None, gt=0)
    max_alignment_seconds: float | None = Field(default=None, gt=0)
    supports_native_long_audio: bool = False
    supports_streaming: bool = False
    supports_segment_timestamps: bool = False
    supports_word_timestamps: bool = False
    supports_confidence: bool = False
    supports_language_detection: bool = False
    timestamp_granularity: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities", "settings")
    @classmethod
    def metadata_does_not_contain_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_sensitive_metadata(value, "gateway model metadata")
        return value


class GatewayModelCreate(GatewayModelFields):
    pass


class GatewayModelUpdate(BaseModel):
    gateway_id: int | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=300)
    display_name: str | None = Field(default=None, min_length=1, max_length=300)
    enabled: bool | None = None
    capabilities: dict[str, Any] | None = None
    max_chunk_seconds: float | None = Field(default=None, gt=0)
    max_alignment_seconds: float | None = Field(default=None, gt=0)
    supports_native_long_audio: bool | None = None
    supports_streaming: bool | None = None
    supports_segment_timestamps: bool | None = None
    supports_word_timestamps: bool | None = None
    supports_confidence: bool | None = None
    supports_language_detection: bool | None = None
    timestamp_granularity: list[str] | None = None
    languages: list[str] | None = None
    settings: dict[str, Any] | None = None

    @field_validator("capabilities", "settings")
    @classmethod
    def metadata_does_not_contain_secrets(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is not None:
            _reject_sensitive_metadata(value, "gateway model metadata")
        return value


class GatewayModelRead(GatewayModelFields):
    id: int
    gateway_name: str
    gateway_type: GatewayType
    gateway_base_url: str
    protocol: str
    health_status: str
    created_at: datetime
    updated_at: datetime


class GatewayHealthRead(BaseModel):
    gateway_id: int
    status: str
    detail: str | None = None
    checked_at: datetime


def _validate_gateway_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("Gateway Base URL must be an absolute HTTP(S) or WS(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Gateway Base URL cannot contain credentials")
    return normalized


def _validate_secret_env_name(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized) is None:
        raise ValueError("Secret reference must be an environment variable name")
    return normalized


def _reject_sensitive_metadata(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if is_sensitive_metadata_key(str(key)):
                raise ValueError(
                    f"{location} cannot store credentials; use secret_env_name instead"
                )
            _reject_sensitive_metadata(item, location)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_metadata(item, location)


def is_sensitive_metadata_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized in {
        "api_key",
        "apikey",
        "authorization",
        "token",
        "bearer_token",
        "access_token",
        "password",
        "secret",
        "credential",
        "credentials",
    }:
        return True
    return normalized.startswith(("secret_", "password_", "credential_")) or normalized.endswith(
        ("_api_key", "_access_token", "_bearer_token", "_password", "_secret")
    )


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[Capability] = Field(min_length=1)
    notes: str | None = None
    enabled: bool = True
    provider: str | None = Field(default=None, max_length=100)
    base_url: str | None = None
    secret_env_name: str | None = Field(default=None, max_length=200)
    secret_value: str | None = Field(default=None, max_length=20_000, repr=False)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[Capability]) -> list[Capability]:
        return list(dict.fromkeys(value))

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        return _validate_gateway_base_url(value) if value else None

    @field_validator("secret_env_name")
    @classmethod
    def valid_secret_env_name(cls, value: str | None) -> str | None:
        return _validate_secret_env_name(value)

    @model_validator(mode="after")
    def one_secret_source(self) -> ModelCreate:
        if self.secret_env_name and self.secret_value:
            raise ValueError("Choose an environment variable or a direct credential, not both")
        return self


class ModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    capabilities: list[Capability] | None = None
    notes: str | None = None
    enabled: bool | None = None
    provider: str | None = Field(default=None, max_length=100)
    base_url: str | None = None
    secret_env_name: str | None = Field(default=None, max_length=200)
    secret_value: str | None = Field(default=None, max_length=20_000, repr=False)
    clear_secret: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        return _validate_gateway_base_url(value) if value else None

    @field_validator("secret_env_name")
    @classmethod
    def valid_secret_env_name(cls, value: str | None) -> str | None:
        return _validate_secret_env_name(value)

    @model_validator(mode="after")
    def one_secret_source(self) -> ModelUpdate:
        if self.secret_env_name and self.secret_value:
            raise ValueError("Choose an environment variable or a direct credential, not both")
        return self


class ModelRead(BaseModel):
    id: int
    name: str
    capabilities: list[Capability]
    notes: str | None
    enabled: bool
    provider: str | None
    base_url: str | None
    secret_env_name: str | None
    secret_source: Literal["NONE", "ENVIRONMENT", "DIRECT"]
    secret_configured: bool
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
    asr_gateway_model_id: int | None = None
    ocr_gateway_model_id: int | None = None
    ocr_enabled: bool = False
    remove_fillers: bool = True
    fix_sentences: bool = True
    auto_paragraphs: bool = True
    generate_headings: bool = True
    keep_timestamps: bool = False
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
    asr_gateway_model_id: int | None = None
    ocr_gateway_model_id: int | None = None
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


class ProfileModelSelection(BaseModel):
    capability: Literal["ASR", "OCR", "TEXT_PROCESSING"]
    source: Literal["GATEWAY", "MODEL_CONFIG"]
    model_id: int
    model_name: str
    display_name: str
    gateway_id: int | None = None
    gateway_name: str | None = None
    protocol: str | None = None


class ProfileRead(ProfileFields):
    id: int
    is_builtin: bool
    asr_selection: ProfileModelSelection | None = None
    ocr_selection: ProfileModelSelection | None = None
    text_selection: ProfileModelSelection | None = None
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
    default_text_edit_enabled: bool | None = None
    default_text_model_name: str | None = Field(default=None, max_length=300)
    text_prompt_version: str | None = Field(default=None, max_length=100)
    faithfulness_min_length_ratio: float | None = Field(default=None, ge=0.5, le=1.0)
    faithfulness_formula_symbol_ratio: float | None = Field(
        default=None, ge=0.5, le=1.0
    )
    markdown_template_version: str | None = Field(default=None, max_length=100)
    image_insertion_policy: str | None = Field(default=None, max_length=100)
    image_min_gap_seconds: float | None = Field(default=None, ge=0, le=3600)
    export_filename_template: str | None = Field(default=None, max_length=300)
    export_attachment_directory_template: str | None = Field(
        default=None, max_length=300
    )
    export_data_directory_template: str | None = Field(default=None, max_length=300)
    export_conflict_policy: Literal["rename", "fail", "overwrite"] | None = None
    export_keep_json_sidecar: bool | None = None
    export_copy_transcript: bool | None = None
    export_copy_subtitles: bool | None = None


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
    default_text_edit_enabled: bool
    default_text_model_name: str | None
    text_prompt_version: str
    faithfulness_min_length_ratio: float
    faithfulness_formula_symbol_ratio: float
    markdown_template_version: str
    image_insertion_policy: str
    image_min_gap_seconds: float
    export_filename_template: str
    export_attachment_directory_template: str
    export_data_directory_template: str
    export_conflict_policy: Literal["rename", "fail", "overwrite"]
    export_keep_json_sidecar: bool
    export_copy_transcript: bool
    export_copy_subtitles: bool
    updated_at: datetime


class TaskInput(BaseModel):
    source: str = Field(min_length=1)
    source_type: Literal["URL", "LOCAL_FILE"]
    title: str | None = Field(default=None, max_length=300)
    simulate_failure: Literal["never", "once", "always"] = "never"
    text_source: Literal["ASR", "SUBTITLE"] = "ASR"

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("输入来源不能为空或包含 NUL 字符")
        return normalized

    @model_validator(mode="after")
    def subtitle_requires_video_url(self) -> TaskInput:
        if self.text_source == "SUBTITLE" and self.source_type != "URL":
            raise ValueError("从视频链接抓取字幕仅支持 URL 输入")
        return self


class TaskBatchCreate(BaseModel):
    inputs: list[TaskInput] = Field(min_length=1, max_length=100)
    profile_id: int | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    pipeline_mode: Literal["REAL", "SIMULATED"] = "REAL"
    asr_model_id: int | None = None
    asr_gateway_model_id: int | None = None
    ocr_gateway_model_id: int | None = None
    text_model_id: int | None = None
    export_directory: str | None = None
    export_vault_path: str | None = None
    export_inbox_relative_path: str | None = None

    @field_validator("export_directory")
    @classmethod
    def export_directory_must_be_absolute(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if "\x00" in normalized or not Path(normalized).is_absolute():
            raise ValueError("Export directory must be an absolute path")
        return normalized

    @field_validator("export_vault_path")
    @classmethod
    def export_vault_must_be_absolute(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if "\x00" in normalized or not Path(normalized).is_absolute():
            raise ValueError("Export Vault path must be an absolute path")
        return normalized

    @field_validator("export_inbox_relative_path")
    @classmethod
    def export_inbox_must_be_relative(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        path = Path(normalized)
        if "\x00" in normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError("Export Inbox must be a safe relative path")
        return normalized


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
    message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    technical_error: str | None
    retryable: bool | None
    input_artifacts: list[str] | None
    output_artifacts: list[str] | None
    model_usage: list[dict[str, Any]] = Field(default_factory=list)
    log_path: str | None


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
    media_info_path: str | None
    transcript_path: str | None
    frames_manifest_path: str | None
    ocr_timeline_path: str | None
    fusion_timeline_path: str | None
    artifacts_index_path: str | None
    document_path: str | None
    markdown_path: str | None
    export_path: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskDetail(TaskRead):
    snapshot: SnapshotRead
    stages: list[StageRead]
    media_info: dict[str, Any] | None = None
    transcript_preview: dict[str, Any] | None = None
    frame_summary: dict[str, Any] | None = None
    ocr_preview: dict[str, Any] | None = None
    fusion_preview: dict[str, Any] | None = None
    document_preview: dict[str, Any] | None = None
    markdown_preview: str | None = None
    exports: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


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
