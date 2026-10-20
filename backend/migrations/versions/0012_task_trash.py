"""Add reversible task deletion and a bounded history query index."""

from alembic import op
import sqlalchemy as sa


revision = "0012_task_trash"
down_revision = "0011_full_article_text_edit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("trash_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tasks_history_page", "tasks", ["deleted_at", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_history_page", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("trash_expires_at")
        batch.drop_column("deleted_at")
