"""add independent ASR and OCR gateway configuration

Revision ID: 0005_independent_model_gateways
Revises: 0004_core_pipeline_artifacts
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_independent_model_gateways"
down_revision = "0004_core_pipeline_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("gateway_type", sa.String(length=16), nullable=False),
        sa.Column("protocol", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("secret_env_name", sa.String(length=200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gateway_configs_name", "gateway_configs", ["name"], unique=True)
    op.create_index("ix_gateway_configs_gateway_type", "gateway_configs", ["gateway_type"])
    op.create_index("ix_gateway_configs_protocol", "gateway_configs", ["protocol"])
    op.create_index("ix_gateway_configs_enabled", "gateway_configs", ["enabled"])
    op.create_index("ix_gateway_configs_health_status", "gateway_configs", ["health_status"])

    op.create_table(
        "gateway_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gateway_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("max_chunk_seconds", sa.Float(), nullable=True),
        sa.Column("max_alignment_seconds", sa.Float(), nullable=True),
        sa.Column("supports_native_long_audio", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_segment_timestamps", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_word_timestamps", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_confidence", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_language_detection", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("timestamp_granularity", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("languages", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway_configs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("gateway_id", "model_name"),
    )
    op.create_index("ix_gateway_models_gateway_id", "gateway_models", ["gateway_id"])
    op.create_index("ix_gateway_models_model_name", "gateway_models", ["model_name"])
    op.create_index("ix_gateway_models_enabled", "gateway_models", ["enabled"])
    op.create_index("ix_gateway_models_health_status", "gateway_models", ["health_status"])

    with op.batch_alter_table("processing_profiles") as batch:
        batch.add_column(sa.Column("asr_gateway_model_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ocr_gateway_model_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_processing_profiles_asr_gateway_model",
            "gateway_models",
            ["asr_gateway_model_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_processing_profiles_ocr_gateway_model",
            "gateway_models",
            ["ocr_gateway_model_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("processing_profiles") as batch:
        batch.drop_constraint("fk_processing_profiles_ocr_gateway_model", type_="foreignkey")
        batch.drop_constraint("fk_processing_profiles_asr_gateway_model", type_="foreignkey")
        batch.drop_column("ocr_gateway_model_id")
        batch.drop_column("asr_gateway_model_id")
    op.drop_index("ix_gateway_models_health_status", table_name="gateway_models")
    op.drop_index("ix_gateway_models_enabled", table_name="gateway_models")
    op.drop_index("ix_gateway_models_model_name", table_name="gateway_models")
    op.drop_index("ix_gateway_models_gateway_id", table_name="gateway_models")
    op.drop_table("gateway_models")
    op.drop_index("ix_gateway_configs_health_status", table_name="gateway_configs")
    op.drop_index("ix_gateway_configs_enabled", table_name="gateway_configs")
    op.drop_index("ix_gateway_configs_protocol", table_name="gateway_configs")
    op.drop_index("ix_gateway_configs_gateway_type", table_name="gateway_configs")
    op.drop_index("ix_gateway_configs_name", table_name="gateway_configs")
    op.drop_table("gateway_configs")
