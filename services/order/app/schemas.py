import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer, StringConstraints


# Money stays Decimal in the database and services; in JSON responses it is shown as a number with two decimals
def money_to_number(amount: Decimal) -> float:
    return round(float(amount), 2)


Money = Annotated[Decimal, PlainSerializer(money_to_number, return_type=float)]

# A reason must contain at least one visible character
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_name: str
    category: str
    quantity: int
    unit_price: Money


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    carrier: str
    tracking_number: str
    status: str
    shipped_on: date
    estimated_delivery: date
    delivered_on: date | None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    status: str
    total_amount: Money
    ordered_at: datetime
    items: list[OrderItemResponse]
    shipment: ShipmentResponse | None


# The shape the gRPC contract will copy in M5
class EligibilityResponse(BaseModel):
    eligible: bool
    reason_code: str
    reason: str
    refund_amount: Money | None
    window_days: int | None
    days_remaining: int | None


class ReturnRequestCreate(BaseModel):
    order_number: str
    order_item_id: uuid.UUID
    reason: Reason


class ReturnRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    order_item_id: uuid.UUID
    customer_id: uuid.UUID
    reason: str
    refund_amount: Money
    status: str
    created_at: datetime


class RefundExecuteRequest(BaseModel):
    return_request_id: uuid.UUID
    approval_id: uuid.UUID


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    return_request_id: uuid.UUID
    approval_id: uuid.UUID
    amount: Money
    status: str
    created_at: datetime
    already_processed: bool = False
