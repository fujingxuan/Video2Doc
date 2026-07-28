"""add document, faithful edit, and Obsidian export records

Revision ID: 0007_document_exports
Revises: 0006_ocr_fusion_artifacts
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_document_exports"
down_revision = "0006_ocr_fusion_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(
            sa.Column(
                "default_text_edit_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("default_text_model_name", sa.String(300)))
        batch.add_column(
            sa.Column(
                "text_prompt_version",
                sa.String(100),
                nullable=False,
                server_default="faithful-edit-v1",
            )
        )
        batch.add_column(
            sa.Column(
                "faithfulness_min_length_ratio",
                sa.Float(),
                nullable=False,
                server_default="0.72",
            )
        )
        batch.add_column(
            sa.Column(
                "faithfulness_formula_symbol_ratio",
                sa.Float(),
                nullable=False,
                server_default="0.8",
            )
        )
        batch.add_column(
            sa.Column(
                "markdown_template_version",
                sa.String(100),
                nullable=False,
                server_default="obsidian-v1",
            )
        )
        batch.add_column(
            sa.Column(
                "image_insertion_policy",
                sa.String(100),
                nullable=False,
                server_default="scene-and-interval",
            )
        )
        batch.add_column(
            sa.Column(
                "image_min_gap_seconds",
                sa.Float(),
                nullable=False,
                server_default="8.0",
            )
        )
        batch.add_column(
            sa.Column(
                "export_filename_template",
                sa.Text(),
                nullable=False,
                server_default="{title}",
            )
        )
        batch.add_column(
            sa.Column(
                "export_attachment_directory_template",
                sa.Text(),
                nullable=False,
                server_default="{stem}.assets",
            )
        )
        batch.add_column(
            sa.Column(
                "export_data_directory_template",
                sa.Text(),
                nullable=False,
                server_default="{stem}.data",
            )
        )
        batch.add_column(
            sa.Column(
                "export_conflict_policy",
                sa.String(32),
                nullable=False,
                server_default="rename",
            )
        )
        batch.add_column(
            sa.Column(
                "export_keep_json_sidecar",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "export_copy_transcript",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "export_copy_subtitles",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("document_path", sa.Text()))
        batch.add_column(sa.Column("markdown_path", sa.Text()))
        batch.add_column(sa.Column("export_path", sa.Text()))

    op.create_table(
        "document_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("document_path", sa.Text(), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("source_reference_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "attempt"),
    )
    op.create_index(
        "ix_document_artifacts_task_id",
        "document_artifacts",
        ["task_id"],
    )
    op.create_table(
        "text_edit_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(300)),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("raw_response_path", sa.Text()),
        sa.Column("validation_path", sa.Text(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "attempt"),
    )
    op.create_index("ix_text_edit_runs_task_id", "text_edit_runs", ["task_id"])
    op.create_table(
        "markdown_exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("markdown_path", sa.Text()),
        sa.Column("attachment_directory", sa.Text()),
        sa.Column("data_directory", sa.Text()),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("sidecar_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "attempt", "target_type"),
    )
    op.create_index("ix_markdown_exports_task_id", "markdown_exports", ["task_id"])
    op.create_index("ix_markdown_exports_status", "markdown_exports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_markdown_exports_status", table_name="markdown_exports")
    op.drop_index("ix_markdown_exports_task_id", table_name="markdown_exports")
    op.drop_table("markdown_exports")
    op.drop_index("ix_text_edit_runs_task_id", table_name="text_edit_runs")
    op.drop_table("text_edit_runs")
    op.drop_index("ix_document_artifacts_task_id", table_name="document_artifacts")
    op.drop_table("document_artifacts")

    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("export_path")
        batch.drop_column("markdown_path")
        batch.drop_column("document_path")

    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("export_copy_subtitles")
        batch.drop_column("export_copy_transcript")
        batch.drop_column("export_keep_json_sidecar")
        batch.drop_column("export_conflict_policy")
        batch.drop_column("export_data_directory_template")
        batch.drop_column("export_attachment_directory_template")
        batch.drop_column("export_filename_template")
        batch.drop_column("image_min_gap_seconds")
        batch.drop_column("image_insertion_policy")
        batch.drop_column("markdown_template_version")
        batch.drop_column("faithfulness_formula_symbol_ratio")
        batch.drop_column("faithfulness_min_length_ratio")
        batch.drop_column("text_prompt_version")
        batch.drop_column("default_text_model_name")
        batch.drop_column("default_text_edit_enabled")
