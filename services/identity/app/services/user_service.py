import uuid

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import User
from app.repositories import user_repository


def get_user_profile(db: Session, user_id: str) -> User:
    # Step 1: load the user the token belongs to
    user = user_repository.get_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise NotFoundError("User not found")

    # Step 2: return the profile
    return user


def list_users(db: Session) -> list[User]:
    return user_repository.list_all(db)
