import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Message


# Only flushes; the service commits
def create(
    db: Session,
    ticket_id: uuid.UUID,
    sender_type: str,
    sender_id: uuid.UUID | None,
    content: str,
) -> Message:
    message = Message(ticket_id=ticket_id, sender_type=sender_type, sender_id=sender_id, content=content)
    db.add(message)
    db.flush()
    return message


# Oldest first, so the conversation reads top to bottom
def list_by_ticket(db: Session, ticket_id: uuid.UUID) -> list[Message]:
    query = select(Message).where(Message.ticket_id == ticket_id).order_by(Message.created_at, Message.id)
    return list(db.scalars(query))
