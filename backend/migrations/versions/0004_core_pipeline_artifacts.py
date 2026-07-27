"""add core pipeline artifact references

Revision ID: 0004_core_pipeline_artifacts
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_core_pipeline_artifacts"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("media_info_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("transcript_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("artifacts_index_path", sa.Text(), nullable=True))
    with op.batch_alter_table("task_stage_runs") as batch:
        batch.add_column(sa.Column("message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("technical_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("retryable", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("input_artifacts", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("output_artifacts", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("log_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_stage_runs") as batch:
        batch.drop_column("log_path")
        batch.drop_column("output_artifacts")
        batch.drop_column("input_artifacts")
        batch.drop_column("technical_error")
        batch.drop_column("retryable")
        batch.drop_column("message")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("artifacts_index_path")
        batch.drop_column("transcript_path")
        batch.drop_column("media_info_path")
