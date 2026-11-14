import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


# user_id is only passed by the seed script (fixed demo ids); otherwise the model makes one
def create(
    db: Session,
    email: str,
    full_name: str,
    password_hash: str,
    role: str,
    user_id: uuid.UUID | None = None,
) -> User:
    user = User(email=email, full_name=full_name, password_hash=password_hash, role=role)
    if user_id is not None:
        user.id = user_id
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_all(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.email)))
