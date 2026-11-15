import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Ticket


# Repositories only flush (send the SQL); the service decides when to commit
def create(db: Session, customer_id: uuid.UUID, customer_email: str, subject: str) -> Ticket:
    ticket = Ticket(customer_id=customer_id, customer_email=customer_email, subject=subject, status="open")
    db.add(ticket)
    db.flush()
    return ticket


# customer_id is optional: customers pass their own id, staff pass None to see any ticket
def get_by_id(db: Session, ticket_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> Ticket | None:
    query = select(Ticket).where(Ticket.id == ticket_id)
    if customer_id is not None:
        query = query.where(Ticket.customer_id == customer_id)
    return db.scalar(query)


# Both filters are optional; most recently updated tickets come first
def list_tickets(db: Session, customer_id: uuid.UUID | None = None, status: str | None = None) -> list[Ticket]:
    query = select(Ticket).order_by(Ticket.updated_at.desc())
    if customer_id is not None:
        query = query.where(Ticket.customer_id == customer_id)
    if status is not None:
        query = query.where(Ticket.status == status)
    return list(db.scalars(query))


def update_status(db: Session, ticket: Ticket, status: str) -> Ticket:
    ticket.status = status
    ticket.updated_at = func.now()
    db.flush()
    return ticket


def update_assignment(db: Session, ticket: Ticket, agent_id: uuid.UUID | None) -> Ticket:
    ticket.assigned_agent_id = agent_id
    ticket.updated_at = func.now()
    db.flush()
    return ticket


# Marks the ticket as changed (used when a message is added)
def touch(db: Session, ticket: Ticket) -> Ticket:
    ticket.updated_at = func.now()
    db.flush()
    return ticket
