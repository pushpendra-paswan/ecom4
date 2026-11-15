import logging
import uuid

import redis

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.services import run_service

logger = logging.getLogger(__name__)

# redis-py creates connections lazily and resets its pool after a fork, so one client per module is safe
redis_client = redis.Redis.from_url(settings.redis_url)

# Per-ticket lock: one agent run per ticket at a time
LOCK_TIMEOUT_SECONDS = 120
LOCK_RETRY_SECONDS = 3
MAX_LOCK_RETRIES = 20
# Real failures (model, gRPC, database): retried 3 times, 5 s, 10 s, 20 s apart, then the fallback message
MAX_FAILURE_RETRIES = 3
FIRST_RETRY_SECONDS = 5


# Thin entry point: take the ticket lock, call run_service, retry or give up. All the work is in run_service.
# Explicit self.retry (not autoretry_for) because the final failure must post a fallback message.
# The two retry counters are task arguments, so waiting for the lock never uses up the failure retries.
@celery_app.task(
    name="app.tasks.run_agent_for_message",
    bind=True,
    acks_late=True,
    ignore_result=True,
    soft_time_limit=90,
    max_retries=None,  # our own counters below decide when to stop
)
def run_agent_for_message(
    self, ticket_id: str, message_id: str, lock_retries: int = 0, failure_retries: int = 0
) -> None:
    # Step 1: take the per-ticket lock without waiting; if another run holds it, try again in 3 seconds
    lock = redis_client.lock(f"agent:lock:{ticket_id}", timeout=LOCK_TIMEOUT_SECONDS, blocking=False)
    if not lock.acquire():
        if lock_retries < MAX_LOCK_RETRIES:
            logger.info("Ticket %s is busy, message %s waits for the lock (%d)", ticket_id, message_id, lock_retries + 1)
            raise self.retry(
                args=[ticket_id, message_id],
                kwargs={"lock_retries": lock_retries + 1, "failure_retries": failure_retries},
                countdown=LOCK_RETRY_SECONDS,
            )
        # The lock stayed busy for a minute: give up with the fallback message
        db = SessionLocal()
        try:
            run_service.mark_run_failed(db, uuid.UUID(ticket_id), uuid.UUID(message_id), "lock_busy")
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        # Step 2: decide and (maybe) answer
        run_service.process_customer_message(db, uuid.UUID(ticket_id), uuid.UUID(message_id))
    except Exception as error:
        # Step 3: retry with an exponential countdown; after the last retry post the fallback and record the error
        db.rollback()
        if failure_retries < MAX_FAILURE_RETRIES:
            countdown = FIRST_RETRY_SECONDS * 2**failure_retries
            logger.warning(
                "Run for message %s failed (%r), retry %d in %d s", message_id, error, failure_retries + 1, countdown
            )
            raise self.retry(
                args=[ticket_id, message_id],
                kwargs={"lock_retries": lock_retries, "failure_retries": failure_retries + 1},
                countdown=countdown,
                exc=error,
            )
        run_service.mark_run_failed(db, uuid.UUID(ticket_id), uuid.UUID(message_id), f"{type(error).__name__}: {error}")
    finally:
        # Step 4: always close the session and release the lock we hold (it may have expired meanwhile)
        db.close()
        try:
            lock.release()
        except redis.exceptions.LockError:
            logger.warning("Lock for ticket %s had already expired", ticket_id)


# M9: resume a ticket's paused graph after a human decided a refund approval (queued by the consumer for
# approval.decided events with auto_approved=false). Same lock, retries and session handling as
# run_agent_for_message; after the last failure the run is marked failed and NOTHING is posted (a human can always
# follow up on the ticket).
@celery_app.task(
    name="app.tasks.resume_after_approval",
    bind=True,
    acks_late=True,
    ignore_result=True,
    soft_time_limit=90,
    max_retries=None,  # our own counters below decide when to stop
)
def resume_after_approval(
    self, ticket_id: str, approval_id: str, lock_retries: int = 0, failure_retries: int = 0
) -> None:
    # Step 1: take the per-ticket lock without waiting; if another run holds it, try again in 3 seconds
    lock = redis_client.lock(f"agent:lock:{ticket_id}", timeout=LOCK_TIMEOUT_SECONDS, blocking=False)
    if not lock.acquire():
        if lock_retries < MAX_LOCK_RETRIES:
            logger.info("Ticket %s is busy, approval %s waits for the lock (%d)", ticket_id, approval_id, lock_retries + 1)
            raise self.retry(
                args=[ticket_id, approval_id],
                kwargs={"lock_retries": lock_retries + 1, "failure_retries": failure_retries},
                countdown=LOCK_RETRY_SECONDS,
            )
        # The lock stayed busy for a minute: give up (nothing is posted)
        db = SessionLocal()
        try:
            run_service.mark_approval_run_failed(db, uuid.UUID(ticket_id), uuid.UUID(approval_id), "lock_busy")
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        # Step 2: resume the paused graph (if it waits for this approval) and record the run
        run_service.process_approval_decision(db, uuid.UUID(ticket_id), uuid.UUID(approval_id))
    except Exception as error:
        # Step 3: retry with an exponential countdown; after the last retry record the error
        db.rollback()
        if failure_retries < MAX_FAILURE_RETRIES:
            countdown = FIRST_RETRY_SECONDS * 2**failure_retries
            logger.warning(
                "Resume for approval %s failed (%r), retry %d in %d s", approval_id, error, failure_retries + 1, countdown
            )
            raise self.retry(
                args=[ticket_id, approval_id],
                kwargs={"lock_retries": lock_retries, "failure_retries": failure_retries + 1},
                countdown=countdown,
                exc=error,
            )
        run_service.mark_approval_run_failed(
            db, uuid.UUID(ticket_id), uuid.UUID(approval_id), f"{type(error).__name__}: {error}"
        )
    finally:
        # Step 4: always close the session and release the lock we hold (it may have expired meanwhile)
        db.close()
        try:
            lock.release()
        except redis.exceptions.LockError:
            logger.warning("Lock for ticket %s had already expired", ticket_id)
