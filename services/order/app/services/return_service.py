import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import BadRequestError, ConflictError, NotFoundError
from app.models import ReturnRequest
from app.repositories import order_repository, return_repository

# Return rules: days counted from the delivery date, a return is allowed when days_since_delivery <= window
DEFAULT_RETURN_WINDOW_DAYS = 30
ELECTRONICS_RETURN_WINDOW_DAYS = 15
NON_RETURNABLE_CATEGORIES = ("gift_card", "perishable")


# Decides whether one order item (the whole line) can be returned. This is the only place these rules live.
# Customers pass their own customer_id; staff pass None.
def check_return_eligibility(
    db: Session,
    order_number: str,
    order_item_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
) -> dict:
    # The result always has these six keys; the steps below fill in what applies
    result = {
        "eligible": False,
        "reason_code": None,
        "reason": None,
        "refund_amount": None,
        "window_days": None,
        "days_remaining": None,
    }

    # Step 1: load the order (a customer only finds their own) and make sure the item belongs to it
    order = order_repository.get_by_order_number(db, order_number, customer_id)
    if order is None:
        raise NotFoundError("Order not found")
    item = next((order_item for order_item in order.items if order_item.id == order_item_id), None)
    if item is None:
        raise NotFoundError("Order item not found")

    # Step 2: only delivered orders can be returned
    delivered_on = order.shipment.delivered_on if order.shipment is not None else None
    if order.status != "delivered" or delivered_on is None:
        result["reason_code"] = "not_delivered"
        result["reason"] = f"The order has not been delivered yet (status: {order.status})"
        return result

    # Step 3: some categories can never be returned
    if item.category in NON_RETURNABLE_CATEGORIES:
        result["reason_code"] = "non_returnable"
        result["reason"] = f"Items in the '{item.category}' category cannot be returned"
        return result

    # Step 4: check the return window (electronics get a shorter one)
    window_days = ELECTRONICS_RETURN_WINDOW_DAYS if item.category == "electronics" else DEFAULT_RETURN_WINDOW_DAYS
    days_since_delivery = (date.today() - delivered_on).days
    result["window_days"] = window_days
    result["days_remaining"] = max(window_days - days_since_delivery, 0)
    if days_since_delivery > window_days:
        result["reason_code"] = "window_expired"
        result["reason"] = f"The {window_days}-day return window ended {days_since_delivery - window_days} day(s) ago"
        return result

    # Step 5: an item can be returned only once
    if return_repository.get_by_order_item_id(db, item.id) is not None:
        result["reason_code"] = "already_requested"
        result["reason"] = "A return has already been requested for this item"
        return result

    # Step 6: eligible, the refund is the whole line
    result["eligible"] = True
    result["reason_code"] = "eligible"
    result["reason"] = f"This item can be returned within {result['days_remaining']} more day(s)"
    result["refund_amount"] = item.quantity * item.unit_price
    return result


def create_return_request(
    db: Session,
    customer_id: uuid.UUID,
    order_number: str,
    order_item_id: uuid.UUID,
    reason: str,
) -> ReturnRequest:
    # Step 1: run the eligibility check again, an earlier check is never trusted
    eligibility = check_return_eligibility(db, order_number, order_item_id, customer_id)
    if eligibility["reason_code"] == "already_requested":
        raise ConflictError(eligibility["reason"])
    if not eligibility["eligible"]:
        raise BadRequestError(eligibility["reason"])

    # Step 2: load the order again to get its id
    order = order_repository.get_by_order_number(db, order_number, customer_id)

    # Step 3: save the return request (the refund amount comes from the order item, never from the caller); the unique order_item_id stops two simultaneous requests
    try:
        return_request = return_repository.create(
            db, order.id, order_item_id, customer_id, reason, eligibility["refund_amount"]
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("A return has already been requested for this item")

    # Step 4: return the saved request
    return return_request


# Customers pass their own customer_id; staff pass None
def get_return_request(db: Session, return_request_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> ReturnRequest:
    # Step 1: load the return request (a customer only finds their own)
    return_request = return_repository.get_by_id(db, return_request_id, customer_id)
    if return_request is None:
        raise NotFoundError("Return request not found")

    # Step 2: return it
    return return_request


def list_return_requests(db: Session, customer_id: uuid.UUID) -> list[ReturnRequest]:
    return return_repository.list_for_customer(db, customer_id)
