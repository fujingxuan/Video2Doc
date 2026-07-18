from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ModelConfig(TimestampMixin, Base):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    capabilities: Mapped[list[ModelCapability]] = relationship(
        back_populates="model", cascade="all, delete-orphan", lazy="selectin"
    )


class ModelCapability(Base):
    __tablename__ = "model_capabilities"
    __table_args__ = (UniqueConstraint("model_id", "capability"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model_configs.id", ondelete="CASCADE"), index=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[ModelConfig] = relationship(back_populates="capabilities")


class ProcessingProfile(TimestampMixin, Base):
    __tablename__ = "processing_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    asr_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="RESTRICT"), nullable=True
    )
    ocr_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="RESTRICT"), nullable=True
    )
    text_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="RESTRICT"), nullable=True
    )
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    remove_fillers: Mapped[bool] = mapped_column(Boolean, default=True)
    fix_sentences: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_paragraphs: Mapped[bool] = mapped_column(Boolean, default=True)
    generate_headings: Mapped[bool] = mapped_column(Boolean, default=True)
    keep_timestamps: Mapped[bool] = mapped_column(Boolean, default=True)
    keep_raw_transcript: Mapped[bool] = mapped_column(Boolean, default=True)
    keep_downloaded_media: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_frame_interval_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    markdown_template: Mapped[str | None] = mapped_column(Text)
    output_directory: Mapped[str | None] = mapped_column(Text)
    data_directory: Mapped[str | None] = mapped_column(Text)

    asr_model: Mapped[ModelConfig | None] = relationship(foreign_keys=[asr_model_id], lazy="selectin")
    ocr_model: Mapped[ModelConfig | None] = relationship(foreign_keys=[ocr_model_id], lazy="selectin")
    text_model: Mapped[ModelConfig | None] = relationship(foreign_keys=[text_model_id], lazy="selectin")


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    oneapi_base_url: Mapped[str | None] = mapped_column(Text)
    oneapi_api_key_ref: Mapped[str | None] = mapped_column(String(200), default="env:ONEAPI_API_KEY")
    obsidian_vault_path: Mapped[str | None] = mapped_column(Text)
    inbox_path: Mapped[str | None] = mapped_column(Text)
    data_path: Mapped[str | None] = mapped_column(Text)
    ffmpeg_path: Mapped[str | None] = mapped_column(Text)
    ytdlp_path: Mapped[str | None] = mapped_column(Text)
    ocr_command: Mapped[str | None] = mapped_column(Text)
    last_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_profiles.id", ondelete="SET NULL"), nullable=True
    )
    last_task_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    default_markdown_template: Mapped[str | None] = mapped_column(Text)
    default_ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    default_keep_raw_transcript: Mapped[bool] = mapped_column(Boolean, default=True)
    default_keep_timestamps: Mapped[bool] = mapped_column(Boolean, default=True)
    default_keep_downloaded_media: Mapped[bool] = mapped_column(Boolean, default=False)
    default_keep_frames: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), index=True, default="QUEUED")
    current_stage: Mapped[str] = mapped_column(String(64), default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    workspace_path: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshot: Mapped[TaskConfigSnapshot] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    stages: Mapped[list[TaskStageRun]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStageRun.id", lazy="selectin"
    )


class TaskConfigSnapshot(Base):
    __tablename__ = "task_config_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    task: Mapped[Task] = relationship(back_populates="snapshot")


class TaskStageRun(Base):
    __tablename__ = "task_stage_runs"
    __table_args__ = (UniqueConstraint("task_id", "attempt", "stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    task: Mapped[Task] = relationship(back_populates="stages")
