"""Enforce the single-processing-task queue invariant.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_tasks_single_processing",
        "tasks",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'PROCESSING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_single_processing", table_name="tasks")
