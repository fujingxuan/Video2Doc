"""add three-category model credentials and provider endpoints

Revision ID: 0009_three_model_credentials
Revises: 0008_v2_traceable_inputs
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_three_model_credentials"
down_revision = "0008_v2_traceable_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_configs") as batch:
        batch.add_column(sa.Column("provider", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("base_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("secret_env_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("secret_ciphertext", sa.Text(), nullable=True))
    with op.batch_alter_table("gateway_configs") as batch:
        batch.add_column(sa.Column("secret_ciphertext", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE model_configs
            SET provider = 'oneapi',
                base_url = COALESCE(
                    (SELECT oneapi_base_url FROM app_settings WHERE id = 1),
                    'http://127.0.0.1:3000/v1'
                ),
                secret_env_name = 'ONEAPI_API_KEY'
            WHERE EXISTS (
                SELECT 1 FROM model_capabilities
                WHERE model_capabilities.model_id = model_configs.id
                  AND model_capabilities.capability = 'TEXT_PROCESSING'
            )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("gateway_configs") as batch:
        batch.drop_column("secret_ciphertext")
    with op.batch_alter_table("model_configs") as batch:
        batch.drop_column("secret_ciphertext")
        batch.drop_column("secret_env_name")
        batch.drop_column("base_url")
        batch.drop_column("provider")
