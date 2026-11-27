"""document library and Q&A tables (D13, alpha.5)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-28 01:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("doc_date", sa.String(length=16), nullable=True),
        sa.Column("form", sa.String(length=20), nullable=True),
        sa.Column("accession", sa.String(length=24), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("section_summaries", sa.JSON(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "sha256", name="uq_stock_documents_ticker_sha"),
    )
    op.create_index("ix_stock_documents_ticker", "stock_documents", ["ticker"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["stock_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "seq", name="uq_document_chunks_doc_seq"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_ticker", "document_chunks", ["ticker"])
    op.execute(
        "CREATE INDEX ix_document_chunks_fts ON document_chunks "
        "USING gin (to_tsvector('simple', coalesce(section, '') || ' ' || content))"
    )

    op.create_table(
        "qa_threads",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_threads_user_id", "qa_threads", ["user_id"])
    op.create_index("ix_qa_threads_ticker", "qa_threads", ["ticker"])

    op.create_table(
        "qa_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("used_web", sa.Boolean(), nullable=False),
        sa.Column("used_statements", sa.Boolean(), nullable=False),
        sa.Column("insufficient_evidence", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["qa_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_messages_thread_id", "qa_messages", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_qa_messages_thread_id", table_name="qa_messages")
    op.drop_table("qa_messages")
    op.drop_index("ix_qa_threads_ticker", table_name="qa_threads")
    op.drop_index("ix_qa_threads_user_id", table_name="qa_threads")
    op.drop_table("qa_threads")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_fts")
    op.drop_index("ix_document_chunks_ticker", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_stock_documents_ticker", table_name="stock_documents")
    op.drop_table("stock_documents")
