import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem


# customer_id is optional: customers pass their own id, staff pass None to see any order.
# Items are loaded with their return request (M9), the shipment with the order.
def get_by_order_number(db: Session, order_number: str, customer_id: uuid.UUID | None = None) -> Order | None:
    query = (
        select(Order)
        .where(Order.order_number == order_number)
        .options(selectinload(Order.items).selectinload(OrderItem.return_request), selectinload(Order.shipment))
    )
    if customer_id is not None:
        query = query.where(Order.customer_id == customer_id)
    return db.scalar(query)


def list_for_customer(db: Session, customer_id: uuid.UUID) -> list[Order]:
    query = (
        select(Order)
        .where(Order.customer_id == customer_id)
        .options(selectinload(Order.items).selectinload(OrderItem.return_request), selectinload(Order.shipment))
        .order_by(Order.order_number)
    )
    return list(db.scalars(query))
