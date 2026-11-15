import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# One row per customer message (the "trigger message"): did the AI answer it, skip it, or fail?
# The conversation itself lives in the Ticket service; LangGraph's checkpoint tables are not ours.
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Unique: a duplicate event for the same customer message can never create a second run
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(20))  # started | completed | skipped | failed
    note: Mapped[str | None] = mapped_column(Text)  # skip reason or error message
    # created_at of the newest ticket message the run read
    context_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_message_id: Mapped[uuid.UUID | None] = mapped_column()
    tool_call_count: Mapped[int] = mapped_column(default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
