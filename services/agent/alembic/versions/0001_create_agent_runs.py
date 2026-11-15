"""create agent_runs table

Only our own table. LangGraph's checkpointer creates its checkpoint* tables itself, and env.py tells Alembic to ignore them.

Revision ID: 0001
Revises:
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_message_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("context_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_message_id", sa.Uuid(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trigger_message_id"),
    )
    op.create_index("ix_agent_runs_ticket_id", "agent_runs", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_ticket_id", table_name="agent_runs")
    op.drop_table("agent_runs")
