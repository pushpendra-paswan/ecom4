"""AI activity log columns on agent_runs (M10)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: what the run did (tool calls and retrieved knowledge-base chunks), empty lists for existing rows
    op.add_column(
        "agent_runs",
        sa.Column("tool_calls", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column("retrieved_chunks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )

    # Step 2: the reply text, the model and its token usage, and how long the run took
    op.add_column("agent_runs", sa.Column("reply_text", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("model_name", sa.String(length=100), nullable=True))
    op.add_column("agent_runs", sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("agent_runs", sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("agent_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "duration_ms")
    op.drop_column("agent_runs", "output_tokens")
    op.drop_column("agent_runs", "input_tokens")
    op.drop_column("agent_runs", "model_name")
    op.drop_column("agent_runs", "reply_text")
    op.drop_column("agent_runs", "retrieved_chunks")
    op.drop_column("agent_runs", "tool_calls")
