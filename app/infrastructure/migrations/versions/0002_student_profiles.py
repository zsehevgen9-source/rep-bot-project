"""student profiles

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_profiles",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("grade", sa.SmallInteger(), nullable=True),
        sa.Column("diagnostic_status", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("diagnostic_grade", sa.SmallInteger(), nullable=True),
        sa.Column("diagnostic_summary", sa.Text(), nullable=True),
        sa.Column("diagnostic_scores", sa.Text(), nullable=True),
        sa.Column("weak_topics", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("student_profiles")
