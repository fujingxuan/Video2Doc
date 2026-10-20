"""Persist manual review decisions so export can resume without transcription."""

from alembic import op
import sqlalchemy as sa

revision = "0013_manual_review"
down_revision = "0012_task_trash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("review_submission_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("review_submission_path")
