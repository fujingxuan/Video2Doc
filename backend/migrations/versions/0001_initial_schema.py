"""Create the first-round Video2Doc schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_model_configs_name", "model_configs", ["name"])
    op.create_index("ix_model_configs_enabled", "model_configs", ["enabled"])
    op.create_table(
        "model_capabilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["model_configs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("model_id", "capability"),
    )
    op.create_index("ix_model_capabilities_model_id", "model_capabilities", ["model_id"])
    op.create_index("ix_model_capabilities_capability", "model_capabilities", ["capability"])
    op.create_table(
        "processing_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("asr_model_id", sa.Integer(), nullable=True),
        sa.Column("ocr_model_id", sa.Integer(), nullable=True),
        sa.Column("text_model_id", sa.Integer(), nullable=True),
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False),
        sa.Column("remove_fillers", sa.Boolean(), nullable=False),
        sa.Column("fix_sentences", sa.Boolean(), nullable=False),
        sa.Column("auto_paragraphs", sa.Boolean(), nullable=False),
        sa.Column("generate_headings", sa.Boolean(), nullable=False),
        sa.Column("keep_timestamps", sa.Boolean(), nullable=False),
        sa.Column("keep_raw_transcript", sa.Boolean(), nullable=False),
        sa.Column("keep_downloaded_media", sa.Boolean(), nullable=False),
        sa.Column("ocr_frame_interval_seconds", sa.Float(), nullable=False),
        sa.Column("markdown_template", sa.Text(), nullable=True),
        sa.Column("output_directory", sa.Text(), nullable=True),
        sa.Column("data_directory", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asr_model_id"], ["model_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ocr_model_id"], ["model_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["text_model_id"], ["model_configs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_processing_profiles_name", "processing_profiles", ["name"])
    op.create_index("ix_processing_profiles_is_default", "processing_profiles", ["is_default"])
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oneapi_base_url", sa.Text(), nullable=True),
        sa.Column("oneapi_api_key_ref", sa.String(length=200), nullable=True),
        sa.Column("obsidian_vault_path", sa.Text(), nullable=True),
        sa.Column("inbox_path", sa.Text(), nullable=True),
        sa.Column("data_path", sa.Text(), nullable=True),
        sa.Column("ffmpeg_path", sa.Text(), nullable=True),
        sa.Column("ytdlp_path", sa.Text(), nullable=True),
        sa.Column("ocr_command", sa.Text(), nullable=True),
        sa.Column("last_profile_id", sa.Integer(), nullable=True),
        sa.Column("last_task_config", sa.JSON(), nullable=True),
        sa.Column("default_markdown_template", sa.Text(), nullable=True),
        sa.Column("default_ocr_enabled", sa.Boolean(), nullable=False),
        sa.Column("default_keep_raw_transcript", sa.Boolean(), nullable=False),
        sa.Column("default_keep_timestamps", sa.Boolean(), nullable=False),
        sa.Column("default_keep_downloaded_media", sa.Boolean(), nullable=False),
        sa.Column("default_keep_frames", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_profile_id"], ["processing_profiles.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_source_type", "tasks", ["source_type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_table(
        "task_config_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_task_config_snapshots_task_id", "task_config_snapshots", ["task_id"])
    op.create_table(
        "task_stage_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "attempt", "stage"),
    )
    op.create_index("ix_task_stage_runs_task_id", "task_stage_runs", ["task_id"])


def downgrade() -> None:
    op.drop_table("task_stage_runs")
    op.drop_table("task_config_snapshots")
    op.drop_table("tasks")
    op.drop_table("app_settings")
    op.drop_table("processing_profiles")
    op.drop_table("model_capabilities")
    op.drop_table("model_configs")
