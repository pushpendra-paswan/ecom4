from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services import auth_service, user_service

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register_customer(db, body.email, body.password, body.full_name)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login(db, body.email, body.password)


@router.get("/me", response_model=UserResponse)
def me(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_service.get_user_profile(db, current_user["user_id"])
