"""Allow audio preparation alongside a single inference task."""
from alembic import op
import sqlalchemy as sa

revision = "0014_pipeline_lanes"
down_revision = "0013_manual_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("processing_lane", sa.String(16),
                                    nullable=False, server_default="main"))
    op.drop_index("uq_tasks_single_processing", table_name="tasks")
    op.create_index("uq_tasks_processing_lane", "tasks", ["processing_lane"],
                    unique=True, sqlite_where=sa.text("status = 'PROCESSING'"))


def downgrade() -> None:
    # Refuse downgrade while two tasks are active rather than discard their state.
    op.create_index("uq_tasks_single_processing", "tasks", ["status"], unique=True,
                    sqlite_where=sa.text("status = 'PROCESSING'"))
    op.drop_index("uq_tasks_processing_lane", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("processing_lane")
