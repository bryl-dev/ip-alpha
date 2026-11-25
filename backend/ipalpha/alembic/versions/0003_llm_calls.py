"""llm_calls telemetry table

Revision ID: 0003_llm_calls
Revises: 0002
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_llm_calls"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("service", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("schema_name", sa.String(100)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("gpu_seconds", sa.Float, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_node_created", "llm_calls", ["node", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_node_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
    op.drop_table("llm_calls")
