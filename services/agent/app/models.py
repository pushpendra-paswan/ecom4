import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# One row per trigger: a customer message (run_type "message": did the AI answer it, skip it, or fail?) or, from M9,
# a human decision on a refund approval (run_type "approval": was the paused graph resumed and the outcome posted?).
# The conversation itself lives in the Ticket service; LangGraph's checkpoint tables are not ours.
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(index=True)
    run_type: Mapped[str] = mapped_column(String(20), default="message", server_default="message")  # message | approval
    # Unique: a duplicate event for the same customer message can never create a second run (null for approval runs)
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    # Unique: a duplicate approval.decided event can never create a second approval run (null for message runs)
    trigger_approval_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(20))  # started | completed | skipped | failed
    note: Mapped[str | None] = mapped_column(Text)  # skip reason or error message
    # created_at of the newest ticket message the run read
    context_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_message_id: Mapped[uuid.UUID | None] = mapped_column()
    tool_call_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Why the AI handed the ticket to a human (escalate_to_human), for staff; None if it did not escalate
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    # M9: the run ended paused, waiting for a human decision on this approval
    paused_for_approval_id: Mapped[uuid.UUID | None] = mapped_column()
    # M10, the AI activity log (staff only). tool_calls: [{step, tool, tier, arguments, result, is_error}] in order;
    # retrieved_chunks: [{query, document_id, title, section, score}] from search_knowledge_base
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    retrieved_chunks: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The exact text the run posted, or tried to post when the ticket was taken over meanwhile
    reply_text: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(100))
    # Summed over this run's model calls (0 when the run made none)
    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
