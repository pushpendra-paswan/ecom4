import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import (
    MessageCreate,
    MessageResponse,
    StatusUpdate,
    TicketCreate,
    TicketResponse,
    TicketStatus,
    TicketWithMessagesResponse,
)
from app.services import ticket_service

router = APIRouter()


# Customer only: the customer id and email come from the token, never from the body
@router.post("/tickets", response_model=TicketWithMessagesResponse, status_code=201)
def create_ticket(
    body: TicketCreate,
    current_user: dict = Depends(require_role("customer")),
    db: Session = Depends(get_db),
):
    return ticket_service.create_ticket(
        db, uuid.UUID(current_user["user_id"]), current_user["email"], body.subject, body.message
    )


# Customers see their own tickets; agents and admins see all. Everyone can filter by status.
@router.get("/tickets", response_model=list[TicketResponse])
def list_tickets(
    status: TicketStatus | None = None,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    customer_id = uuid.UUID(current_user["user_id"]) if current_user["role"] == "customer" else None
    return ticket_service.list_tickets(db, customer_id, status)


# Customers only see their own tickets (anything else is a 404); agents and admins see any ticket
@router.get("/tickets/{ticket_id}", response_model=TicketWithMessagesResponse)
def get_ticket(
    ticket_id: uuid.UUID,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    customer_id = uuid.UUID(current_user["user_id"]) if current_user["role"] == "customer" else None
    return ticket_service.get_ticket(db, ticket_id, customer_id)


# The sender type comes from the role: customers post as "customer", agents and admins as "agent"
@router.post("/tickets/{ticket_id}/messages", response_model=MessageResponse, status_code=201)
def add_message(
    ticket_id: uuid.UUID,
    body: MessageCreate,
    current_user: dict = Depends(require_role("customer", "agent", "admin")),
    db: Session = Depends(get_db),
):
    user_id = uuid.UUID(current_user["user_id"])
    if current_user["role"] == "customer":
        return ticket_service.add_message(db, ticket_id, "customer", user_id, body.content, customer_id=user_id)
    return ticket_service.add_message(db, ticket_id, "agent", user_id, body.content)


# Staff only: assigns the ticket to the caller
@router.post("/tickets/{ticket_id}/assign", response_model=TicketResponse)
def assign_ticket(
    ticket_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return ticket_service.assign_ticket(db, ticket_id, uuid.UUID(current_user["user_id"]))


# Staff only: open, escalated or resolved (awaiting_approval is set by the approval logic)
@router.patch("/tickets/{ticket_id}/status", response_model=TicketResponse)
def update_status(
    ticket_id: uuid.UUID,
    body: StatusUpdate,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return ticket_service.update_status(db, ticket_id, body.status)


# M10, staff only: take over the conversation (the AI stops replying). 409 if another agent has it, unless admin.
@router.post("/tickets/{ticket_id}/takeover", response_model=TicketResponse)
def take_over_ticket(
    ticket_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return ticket_service.take_over_ticket(db, ticket_id, uuid.UUID(current_user["user_id"]), current_user["role"])


# M10, staff only: hand the conversation back to the AI. 403 unless you took it over (or are an admin);
# 409 if it is not taken over.
@router.post("/tickets/{ticket_id}/release", response_model=TicketResponse)
def release_ticket(
    ticket_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return ticket_service.release_ticket(db, ticket_id, uuid.UUID(current_user["user_id"]), current_user["role"])
