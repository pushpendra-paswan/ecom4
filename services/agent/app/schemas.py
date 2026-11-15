import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

RunStatus = Literal["started", "completed", "skipped", "failed"]


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    trigger_message_id: uuid.UUID
    status: str
    note: str | None
    context_until: datetime | None
    reply_message_id: uuid.UUID | None
    tool_call_count: int
    escalation_reason: str | None
    started_at: datetime
    finished_at: datetime | None
