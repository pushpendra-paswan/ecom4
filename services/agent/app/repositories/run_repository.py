import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun


# Repositories only flush (send the SQL); the service decides when to commit.
# A "message" run has a trigger message, an "approval" run (M9) a trigger approval.
def create(
    db: Session,
    ticket_id: uuid.UUID,
    run_type: str,
    trigger_message_id: uuid.UUID | None,
    trigger_approval_id: uuid.UUID | None,
) -> AgentRun:
    run = AgentRun(
        ticket_id=ticket_id,
        run_type=run_type,
        trigger_message_id=trigger_message_id,
        trigger_approval_id=trigger_approval_id,
        status="started",
        tool_call_count=0,
    )
    db.add(run)
    db.flush()
    return run


def get_by_trigger_message(db: Session, trigger_message_id: uuid.UUID) -> AgentRun | None:
    return db.scalar(select(AgentRun).where(AgentRun.trigger_message_id == trigger_message_id))


def get_by_trigger_approval(db: Session, trigger_approval_id: uuid.UUID) -> AgentRun | None:
    return db.scalar(select(AgentRun).where(AgentRun.trigger_approval_id == trigger_approval_id))


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
    escalation_reason: str | None,
    paused_for_approval_id: uuid.UUID | None,
    finished_at: datetime | None,
) -> AgentRun:
    run.status = status
    run.note = note
    run.context_until = context_until
    run.reply_message_id = reply_message_id
    run.tool_call_count = tool_call_count
    run.escalation_reason = escalation_reason
    run.paused_for_approval_id = paused_for_approval_id
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
