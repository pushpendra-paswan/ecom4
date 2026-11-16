import logging
import time
import uuid
from datetime import datetime, timezone

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.agent import clients, graph
from app.agent.prompts import AWAITING_APPROVAL_MESSAGE
from app.errors import NotFoundError, ToolCallError
from app.models import AgentRun
from app.repositories import run_repository

logger = logging.getLogger(__name__)

# GET /runs never returns more than this many rows
MAX_RUNS_LISTED = 50
# Long error messages (for example a full API error) are cut to this length before they are saved
MAX_NOTE_LENGTH = 1000
# M10: the note of a run whose message the Ticket service rejected because a human had taken over the ticket
REPLY_DISCARDED_NOTE = "reply_discarded_takeover"


# Decides whether the AI answers one customer message, and if so runs the graph and records the result.
# Called by the Celery task while it holds the per-ticket lock, so only one run per ticket is active.
def process_customer_message(db: Session, ticket_id: uuid.UUID, message_id: uuid.UUID) -> None:
    # Step 1: start the clock for duration_ms (M10)
    start_time = time.monotonic()

    # Step 2: one run per trigger message. A finished one means this is a duplicate event; a started or
    # failed one means this is a retry, so reuse it.
    run = run_repository.get_by_trigger_message(db, message_id)
    if run is not None and run.status in ("completed", "skipped"):
        logger.info("Message %s already handled (run %s is %s), nothing to do", message_id, run.id, run.status)
        return
    if run is None:
        run = run_repository.create(db, ticket_id, "message", message_id, None)
        db.commit()

    # Step 3: load the ticket and its conversation from the Ticket service (the source of truth)
    ticket = clients.get_ticket(str(ticket_id))
    trigger_message = None
    for message in ticket["messages"]:
        if message["id"] == str(message_id):
            trigger_message = message
    if trigger_message is None:
        run_repository.update(
            db, run, "skipped", "message_not_found", None, None, 0, None, None, datetime.now(timezone.utc)
        )
        db.commit()
        logger.warning("Run %s skipped: message %s is not on ticket %s", run.id, message_id, ticket_id)
        return
    trigger_created_at = datetime.fromisoformat(trigger_message["created_at"])

    # Step 4: apply the skip rules in order
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
    # M10: a human has taken over the conversation, so the AI posts nothing at all (checked before
    # awaiting_approval, so a taken-over ticket does not get the fixed "under review" message either)
    if skip_reason is None and ticket["taken_over_by"] is not None:
        skip_reason = "human_takeover"
    if skip_reason is None and ticket["status"] == "awaiting_approval":
        skip_reason = "awaiting_approval"
    if skip_reason is not None:
        # While a refund waits for a human, the customer gets one fixed message and no model run: a new run would
        # start the graph again and overwrite the paused state (M9). M10: if a human took over the ticket a moment
        # ago, the Ticket service rejects the message; the run records that and moves on.
        reply_message_id = None
        reply_text = None
        note = skip_reason
        if skip_reason == "awaiting_approval":
            reply_text = AWAITING_APPROVAL_MESSAGE
            try:
                message = clients.post_message(str(ticket_id), "ai", AWAITING_APPROVAL_MESSAGE)
                reply_message_id = uuid.UUID(message["id"])
            except ToolCallError as error:
                logger.warning("Run %s: awaiting-approval message discarded: %s", run.id, error)
                note = REPLY_DISCARDED_NOTE
        run_repository.update(
            db, run, "skipped", note, None, reply_message_id, 0, None, None, datetime.now(timezone.utc)
        )
        run_repository.save_activity(
            db, run, [], [], reply_text, None, 0, 0, int((time.monotonic() - start_time) * 1000)
        )
        db.commit()
        logger.info("Run %s for message %s skipped: %s", run.id, message_id, note)
        return

    # Step 5: end the read transaction before the long graph run. An open transaction would also block the
    # checkpointer's first setup(): its CREATE INDEX CONCURRENTLY waits for every open transaction in agent_db.
    db.commit()

    # Step 6: run the graph. Hitting the step limit is a failure with a fallback reply, not a retry.
    try:
        result = graph.run_agent(str(ticket_id))
    except GraphRecursionError:
        logger.warning("Run %s hit the step limit", run.id)
        mark_run_failed(db, ticket_id, message_id, "step_limit")
        return

    # Step 7: a run that ended paused for a refund approval gets a note and the approval id
    note = None
    paused_for_approval_id = None
    if result["paused_for_approval_id"] is not None:
        paused_for_approval_id = uuid.UUID(result["paused_for_approval_id"])
        note = f"paused for approval {paused_for_approval_id}"

    # Step 8 (M10): a reply the Ticket service rejected (a human took over during the run) makes the run "skipped",
    # not failed and not retried; the reply stays in reply_text for staff. Otherwise the run is completed.
    status = "completed"
    reply_message_id = None
    if result["reply_discarded"]:
        status = "skipped"
        note = REPLY_DISCARDED_NOTE
    else:
        reply_message_id = uuid.UUID(result["reply_message_id"])

    # Step 9: record the run with its activity log (and the escalation reason, if the AI handed the ticket over)
    run_repository.update(
        db,
        run,
        status,
        note,
        datetime.fromisoformat(result["context_until"]),
        reply_message_id,
        result["tool_call_count"],
        result["escalation_reason"],
        paused_for_approval_id,
        datetime.now(timezone.utc),
    )
    run_repository.save_activity(
        db,
        run,
        result["tool_calls"],
        result["retrieved_chunks"],
        result["reply_text"],
        result["model_name"],
        result["input_tokens"],
        result["output_tokens"],
        int((time.monotonic() - start_time) * 1000),
    )
    db.commit()
    logger.info(
        "Run %s %s: reply %s, %d tool call(s)%s",
        run.id,
        status,
        reply_message_id,
        result["tool_call_count"],
        " (reply discarded: the ticket was taken over)" if result["reply_discarded"] else "",
    )
    if result["escalation_reason"] is not None:
        logger.info("Run %s escalated ticket %s: %s", run.id, ticket_id, result["escalation_reason"])
    if paused_for_approval_id is not None:
        logger.info("Run %s paused ticket %s for approval %s", run.id, ticket_id, paused_for_approval_id)


