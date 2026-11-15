import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import BadRequestError, ConflictError, NotFoundError
from app.models import Approval
from app.repositories import approval_repository, ticket_repository

logger = logging.getLogger(__name__)


# Creates an approval request for a sensitive action on a ticket.
# Refunds at or below REFUND_AUTO_APPROVE_LIMIT are approved straight away by policy; larger ones wait for a human.
def create_approval(
    db: Session,
    ticket_id: uuid.UUID,
    action_type: str,
    return_request_id: uuid.UUID,
    amount: Decimal,
    summary: str,
) -> Approval:
    # Step 1: the ticket must exist
    ticket = ticket_repository.get_by_id(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")

    # Step 2: only one approval per return request
    if approval_repository.get_by_return_request_id(db, return_request_id) is not None:
        raise ConflictError("An approval already exists for this return request")

    # Step 3: apply the auto-approve policy
    auto_approved = action_type == "refund" and amount <= settings.refund_auto_approve_limit
    if auto_approved:
        status = "approved"
        decided_at = datetime.now(timezone.utc)
    else:
        status = "pending"
        decided_at = None

    # Step 4: save the approval; the unique return_request_id stops two simultaneous requests
    try:
        approval = approval_repository.create(
            db, ticket.id, action_type, return_request_id, amount, summary, status, auto_approved, decided_at
        )

        # Step 5: a pending approval puts the ticket on hold until someone decides
        if status == "pending":
            ticket_repository.update_status(db, ticket, "awaiting_approval")

        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("An approval already exists for this return request")

    # Step 6: log the outcome
    logger.info(
        "Approval %s for return request %s (amount %s) created as %s%s",
        approval.id,
        return_request_id,
        amount,
        status,
        " (auto-approved by policy)" if auto_approved else "",
    )
    return approval


def list_approvals(db: Session, status: str | None = None) -> list[Approval]:
    return approval_repository.list_approvals(db, status)


def get_approval(db: Session, approval_id: uuid.UUID) -> Approval:
    # Step 1: load the approval
    approval = approval_repository.get_by_id(db, approval_id)
    if approval is None:
        raise NotFoundError("Approval not found")

    # Step 2: return it
    return approval


def approve(db: Session, approval_id: uuid.UUID, decided_by: uuid.UUID, note: str | None = None) -> Approval:
    # Step 1: load the approval and lock its row, so two simultaneous decisions cannot both succeed
    approval = approval_repository.get_by_id_for_update(db, approval_id)
    if approval is None:
        raise NotFoundError("Approval not found")

    # Step 2: only a pending approval can be decided
    if approval.status != "pending":
        raise ConflictError(f"Approval has already been decided (status: {approval.status})")

    # Step 3: record the decision
    approval_repository.update(db, approval, "approved", decided_by, note, datetime.now(timezone.utc))

    # Step 4: the ticket is no longer waiting
    ticket = ticket_repository.get_by_id(db, approval.ticket_id)
    if ticket.status == "awaiting_approval":
        ticket_repository.update_status(db, ticket, "open")

    # Step 5: save (this also releases the row lock)
    db.commit()
    logger.info("Approval %s approved by %s", approval.id, decided_by)
    return approval


def reject(db: Session, approval_id: uuid.UUID, decided_by: uuid.UUID, note: str) -> Approval:
    # Step 1: a rejection must explain why (checked here too, because gRPC callers skip the REST validation)
    if note is None or note.strip() == "":
        raise BadRequestError("A note is required when rejecting")

    # Step 2: load the approval and lock its row, so two simultaneous decisions cannot both succeed
    approval = approval_repository.get_by_id_for_update(db, approval_id)
    if approval is None:
        raise NotFoundError("Approval not found")

    # Step 3: only a pending approval can be decided
    if approval.status != "pending":
        raise ConflictError(f"Approval has already been decided (status: {approval.status})")

    # Step 4: record the decision
    approval_repository.update(db, approval, "rejected", decided_by, note.strip(), datetime.now(timezone.utc))

    # Step 5: the ticket is no longer waiting
    ticket = ticket_repository.get_by_id(db, approval.ticket_id)
    if ticket.status == "awaiting_approval":
        ticket_repository.update_status(db, ticket, "open")

    # Step 6: save (this also releases the row lock)
    db.commit()
    logger.info("Approval %s rejected by %s", approval.id, decided_by)
    return approval
