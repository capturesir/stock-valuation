"""ai_suggestions table (beta.2: T1/T2/T7 accept/dismiss with verified sources)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-28 01:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_suggestions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("task_key", sa.String(length=12), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("source_title", sa.String(length=240), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("source_date", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "task_key", "source_id", name="uq_ai_suggestions_source"),
    )
    op.create_index("ix_ai_suggestions_ticker", "ai_suggestions", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_ai_suggestions_ticker", table_name="ai_suggestions")
    op.drop_table("ai_suggestions")