# M9: a human approved or rejected a refund. If this ticket's graph is paused for that approval, resume it so it
# posts the outcome. Called by the Celery task while it holds the per-ticket lock.
def process_approval_decision(db: Session, ticket_id: uuid.UUID, approval_id: uuid.UUID) -> None:
    # Step 1: start the clock for duration_ms (M10)
    start_time = time.monotonic()

    # Step 2: one run per approval. A finished one means this is a duplicate event; a started or failed one means
    # this is a retry, so reuse it.
    run = run_repository.get_by_trigger_approval(db, approval_id)
    if run is not None and run.status in ("completed", "skipped"):
        logger.info("Approval %s already handled (run %s is %s), nothing to do", approval_id, run.id, run.status)
        return
    if run is None:
        run = run_repository.create(db, ticket_id, "approval", None, approval_id)

    # Step 3: end the transaction before the graph runs (same reason as in process_customer_message)
    db.commit()

    # Step 4: resume the paused graph, if it waits for this approval
    result = graph.resume_agent(str(ticket_id), str(approval_id))

    # Step 5: record the outcome message; or that it was discarded (M10: a human has taken over the ticket and tells
    # the customer); or that there was nothing waiting (an approval created over REST, or a duplicate event)
    finished_at = datetime.now(timezone.utc)
    if result["resumed"] and result["reply_discarded"]:
        run_repository.update(db, run, "skipped", REPLY_DISCARDED_NOTE, None, None, 0, None, None, finished_at)
        logger.info("Approval run %s skipped: outcome message discarded, ticket %s is taken over", run.id, ticket_id)
    elif result["resumed"]:
        reply_message_id = uuid.UUID(result["reply_message_id"])
        run_repository.update(db, run, "completed", None, None, reply_message_id, 0, None, None, finished_at)
        logger.info("Approval run %s completed: outcome message %s", run.id, result["reply_message_id"])
    else:
        run_repository.update(db, run, "skipped", "no_pending_interrupt", None, None, 0, None, None, finished_at)
        logger.info("Approval run %s skipped: ticket %s is not waiting for approval %s", run.id, ticket_id, approval_id)

    # Step 6: the activity log of a resume run is only its outcome text (no model call, no tools)
    run_repository.save_activity(
        db, run, [], [], result["reply_text"], None, 0, 0, int((time.monotonic() - start_time) * 1000)
    )
    db.commit()


