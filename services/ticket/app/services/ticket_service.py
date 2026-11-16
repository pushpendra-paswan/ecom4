import uuid

from sqlalchemy.orm import Session

from app.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.events import publish_event
from app.models import Message, Ticket
from app.repositories import message_repository, ticket_repository

# Staff can set these by hand; awaiting_approval is set only by the approval service
STAFF_SETTABLE_STATUSES = ("open", "escalated", "resolved")

# M10: system messages posted when a staff member takes over a conversation and when they hand it back
TAKEOVER_MESSAGE = "A support agent has joined the conversation."
RELEASE_MESSAGE = "Our AI assistant is back to help. A support agent can still join at any time."


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
    # Step 1: load and lock the ticket (a customer only finds their own). The lock makes a takeover that is being
    # saved right now finish first, so step 2 always sees it.
    ticket = ticket_repository.get_by_id_for_update(db, ticket_id, customer_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2 (M10): while a human has taken over, the AI may not post at all (system messages are still allowed).
    # Enforced here, not only in the agent, so an AI run that started before the takeover cannot reply after it.
    if sender_type == "ai" and ticket.taken_over_by is not None:
        raise ConflictError("A support agent has taken over this ticket; AI messages are not accepted")

    # Step 3: save the message
    message = message_repository.create(db, ticket.id, sender_type, sender_id, content)

    # Step 4: a customer writing on a resolved ticket reopens it; otherwise just mark the ticket as updated
    old_status = ticket.status
    if sender_type == "customer" and old_status == "resolved":
        ticket_repository.update_status(db, ticket, "open")
    else:
        ticket_repository.touch(db, ticket)

    # Step 5: save everything together
    db.commit()

    # Step 6: publish the new message, and the reopen if it happened
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


# M10: a staff member takes over the conversation; the AI stays silent until it is released.
# Taking over your own ticket again changes nothing; another agent's takeover is a conflict, except for an admin.
def take_over_ticket(db: Session, ticket_id: uuid.UUID, staff_id: uuid.UUID, staff_role: str) -> Ticket:
    # Step 1: load and lock the ticket (two agents taking over at once are handled one after the other)
    ticket = ticket_repository.get_by_id_for_update(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: already taken over by the caller: nothing to do
    if ticket.taken_over_by == staff_id:
        db.commit()
        return ticket

    # Step 3: taken over by someone else: only an admin may take it from them
    if ticket.taken_over_by is not None and staff_role != "admin":
        raise ConflictError("This ticket has already been taken over by another support agent")

    # Step 4: take it over, assign it to the caller, and tell the customer in a system message
    old_assigned_agent_id = ticket.assigned_agent_id
    ticket_repository.set_taken_over(db, ticket, staff_id)
    if old_assigned_agent_id != staff_id:
        ticket_repository.update_assignment(db, ticket, staff_id)
    message = message_repository.create(db, ticket.id, "system", None, TAKEOVER_MESSAGE)

    # Step 5: save everything together
    db.commit()

    # Step 6: publish the assignment (if it changed), the system message and the takeover
    if old_assigned_agent_id != staff_id:
        publish_event(
            "ticket.assigned",
            {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "assigned_agent_id": ticket.assigned_agent_id},
        )
    publish_event(
        "ticket.message_created",
        {
            "ticket_id": ticket.id,
            "customer_id": ticket.customer_id,
            "message_id": message.id,
            "sender_type": message.sender_type,
        },
    )
    publish_event(
        "ticket.takeover_changed",
        {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "taken_over_by": ticket.taken_over_by},
    )
    return ticket


# M10: hand the conversation back to the AI. Only the staff member who took it over, or an admin, may do this.
def release_ticket(db: Session, ticket_id: uuid.UUID, staff_id: uuid.UUID, staff_role: str) -> Ticket:
    # Step 1: load and lock the ticket
    ticket = ticket_repository.get_by_id_for_update(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: there must be a takeover to release, and it must be the caller's (or the caller is an admin)
    if ticket.taken_over_by is None:
        raise ConflictError("This ticket is not taken over")
    if ticket.taken_over_by != staff_id and staff_role != "admin":
        raise ForbiddenError("Only the support agent who took over this ticket, or an admin, can release it")

    # Step 3: clear the takeover; an escalated ticket goes back to open so the AI can help again
    old_status = ticket.status
    ticket_repository.clear_taken_over(db, ticket)
    if old_status == "escalated":
        ticket_repository.update_status(db, ticket, "open")

    # Step 4: tell the customer in a system message, and save everything together
    message = message_repository.create(db, ticket.id, "system", None, RELEASE_MESSAGE)
    db.commit()

    # Step 5: publish the system message, the status change (if any) and the takeover change
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
    publish_event(
        "ticket.takeover_changed",
        {"ticket_id": ticket.id, "customer_id": ticket.customer_id, "taken_over_by": None},
    )
    return ticket
