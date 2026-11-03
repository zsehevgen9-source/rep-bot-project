"""daily tasks and notifications

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    sa.Column("chat_id", sa.BigInteger(), nullable=True),
    sa.Column("notify_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column("notify_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column("daily_time", sa.String(length=5), nullable=True),
    sa.Column("report_time", sa.String(length=5), nullable=True),
    sa.Column("tz_offset", sa.SmallInteger(), nullable=True),
    sa.Column("last_daily_date", sa.String(length=10), nullable=True),
    sa.Column("last_report_date", sa.String(length=10), nullable=True),
    sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("streak_days", sa.SmallInteger(), nullable=True),
    sa.Column("last_study_date", sa.String(length=10), nullable=True),
    sa.Column("activity", sa.Text(), nullable=True),
]


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("student_profiles", column)


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("student_profiles", column.name)