# M9: final failure of an approval run. Nothing is posted (a human can always follow up); the run is marked failed.
def mark_approval_run_failed(db: Session, ticket_id: uuid.UUID, approval_id: uuid.UUID, error_text: str) -> None:
    # Step 1: find the run (it may not exist if the failure happened before it was created)
    run = run_repository.get_by_trigger_approval(db, approval_id)
    if run is None:
        run = run_repository.create(db, ticket_id, "approval", None, approval_id)
    if run.status in ("completed", "skipped"):
        logger.info("Run %s is already %s, not marking it failed", run.id, run.status)
        return

    # Step 2: mark it failed with the error. duration_ms (M10) counts from the first attempt, retries included.
    note = error_text[:MAX_NOTE_LENGTH]
    finished_at = datetime.now(timezone.utc)
    run_repository.update(db, run, "failed", note, None, run.reply_message_id, 0, None, None, finished_at)
    run_repository.save_activity(
        db, run, [], [], None, None, 0, 0, int((finished_at - run.started_at).total_seconds() * 1000)
    )
    db.commit()
    logger.error("Approval run %s for approval %s failed: %s", run.id, approval_id, note)


# Final failure: post ONE fallback message on the ticket and mark the run failed with the error.
# Also used when the graph hits its step limit (error_text "step_limit").
def mark_run_failed(db: Session, ticket_id: uuid.UUID, message_id: uuid.UUID, error_text: str) -> None:
    # Step 1: find the run (it may not exist if the failure happened before it was created)
    run = run_repository.get_by_trigger_message(db, message_id)
    if run is None:
        run = run_repository.create(db, ticket_id, "message", message_id, None)
    if run.status in ("completed", "skipped"):
        logger.info("Run %s is already %s, not marking it failed", run.id, run.status)
        return

    # Step 2: post the fallback, but only once per run (a run that already replied keeps its reply).
    # M10: a ticket a human has taken over rejects it (ToolCallError); the run stays failed and the note says so.
    note = error_text[:MAX_NOTE_LENGTH]
    reply_message_id = run.reply_message_id
    reply_text = run.reply_text
    if reply_message_id is None:
        reply_text = graph.FALLBACK_MESSAGE
        try:
            message = clients.post_message(str(ticket_id), "ai", graph.FALLBACK_MESSAGE)
            reply_message_id = uuid.UUID(message["id"])
        except ToolCallError as error:
            logger.warning("Run %s: fallback message discarded: %s", run.id, error)
            note = f"{error_text} ({REPLY_DISCARDED_NOTE})"[:MAX_NOTE_LENGTH]
        except Exception as error:
            logger.error("Run %s: could not post the fallback message: %r", run.id, error)
            note = f"{error_text} (fallback not posted: {error!r})"[:MAX_NOTE_LENGTH]

    # Step 3: mark the run failed. The graph's final state is lost on a failure, so the activity log has only the
    # fallback text and the duration, counted from the first attempt (retries included).
    finished_at = datetime.now(timezone.utc)
    run_repository.update(
        db,
        run,
        "failed",
        note,
        run.context_until,
        reply_message_id,
        run.tool_call_count,
        run.escalation_reason,
        run.paused_for_approval_id,
        finished_at,
    )
    run_repository.save_activity(
        db,
        run,
        run.tool_calls,
        run.retrieved_chunks,
        reply_text,
        run.model_name,
        run.input_tokens,
        run.output_tokens,
        int((finished_at - run.started_at).total_seconds() * 1000),
    )
    db.commit()
    logger.error("Run %s for message %s failed: %s", run.id, message_id, note)


def list_runs(db: Session, ticket_id: uuid.UUID | None, status: str | None) -> list[AgentRun]:
    return run_repository.list_runs(db, ticket_id, status, MAX_RUNS_LISTED)


# M10: one run with its activity log
def get_run(db: Session, run_id: uuid.UUID) -> AgentRun:
    run = run_repository.get_by_id(db, run_id)
    if run is None:
        raise NotFoundError("Run not found")
    return run


# M10: the AI activity log of one ticket, every run oldest first. A ticket with no runs gives an empty list
# (this service does not know which tickets exist).
def get_ticket_activity(db: Session, ticket_id: uuid.UUID) -> list[AgentRun]:
    return run_repository.list_for_ticket_oldest_first(db, ticket_id)
