import uuid

from sqlalchemy.orm import Session

from app.errors import BadRequestError, NotFoundError
from app.events import publish_event
from app.models import Message, Ticket
from app.repositories import message_repository, ticket_repository

# Staff can set these by hand; awaiting_approval is set only by the approval service
STAFF_SETTABLE_STATUSES = ("open", "escalated", "resolved")


# Creates the ticket and its first (customer) message in one transaction.
# Returns {"ticket": Ticket, "messages": [Message]}.
def create_ticket(db: Session, customer_id: uuid.UUID, customer_email: str, subject: str, first_message: str) -> dict:
    # Step 1: create the ticket (status open)
    ticket = ticket_repository.create(db, customer_id, customer_email, subject)

    # Step 2: add the customer's first message
    message = message_repository.create(db, ticket.id, "customer", customer_id, first_message)

    # Step 3: save both together
    db.commit()

    # Step 4: tell the rest of the platform (after the commit, so listeners can already read the data)
    publish_event("ticket.created", {"ticket_id": ticket.id, "customer_id": ticket.customer_id})
    publish_event(
        "ticket.message_created",
        {
            "ticket_id": ticket.id,
            "customer_id": ticket.customer_id,
            "message_id": message.id,
            "sender_type": message.sender_type,
        },
    )
    return {"ticket": ticket, "messages": [message]}


# Customers pass their own customer_id; staff pass None to see every ticket
def list_tickets(db: Session, customer_id: uuid.UUID | None = None, status: str | None = None) -> list[Ticket]:
    return ticket_repository.list_tickets(db, customer_id, status)


# Customers pass their own customer_id (another customer's ticket looks like it does not exist); staff pass None.
# Returns {"ticket": Ticket, "messages": [Message, ...]} with messages oldest first.
def get_ticket(db: Session, ticket_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> dict:
    # Step 1: load the ticket (a customer only finds their own)
    ticket = ticket_repository.get_by_id(db, ticket_id, customer_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: load its messages
    messages = message_repository.list_by_ticket(db, ticket.id)
    return {"ticket": ticket, "messages": messages}


# sender_type and sender_id are chosen by the caller (the REST router derives them from the role;
# later the AI agent calls this through gRPC with sender_type "ai"). customer_id limits a customer to their own tickets.
def add_message(
    db: Session,
    ticket_id: uuid.UUID,
    sender_type: str,
    sender_id: uuid.UUID | None,
    content: str,
    customer_id: uuid.UUID | None = None,
) -> Message:
    # Step 1: load the ticket (a customer only finds their own)
    ticket = ticket_repository.get_by_id(db, ticket_id, customer_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: save the message
    message = message_repository.create(db, ticket.id, sender_type, sender_id, content)

    # Step 3: a customer writing on a resolved ticket reopens it; otherwise just mark the ticket as updated
    old_status = ticket.status
    if sender_type == "customer" and old_status == "resolved":
        ticket_repository.update_status(db, ticket, "open")
    else:
        ticket_repository.touch(db, ticket)

    # Step 4: save everything together
    db.commit()

    # Step 5: publish the new message, and the reopen if it happened
    publish_event(
        "ticket.message_created",
        {
            "ticket_id": ticket.id,
            "customer_id": ticket.customer_id,
            "message_id": message.id,
            "sender_type": message.sender_type,
        },
    )
    if ticket.status != old_status:
        publish_event(
            "ticket.status_changed",
            {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "old_status": old_status, "new_status": ticket.status},
        )
    return message


# Assigns the ticket to the given staff member (the router passes the caller's own id)
def assign_ticket(db: Session, ticket_id: uuid.UUID, agent_id: uuid.UUID) -> Ticket:
    # Step 1: load the ticket
    ticket = ticket_repository.get_by_id(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: assign it and save
    ticket_repository.update_assignment(db, ticket, agent_id)
    db.commit()

    # Step 3: publish the assignment
    publish_event(
        "ticket.assigned",
        {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "assigned_agent_id": ticket.assigned_agent_id},
    )
    return ticket


def update_status(db: Session, ticket_id: uuid.UUID, status: str) -> Ticket:
    # Step 1: only open, escalated and resolved can be set by hand
    if status not in STAFF_SETTABLE_STATUSES:
        raise BadRequestError(f"Status must be one of: {', '.join(STAFF_SETTABLE_STATUSES)}")

    # Step 2: load the ticket
    ticket = ticket_repository.get_by_id(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 3: change the status and save
    old_status = ticket.status
    ticket_repository.update_status(db, ticket, status)
    db.commit()

    # Step 4: publish the change (setting the status it already has is not a change, so nothing is published)
    if status != old_status:
        publish_event(
            "ticket.status_changed",
            {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "old_status": old_status, "new_status": status},
        )
    return ticket
