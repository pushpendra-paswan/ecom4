import logging
import uuid
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
from app.events import publish_event
from app.models import Refund
from app.repositories import refund_repository, return_repository

logger = logging.getLogger(__name__)


# Simulated refund (no real payment). Safe to call twice with the same approval_id.
# Returns (refund, already_processed).
def execute_refund(db: Session, return_request_id: uuid.UUID, approval_id: uuid.UUID) -> tuple[Refund, bool]:
    # Step 1: load the return request
    return_request = return_repository.get_by_id(db, return_request_id)
    if return_request is None:
        raise NotFoundError("Return request not found")

    # Step 2: the same approval was already used, so return that refund and create nothing
    refund_for_approval = refund_repository.get_by_approval_id(db, approval_id)
    if refund_for_approval is not None:
        if refund_for_approval.return_request_id != return_request_id:
            raise ConflictError("This approval was already used for a different return request")
        return refund_for_approval, True

    # Step 3: the return request was already refunded under a different approval
    if refund_repository.get_by_return_request_id(db, return_request_id) is not None:
        raise ConflictError("This return request already has a refund")

    # Step 4: create the refund (amount from the return request) and mark the request refunded, in one commit
    try:
        refund = refund_repository.create(db, return_request_id, approval_id, return_request.refund_amount)
        return_repository.update_status(db, return_request, "refunded")
        db.commit()
    except IntegrityError:
        # Step 5: another call won the race; undo ours and answer with what the database now holds
        db.rollback()
        existing_refund = refund_repository.get_by_approval_id(db, approval_id)
        if existing_refund is None or existing_refund.return_request_id != return_request_id:
            raise ConflictError("This return request already has a refund")
        return existing_refund, True

    # Step 6: log the simulated refund
    logger.info(
        "Simulated refund of %s for return request %s (approval %s)",
        refund.amount,
        return_request_id,
        approval_id,
    )
    return refund, False


# M9: executes the refund for an approval decided in the Ticket service, but only after verifying that approval.
# Called by the Celery task with the approval it just fetched over gRPC (None if the Ticket service does not know it).
# A failed check refunds nothing: it logs a warning with the reason and returns (no exception, so no retry).
def execute_approved_refund(db: Session, approval_id: str, approval: dict | None) -> None:
    # Step 1: the approval must exist, be approved, and be a refund
    if approval is None:
        logger.warning("Refund verification failed for approval %s: the Ticket service does not know it", approval_id)
        return
    if approval["status"] != "approved":
        logger.warning("Refund verification failed for approval %s: status is %s", approval_id, approval["status"])
        return
    if approval["action_type"] != "refund":
        logger.warning(
            "Refund verification failed for approval %s: action type is %s", approval_id, approval["action_type"]
        )
        return

    # Step 2: its return request must exist here and still be requested (or already refunded, for a repeat)
    return_request = return_repository.get_by_id(db, uuid.UUID(approval["return_request_id"]))
    if return_request is None:
        logger.warning(
            "Refund verification failed for approval %s: return request %s does not exist",
            approval_id,
            approval["return_request_id"],
        )
        return
    if return_request.status not in ("requested", "refunded"):
        logger.warning(
            "Refund verification failed for approval %s: return request status is %s", approval_id, return_request.status
        )
        return

    # Step 3: the approved amount must be exactly the return request's refund amount
    approved_amount = Decimal(approval["amount"])
    if approved_amount != return_request.refund_amount:
        logger.warning(
            "Refund verification failed for approval %s: approved amount %s does not match the refund amount %s",
            approval_id,
            approved_amount,
            return_request.refund_amount,
        )
        return

    # Step 4: execute the idempotent refund. A conflict (this return was refunded under another approval, or
    # this approval was used for another return) is also a verification failure.
    try:
        refund, already_processed = execute_refund(db, return_request.id, uuid.UUID(approval["id"]))
    except ConflictError as error:
        logger.warning("Refund verification failed for approval %s: %s", approval_id, error)
        return

    # Step 5: a repeat (duplicate event or retried task) publishes nothing
    if already_processed:
        logger.info("Approval %s was already refunded (refund %s), nothing to do", approval_id, refund.id)
        return

    # Step 6: announce the new refund (after the commit in execute_refund)
    publish_event(
        "refund.completed",
        {
            "refund_id": refund.id,
            "approval_id": refund.approval_id,
            "return_request_id": refund.return_request_id,
            "ticket_id": approval["ticket_id"],
            "customer_id": return_request.customer_id,
        },
    )
