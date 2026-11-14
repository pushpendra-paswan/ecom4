from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import RefundExecuteRequest, RefundResponse
from app.services import refund_service

router = APIRouter()


# Admin only: the testing entry point (in M9 a Celery task calls the same service function)
@router.post("/refunds/execute", response_model=RefundResponse, status_code=status.HTTP_201_CREATED)
def execute_refund(
    body: RefundExecuteRequest,
    response: Response,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    # Step 1: run the refund (it never trusts an amount from the caller)
    refund, already_processed = refund_service.execute_refund(db, body.return_request_id, body.approval_id)

    # Step 2: 201 for a new refund, 200 when the same approval was already processed
    if already_processed:
        response.status_code = status.HTTP_200_OK
    refund_response = RefundResponse.model_validate(refund)
    refund_response.already_processed = already_processed
    return refund_response
