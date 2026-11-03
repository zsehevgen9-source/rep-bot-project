"""lesson schedule and marks

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("schedule", sa.Text(), nullable=True))
    op.add_column("student_profiles", sa.Column("lesson_marks", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "lesson_marks")
    op.drop_column("student_profiles", "schedule")
