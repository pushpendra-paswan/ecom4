import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Customers and agents live in the identity service's database, so there are no foreign keys to them
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Copied from the token when the ticket is created, so the dashboard never has to ask Identity
    customer_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))  # open | awaiting_approval | escalated | resolved
    assigned_agent_id: Mapped[uuid.UUID | None]
    # M10: the staff member who took over the conversation (the AI stays silent while this is set), and when
    taken_over_by: Mapped[uuid.UUID | None]
    taken_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set again whenever a message is added or the status or assignment changes
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    sender_type: Mapped[str] = mapped_column(String(20))  # customer | ai | agent | system
    sender_id: Mapped[uuid.UUID | None]  # null for ai and system messages
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"

    # This id is the approval_id the order service receives when it executes a refund
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    action_type: Mapped[str] = mapped_column(String(20))  # refund
    # unique: one approval per return request
    return_request_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # pending | approved | rejected
    auto_approved: Mapped[bool]
    decided_by: Mapped[uuid.UUID | None]  # null while pending and when auto-approved
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
