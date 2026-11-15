import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints


# Money stays Decimal in the database and services; in JSON responses it is shown as a number with two decimals
def money_to_number(amount: Decimal) -> float:
    return round(float(amount), 2)


Money = Annotated[Decimal, PlainSerializer(money_to_number, return_type=float)]

# Text fields must contain at least one visible character (surrounding spaces are removed)
Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
MessageContent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

TicketStatus = Literal["open", "awaiting_approval", "escalated", "resolved"]
# awaiting_approval is missing on purpose: only the approval logic sets it
StaffTicketStatus = Literal["open", "escalated", "resolved"]
ApprovalStatus = Literal["pending", "approved", "rejected"]


class TicketCreate(BaseModel):
    subject: Subject
    message: MessageContent


class MessageCreate(BaseModel):
    content: MessageContent


class StatusUpdate(BaseModel):
    status: StaffTicketStatus


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    customer_email: str
    subject: str
    status: str
    assigned_agent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    sender_type: str
    sender_id: uuid.UUID | None
    content: str
    created_at: datetime


# A ticket together with its messages, oldest first
class TicketWithMessagesResponse(BaseModel):
    ticket: TicketResponse
    messages: list[MessageResponse]


class ApprovalCreate(BaseModel):
    action_type: Literal["refund"] = "refund"
    return_request_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    summary: Summary


class ApproveRequest(BaseModel):
    note: Note | None = None


class RejectRequest(BaseModel):
    note: Note


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    action_type: str
    return_request_id: uuid.UUID
    amount: Money
    summary: str
    status: str
    auto_approved: bool
    decided_by: uuid.UUID | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
