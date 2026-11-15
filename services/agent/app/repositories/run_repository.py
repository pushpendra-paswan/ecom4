import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun


# Repositories only flush (send the SQL); the service decides when to commit
def create(db: Session, ticket_id: uuid.UUID, trigger_message_id: uuid.UUID) -> AgentRun:
    run = AgentRun(ticket_id=ticket_id, trigger_message_id=trigger_message_id, status="started", tool_call_count=0)
    db.add(run)
    db.flush()
    return run


def get_by_trigger_message(db: Session, trigger_message_id: uuid.UUID) -> AgentRun | None:
    return db.scalar(select(AgentRun).where(AgentRun.trigger_message_id == trigger_message_id))


# The completed run on this ticket that read the furthest into the conversation
def get_latest_completed_for_ticket(db: Session, ticket_id: uuid.UUID) -> AgentRun | None:
    query = (
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id, AgentRun.status == "completed")
        .order_by(AgentRun.context_until.desc().nulls_last())
        .limit(1)
    )
    return db.scalar(query)


def update(
    db: Session,
    run: AgentRun,
    status: str,
    note: str | None,
    context_until: datetime | None,
    reply_message_id: uuid.UUID | None,
    tool_call_count: int,
    finished_at: datetime | None,
) -> AgentRun:
    run.status = status
    run.note = note
    run.context_until = context_until
    run.reply_message_id = reply_message_id
    run.tool_call_count = tool_call_count
    run.finished_at = finished_at
    db.flush()
    return run


# Both filters are optional; newest first, at most `limit` rows
def list_runs(db: Session, ticket_id: uuid.UUID | None, status: str | None, limit: int) -> list[AgentRun]:
    query = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    if ticket_id is not None:
        query = query.where(AgentRun.ticket_id == ticket_id)
    if status is not None:
        query = query.where(AgentRun.status == status)
    return list(db.scalars(query))
