"""add OCR and fusion artifact references

Revision ID: 0006_ocr_fusion_artifacts
Revises: 0005_independent_model_gateways
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_ocr_fusion_artifacts"
down_revision = "0005_independent_model_gateways"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("frames_manifest_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("ocr_timeline_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("fusion_timeline_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("fusion_timeline_path")
        batch.drop_column("ocr_timeline_path")
        batch.drop_column("frames_manifest_path")
