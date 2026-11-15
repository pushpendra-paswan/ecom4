import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import ApprovalCreate, ApprovalResponse, ApprovalStatus, ApproveRequest, RejectRequest
from app.services import approval_service

router = APIRouter()


# Admin only: a testing entry point. From M5/M9 the AI agent creates approvals through gRPC instead.
@router.post("/tickets/{ticket_id}/approvals", response_model=ApprovalResponse, status_code=201)
def create_approval(
    ticket_id: uuid.UUID,
    body: ApprovalCreate,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    return approval_service.create_approval(
        db, ticket_id, body.action_type, body.return_request_id, body.amount, body.summary
    )


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(
    status: ApprovalStatus | None = None,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return approval_service.list_approvals(db, status)


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return approval_service.get_approval(db, approval_id)


# The body is optional: send nothing, or {"note": "..."}
@router.post("/approvals/{approval_id}/approve", response_model=ApprovalResponse)
def approve(
    approval_id: uuid.UUID,
    body: ApproveRequest | None = None,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    note = body.note if body is not None else None
    return approval_service.approve(db, approval_id, uuid.UUID(current_user["user_id"]), note)


# A note is required when rejecting
@router.post("/approvals/{approval_id}/reject", response_model=ApprovalResponse)
def reject(
    approval_id: uuid.UUID,
    body: RejectRequest,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return approval_service.reject(db, approval_id, uuid.UUID(current_user["user_id"]), body.note)
