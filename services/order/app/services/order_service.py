import uuid

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import Order
from app.repositories import order_repository


# Customers pass their own customer_id (so another customer's order looks like it does not exist); staff pass None
def get_order(db: Session, order_number: str, customer_id: uuid.UUID | None = None) -> Order:
    # Step 1: load the order, with its items and shipment
    order = order_repository.get_by_order_number(db, order_number, customer_id)
    if order is None:
        raise NotFoundError("Order not found")

    # Step 2: return it
    return order


def list_orders(db: Session, customer_id: uuid.UUID) -> list[Order]:
    return order_repository.list_for_customer(db, customer_id)
