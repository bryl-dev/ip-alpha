"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_ja", sa.String(200)),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
    )

    op.create_table(
        "ips",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("name_en", sa.String(200), nullable=False),
        sa.Column("name_ja", sa.String(200)),
        sa.Column("aliases", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("steam_appid", sa.Integer),
        sa.Column("mal_id", sa.Integer),
    )

    op.create_table(
        "ip_ownership",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ip_id", sa.Integer, sa.ForeignKey("ips.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "company_id",
            sa.Integer,
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("weight", sa.Float, nullable=False),
        sa.Column("valid_from", sa.Date),
        sa.Column("note", sa.Text),
        sa.UniqueConstraint("ip_id", "company_id", "role"),
    )

    op.create_table(
        "raw_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "metrics",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ip_id", sa.Integer, sa.ForeignKey("ips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_id", "source", "metric", "ts"),
    )
    op.create_index("ix_metrics_ip_metric_ts", "metrics", ["ip_id", "metric", "ts"])

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ip_id", sa.Integer, sa.ForeignKey("ips.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False, unique=True),
        sa.Column("url", sa.Text),
        sa.Column("lang", sa.String(8)),
        sa.Column("text_original", sa.Text, nullable=False),
        sa.Column("text_en", sa.Text),
        sa.Column("sentiment", sa.Float),
        sa.Column("embedding", Vector(1024)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.execute(
        "CREATE INDEX ix_documents_embedding ON documents USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "stock_prices",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("open", sa.Float),
        sa.Column("high", sa.Float),
        sa.Column("low", sa.Float),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Float),
        sa.UniqueConstraint("ticker", "date"),
    )

    op.create_table(
        "hype_scores",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ip_id", sa.Integer, sa.ForeignKey("ips.id", ondelete="CASCADE")),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("divergence", sa.Float),
        sa.Column("components", JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("ip_id", "date"),
        sa.UniqueConstraint("company_id", "date"),
        sa.CheckConstraint("(ip_id IS NULL) <> (company_id IS NULL)", name="ck_hype_one_owner"),
    )

    op.create_table(
        "theses",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("divergence", sa.Float),
        sa.Column("body", JSONB, nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("tokens", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    for table in (
        "theses",
        "hype_scores",
        "stock_prices",
        "documents",
        "metrics",
        "raw_events",
        "ip_ownership",
        "ips",
        "companies",
    ):
        op.drop_table(table)
