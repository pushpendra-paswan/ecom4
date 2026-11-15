"""add escalation_reason to agent_runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("escalation_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "escalation_reason")
