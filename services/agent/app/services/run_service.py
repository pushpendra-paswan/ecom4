import logging
import uuid
from datetime import datetime, timezone

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.agent import clients, graph
from app.models import AgentRun
from app.repositories import run_repository

logger = logging.getLogger(__name__)

# GET /runs never returns more than this many rows
MAX_RUNS_LISTED = 50
# Long error messages (for example a full API error) are cut to this length before they are saved
MAX_NOTE_LENGTH = 1000


# Decides whether the AI answers one customer message, and if so runs the graph and records the result.
# Called by the Celery task while it holds the per-ticket lock, so only one run per ticket is active.
def process_customer_message(db: Session, ticket_id: uuid.UUID, message_id: uuid.UUID) -> None:
    # Step 1: one run per trigger message. A finished one means this is a duplicate event; a started or
    # failed one means this is a retry, so reuse it.
    run = run_repository.get_by_trigger_message(db, message_id)
    if run is not None and run.status in ("completed", "skipped"):
        logger.info("Message %s already handled (run %s is %s), nothing to do", message_id, run.id, run.status)
        return
    if run is None:
        run = run_repository.create(db, ticket_id, message_id)
        db.commit()

    # Step 2: load the ticket and its conversation from the Ticket service (the source of truth)
    ticket = clients.get_ticket(str(ticket_id))
    trigger_message = None
    for message in ticket["messages"]:
        if message["id"] == str(message_id):
            trigger_message = message
    if trigger_message is None:
        run_repository.update(db, run, "skipped", "message_not_found", None, None, 0, None, datetime.now(timezone.utc))
        db.commit()
        logger.warning("Run %s skipped: message %s is not on ticket %s", run.id, message_id, ticket_id)
        return
    trigger_created_at = datetime.fromisoformat(trigger_message["created_at"])

    # Step 3: apply the skip rules in order
    skip_reason = None
    latest_completed_run = run_repository.get_latest_completed_for_ticket(db, ticket_id)
    if (
        latest_completed_run is not None
        and latest_completed_run.context_until is not None
        and latest_completed_run.context_until >= trigger_created_at
    ):
        skip_reason = "already_answered"
    if skip_reason is None:
        for message in ticket["messages"]:
            if message["sender_type"] == "agent" and datetime.fromisoformat(message["created_at"]) > trigger_created_at:
                skip_reason = "human_replied"
    if skip_reason is None and ticket["status"] == "escalated":
        skip_reason = "ticket_escalated"
    if skip_reason is not None:
        run_repository.update(db, run, "skipped", skip_reason, None, None, 0, None, datetime.now(timezone.utc))
        db.commit()
        logger.info("Run %s for message %s skipped: %s", run.id, message_id, skip_reason)
        return

    # Step 4: end the read transaction before the long graph run. An open transaction would also block the
    # checkpointer's first setup(): its CREATE INDEX CONCURRENTLY waits for every open transaction in agent_db.
    db.commit()

    # Step 5: run the graph. Hitting the step limit is a failure with a fallback reply, not a retry.
    try:
        result = graph.run_agent(str(ticket_id))
    except GraphRecursionError:
        logger.warning("Run %s hit the step limit", run.id)
        mark_run_failed(db, ticket_id, message_id, "step_limit")
        return

    # Step 6: record the answer (and the escalation reason, if the AI handed the ticket to a human)
    run_repository.update(
        db,
        run,
        "completed",
        None,
        datetime.fromisoformat(result["context_until"]),
        uuid.UUID(result["reply_message_id"]),
        result["tool_call_count"],
        result["escalation_reason"],
        datetime.now(timezone.utc),
    )
    db.commit()
    logger.info(
        "Run %s completed: reply %s, %d tool call(s)", run.id, result["reply_message_id"], result["tool_call_count"]
    )
    if result["escalation_reason"] is not None:
        logger.info("Run %s escalated ticket %s: %s", run.id, ticket_id, result["escalation_reason"])


# Final failure: post ONE fallback message on the ticket and mark the run failed with the error.
# Also used when the graph hits its step limit (error_text "step_limit").
def mark_run_failed(db: Session, ticket_id: uuid.UUID, message_id: uuid.UUID, error_text: str) -> None:
    # Step 1: find the run (it may not exist if the failure happened before it was created)
    run = run_repository.get_by_trigger_message(db, message_id)
    if run is None:
        run = run_repository.create(db, ticket_id, message_id)
    if run.status in ("completed", "skipped"):
        logger.info("Run %s is already %s, not marking it failed", run.id, run.status)
        return

    # Step 2: post the fallback, but only once per run (a run that already replied keeps its reply)
    note = error_text[:MAX_NOTE_LENGTH]
    reply_message_id = run.reply_message_id
    if reply_message_id is None:
        try:
            message = clients.post_message(str(ticket_id), "ai", graph.FALLBACK_MESSAGE)
            reply_message_id = uuid.UUID(message["id"])
        except Exception as error:
            logger.error("Run %s: could not post the fallback message: %r", run.id, error)
            note = f"{error_text} (fallback not posted: {error!r})"[:MAX_NOTE_LENGTH]

    # Step 3: mark the run failed
    run_repository.update(
        db,
        run,
        "failed",
        note,
        run.context_until,
        reply_message_id,
        run.tool_call_count,
        run.escalation_reason,
        datetime.now(timezone.utc),
    )
    db.commit()
    logger.error("Run %s for message %s failed: %s", run.id, message_id, note)


def list_runs(db: Session, ticket_id: uuid.UUID | None, status: str | None) -> list[AgentRun]:
    return run_repository.list_runs(db, ticket_id, status, MAX_RUNS_LISTED)
