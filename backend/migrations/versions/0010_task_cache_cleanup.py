"""add automatic task cache cleanup setting

Revision ID: 0010_task_cache_cleanup
Revises: 0009_three_model_credentials
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_task_cache_cleanup"
down_revision = "0009_three_model_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(
            sa.Column(
                "auto_clear_task_cache",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("auto_clear_task_cache")
