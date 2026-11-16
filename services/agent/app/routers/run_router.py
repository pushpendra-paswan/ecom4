import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import RunDetailResponse, RunResponse, RunStatus
from app.services import run_service

router = APIRouter()


# Staff only: one row per customer message or approval decision the AI handled (answered, skipped or failed),
# newest first, max 50. Light: without the tool calls and retrieved chunks (see GET /runs/{run_id})
@router.get("/runs", response_model=list[RunResponse])
def list_runs(
    ticket_id: uuid.UUID | None = None,
    status: RunStatus | None = None,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return run_service.list_runs(db, ticket_id, status)


# M10, staff only: one run with every column, including its AI activity log
@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return run_service.get_run(db, run_id)


# M10, staff only: the AI activity log of one ticket, every run oldest first with every column (for the dashboard)
@router.get("/tickets/{ticket_id}/activity", response_model=list[RunDetailResponse])
def get_ticket_activity(
    ticket_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return run_service.get_ticket_activity(db, ticket_id)
