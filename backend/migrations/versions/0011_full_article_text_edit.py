"""switch text editing to full-article prompt

Revision ID: 0011_full_article_text_edit
Revises: 0010_task_cache_cleanup
"""

from alembic import op


revision = "0011_full_article_text_edit"
down_revision = "0010_task_cache_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE app_settings "
        "SET text_prompt_version = 'faithful-article-v1' "
        "WHERE text_prompt_version = 'faithful-edit-v1'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE app_settings "
        "SET text_prompt_version = 'faithful-edit-v1' "
        "WHERE text_prompt_version = 'faithful-article-v1'"
    )
