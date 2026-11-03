"""parents linked to student, weekly parent report

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("parents", sa.Text(), nullable=True))
    op.add_column("student_profiles", sa.Column("last_parent_report", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "last_parent_report")
    op.drop_column("student_profiles", "parents")
