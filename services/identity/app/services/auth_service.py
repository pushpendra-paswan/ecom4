import os
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ConflictError, UnauthorizedError
from app.models import User
from app.repositories import user_repository

# Step 1: read the private key once at startup; stop with a clear message if it is missing
if not os.path.isfile(settings.jwt_private_key_path):
    raise RuntimeError(
        f"Private key not found at {settings.jwt_private_key_path}. "
        "Run `python scripts/generate_keys.py` before `docker compose up`."
    )
with open(settings.jwt_private_key_path) as private_key_file:
    PRIVATE_KEY = private_key_file.read()

password_hasher = PasswordHasher()


def register_customer(db: Session, email: str, password: str, full_name: str) -> User:
    # Step 1: emails are stored in lowercase so "A@x.com" and "a@x.com" are the same user
    email = email.lower()

    # Step 2: refuse an email that is already registered
    if user_repository.get_by_email(db, email) is not None:
        raise ConflictError("Email is already registered")

    # Step 3: hash the password and save the user (self-registration is always a customer)
    password_hash = password_hasher.hash(password)
    return user_repository.create(db, email, full_name, password_hash, "customer")


def login(db: Session, email: str, password: str) -> dict:
    # Step 1: find the user; the error message is the same for every failure on purpose
    user = user_repository.get_by_email(db, email.lower())
    if user is None:
        raise UnauthorizedError("Invalid email or password")

    # Step 2: check the password against the stored hash
    try:
        password_hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        raise UnauthorizedError("Invalid email or password")

    # Step 3: build the token claims
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }

    # Step 4: sign the token with the private key (RS256)
    access_token = jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")
    return {"access_token": access_token, "token_type": "bearer"}
