"""agent pipeline: document normalization columns, IP review queue, dead letters

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("event_type", sa.String(32)))
    op.add_column("documents", sa.Column("summary", sa.Text))
    op.add_column("documents", sa.Column("ip_mentions", ARRAY(sa.String)))
    op.add_column("documents", sa.Column("normalized_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("mapping", sa.String(16)))
    op.create_index(
        "ix_documents_pending_normalize",
        "documents",
        ["published_at"],
        postgresql_where=sa.text("normalized_at IS NULL"),
    )

    op.create_table(
        "ip_candidates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mention_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "example_document_id",
            sa.BigInteger,
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("stage", "ref"),
    )


def downgrade() -> None:
    op.drop_table("dead_letters")
    op.drop_table("ip_candidates")
    op.drop_index("ix_documents_pending_normalize", table_name="documents")
    for column in ("mapping", "normalized_at", "ip_mentions", "summary", "event_type"):
        op.drop_column("documents", column)
