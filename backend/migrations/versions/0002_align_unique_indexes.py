"""Align unique indexes with SQLAlchemy metadata.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = (
    ("ix_model_configs_name", "model_configs", ["name"]),
    ("ix_processing_profiles_name", "processing_profiles", ["name"]),
    ("ix_task_config_snapshots_task_id", "task_config_snapshots", ["task_id"]),
)


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.drop_index(index_name, table_name=table_name)
        op.create_index(index_name, table_name, columns, unique=True)


def downgrade() -> None:
    for index_name, table_name, columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)
        op.create_index(index_name, table_name, columns, unique=False)
