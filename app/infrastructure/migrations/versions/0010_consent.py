"""consent to personal data processing

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("consent_at", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "consent_at")
