from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import UserResponse
from app.services import user_service

router = APIRouter()


# Admin only: proves that role checks work
@router.get("/users", response_model=list[UserResponse])
def list_users(current_user: dict = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return user_service.list_users(db)
