import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import ReturnRequestCreate, ReturnRequestResponse
from app.services import return_service

router = APIRouter()


# Customer only: the customer id comes from the token, never from the body
@router.post("/returns", response_model=ReturnRequestResponse, status_code=status.HTTP_201_CREATED)
def create_return_request(
    body: ReturnRequestCreate,
    current_user: dict = Depends(require_role("customer")),
    db: Session = Depends(get_db),
):
    return return_service.create_return_request(
        db, uuid.UUID(current_user["user_id"]), body.order_number, body.order_item_id, body.reason
    )


@router.get("/returns", response_model=list[ReturnRequestResponse])
def list_return_requests(current_user: dict = Depends(require_role("customer")), db: Session = Depends(get_db)):
    return return_service.list_return_requests(db, uuid.UUID(current_user["user_id"]))


# Customers only see their own return requests (anything else is a 404); agents and admins see any
@router.get("/returns/{return_id}", response_model=ReturnRequestResponse)
def get_return_request(
    return_id: uuid.UUID,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    customer_id = uuid.UUID(current_user["user_id"]) if current_user["role"] == "customer" else None
    return return_service.get_return_request(db, return_id, customer_id)
