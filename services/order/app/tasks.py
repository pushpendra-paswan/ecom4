import logging

import grpc

from app import ticket_client
from app.celery_app import celery_app
from app.database import SessionLocal
from app.services import refund_service

logger = logging.getLogger(__name__)

# Only these gRPC errors mean "the Ticket service is unreachable right now", so only they are retried:
# 5 retries with exponential backoff (2, 4, 8, 16, 32 seconds)
RETRY_CODES = (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
MAX_RETRIES = 5


# Thin entry point, queued by the order consumer for every approved refund approval (approval.decided).
# Fetches the approval from the Ticket service, then refund_service verifies it and executes the refund.
@celery_app.task(
    name="app.tasks.execute_approved_refund",
    bind=True,
    acks_late=True,
    ignore_result=True,
    max_retries=MAX_RETRIES,
)
def execute_approved_refund(self, approval_id: str) -> None:
    # Step 1: get the approval from the Ticket service (None if it does not exist there);
    # retry only when the Ticket service is unreachable or too slow
    try:
        approval = ticket_client.get_approval(approval_id)
    except grpc.RpcError as error:
        if error.code() in RETRY_CODES:
            countdown = 2 ** (self.request.retries + 1)
            logger.warning(
                "Could not get approval %s (%s), retry %d in %d s",
                approval_id,
                error.code().name,
                self.request.retries + 1,
                countdown,
            )
            raise self.retry(exc=error, countdown=countdown)
        raise

    # Step 2: verify and execute in a session of our own
    db = SessionLocal()
    try:
        refund_service.execute_approved_refund(db, approval_id, approval)
    finally:
        # Step 3: always close the session
        db.close()
