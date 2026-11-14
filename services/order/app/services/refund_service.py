import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
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
