import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import EligibilityResponse, OrderResponse
from app.services import order_service, return_service

router = APIRouter()


@router.get("/orders", response_model=list[OrderResponse])
def list_orders(current_user: dict = Depends(require_role("customer")), db: Session = Depends(get_db)):
    return order_service.list_orders(db, uuid.UUID(current_user["user_id"]))


# Customers only see their own orders (anything else is a 404); agents and admins see any order
@router.get("/orders/{order_number}", response_model=OrderResponse)
def get_order(
    order_number: str,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    customer_id = uuid.UUID(current_user["user_id"]) if current_user["role"] == "customer" else None
    return order_service.get_order(db, order_number, customer_id)


@router.get("/orders/{order_number}/items/{item_id}/return-eligibility", response_model=EligibilityResponse)
def get_return_eligibility(
    order_number: str,
    item_id: uuid.UUID,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    customer_id = uuid.UUID(current_user["user_id"]) if current_user["role"] == "customer" else None
    return return_service.check_return_eligibility(db, order_number, item_id, customer_id)
