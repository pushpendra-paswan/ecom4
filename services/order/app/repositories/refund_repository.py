import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Refund


def get_by_approval_id(db: Session, approval_id: uuid.UUID) -> Refund | None:
    return db.scalar(select(Refund).where(Refund.approval_id == approval_id))


def get_by_return_request_id(db: Session, return_request_id: uuid.UUID) -> Refund | None:
    return db.scalar(select(Refund).where(Refund.return_request_id == return_request_id))


# Only flushes (sends the SQL, so a unique-constraint violation is raised here); the service commits
def create(db: Session, return_request_id: uuid.UUID, approval_id: uuid.UUID, amount: Decimal) -> Refund:
    refund = Refund(
        return_request_id=return_request_id,
        approval_id=approval_id,
        amount=amount,
        status="completed",
    )
    db.add(refund)
    db.flush()
    return refund
