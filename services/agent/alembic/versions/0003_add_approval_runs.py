"""approval runs and paused runs in agent_runs (M9)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: approval runs have no trigger message
    op.alter_column("agent_runs", "trigger_message_id", existing_type=sa.Uuid(), nullable=True)

    # Step 2: the run type (existing rows are message runs), the approval that triggered an approval run (unique),
    # and the approval a paused message run waits for
    op.add_column("agent_runs", sa.Column("run_type", sa.String(length=20), server_default="message", nullable=False))
    op.add_column("agent_runs", sa.Column("trigger_approval_id", sa.Uuid(), nullable=True))
    op.add_column("agent_runs", sa.Column("paused_for_approval_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("agent_runs_trigger_approval_id_key", "agent_runs", ["trigger_approval_id"])


def downgrade() -> None:
    op.drop_constraint("agent_runs_trigger_approval_id_key", "agent_runs", type_="unique")
    op.drop_column("agent_runs", "paused_for_approval_id")
    op.drop_column("agent_runs", "trigger_approval_id")
    op.drop_column("agent_runs", "run_type")
    op.alter_column("agent_runs", "trigger_message_id", existing_type=sa.Uuid(), nullable=False)
