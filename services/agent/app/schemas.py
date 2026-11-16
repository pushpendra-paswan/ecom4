import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

RunStatus = Literal["started", "completed", "skipped", "failed"]


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    run_type: str
    trigger_message_id: uuid.UUID | None
    trigger_approval_id: uuid.UUID | None
    status: str
    note: str | None
    context_until: datetime | None
    reply_message_id: uuid.UUID | None
    tool_call_count: int
    escalation_reason: str | None
    paused_for_approval_id: uuid.UUID | None
    started_at: datetime
    finished_at: datetime | None


# M10: one run with every column, including the AI activity log (GET /runs/{run_id}, GET /tickets/{id}/activity).
# GET /runs stays light and uses RunResponse above.
class RunDetailResponse(RunResponse):
    # [{"step", "tool", "tier", "arguments", "result", "is_error"}] in the order the model asked for them
    tool_calls: list[dict[str, Any]]
    # [{"query", "document_id", "title", "section", "score"}] from search_knowledge_base
    retrieved_chunks: list[dict[str, Any]]
    reply_text: str | None
    model_name: str | None
    input_tokens: int
    output_tokens: int
    duration_ms: int | None
