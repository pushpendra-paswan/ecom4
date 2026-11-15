import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import RunResponse, RunStatus
from app.services import run_service

router = APIRouter()


# Staff only: one row per customer message the AI handled (answered, skipped or failed), newest first, max 50
@router.get("/runs", response_model=list[RunResponse])
def list_runs(
    ticket_id: uuid.UUID | None = None,
    status: RunStatus | None = None,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return run_service.list_runs(db, ticket_id, status)
