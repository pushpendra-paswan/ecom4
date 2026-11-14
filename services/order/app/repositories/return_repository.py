import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReturnRequest


# customer_id is optional: customers pass their own id, staff pass None to see any request
def get_by_id(db: Session, return_request_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> ReturnRequest | None:
    query = select(ReturnRequest).where(ReturnRequest.id == return_request_id)
    if customer_id is not None:
        query = query.where(ReturnRequest.customer_id == customer_id)
    return db.scalar(query)


def get_by_order_item_id(db: Session, order_item_id: uuid.UUID) -> ReturnRequest | None:
    return db.scalar(select(ReturnRequest).where(ReturnRequest.order_item_id == order_item_id))


def list_for_customer(db: Session, customer_id: uuid.UUID) -> list[ReturnRequest]:
    query = select(ReturnRequest).where(ReturnRequest.customer_id == customer_id).order_by(ReturnRequest.created_at.desc())
    return list(db.scalars(query))


# Repositories only flush (send the SQL); the service decides when to commit
def create(
    db: Session,
    order_id: uuid.UUID,
    order_item_id: uuid.UUID,
    customer_id: uuid.UUID,
    reason: str,
    refund_amount: Decimal,
) -> ReturnRequest:
    return_request = ReturnRequest(
        order_id=order_id,
        order_item_id=order_item_id,
        customer_id=customer_id,
        reason=reason,
        refund_amount=refund_amount,
        status="requested",
    )
    db.add(return_request)
    db.flush()
    return return_request


def update_status(db: Session, return_request: ReturnRequest, status: str) -> ReturnRequest:
    return_request.status = status
    db.flush()
    return return_request
