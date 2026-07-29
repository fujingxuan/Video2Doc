"""add v2 stage model traceability and timestamp-free defaults

Revision ID: 0008_v2_traceable_inputs
Revises: 0007_document_exports
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_v2_traceable_inputs"
down_revision = "0007_document_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_stage_runs") as batch:
        batch.add_column(
            sa.Column(
                "model_usage",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    op.execute(
        sa.text("UPDATE processing_profiles SET keep_timestamps = 0")
    )
    op.execute(
        sa.text("UPDATE app_settings SET default_keep_timestamps = 0")
    )


def downgrade() -> None:
    with op.batch_alter_table("task_stage_runs") as batch:
        batch.drop_column("model_usage")
