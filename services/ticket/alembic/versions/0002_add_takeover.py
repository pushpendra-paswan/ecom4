"""human takeover columns on tickets (M10)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The staff member who took over the conversation, and when (both empty while the AI may answer)
    op.add_column("tickets", sa.Column("taken_over_by", sa.Uuid(), nullable=True))
    op.add_column("tickets", sa.Column("taken_over_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "taken_over_at")
    op.drop_column("tickets", "taken_over_by")
