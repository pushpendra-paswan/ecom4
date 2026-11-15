import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Approval


# Only flushes (so a unique-constraint violation is raised here); the service commits
def create(
    db: Session,
    ticket_id: uuid.UUID,
    action_type: str,
    return_request_id: uuid.UUID,
    amount: Decimal,
    summary: str,
    status: str,
    auto_approved: bool,
    decided_at: datetime | None,
) -> Approval:
    approval = Approval(
        ticket_id=ticket_id,
        action_type=action_type,
        return_request_id=return_request_id,
        amount=amount,
        summary=summary,
        status=status,
        auto_approved=auto_approved,
        decided_at=decided_at,
    )
    db.add(approval)
    db.flush()
    return approval


def get_by_id(db: Session, approval_id: uuid.UUID) -> Approval | None:
    return db.scalar(select(Approval).where(Approval.id == approval_id))


# SELECT ... FOR UPDATE: the row stays locked until the transaction ends, so a second
# decision on the same approval waits here and then sees the first one's result.
# populate_existing makes sure we read the locked row's values, not an older copy in the session.
def get_by_id_for_update(db: Session, approval_id: uuid.UUID) -> Approval | None:
    query = (
        select(Approval)
        .where(Approval.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalar(query)


def get_by_return_request_id(db: Session, return_request_id: uuid.UUID) -> Approval | None:
    return db.scalar(select(Approval).where(Approval.return_request_id == return_request_id))


# Newest first, optionally only one status
def list_approvals(db: Session, status: str | None = None) -> list[Approval]:
    query = select(Approval).order_by(Approval.created_at.desc())
    if status is not None:
        query = query.where(Approval.status == status)
    return list(db.scalars(query))


def update(
    db: Session,
    approval: Approval,
    status: str,
    decided_by: uuid.UUID,
    decision_note: str | None,
    decided_at: datetime,
) -> Approval:
    approval.status = status
    approval.decided_by = decided_by
    approval.decision_note = decision_note
    approval.decided_at = decided_at
    db.flush()
    return approval
