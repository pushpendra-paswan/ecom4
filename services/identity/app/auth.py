# Token verification. This file is COPIED into every service (see PROJECT_CONTEXT.md, "auth.py copy contract").
# It only needs: the public key (path from config), PyJWT, FastAPI and errors.py.
# When copying, change nothing except what `from app.config` / `from app.errors` import if names differ.
import os

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.errors import ForbiddenError, UnauthorizedError

# Step 1: read the public key once at startup; stop with a clear message if it is missing
if not os.path.isfile(settings.jwt_public_key_path):
    raise RuntimeError(
        f"Public key not found at {settings.jwt_public_key_path}. "
        "Run `python scripts/generate_keys.py` before `docker compose up`."
    )
with open(settings.jwt_public_key_path) as public_key_file:
    PUBLIC_KEY = public_key_file.read()

# auto_error=False so a missing header gives us None and we raise a 401 ourselves (not 403)
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    # Step 1: a token must be present
    if credentials is None:
        raise UnauthorizedError("Missing token")

    # Step 2: check signature and expiry locally with the public key
    try:
        claims = jwt.decode(
            credentials.credentials,
            PUBLIC_KEY,
            algorithms=["RS256"],
            options={"require": ["sub", "email", "role", "exp"]},
        )
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid or expired token")

    # Step 3: return only what the services need
    return {"user_id": claims["sub"], "email": claims["email"], "role": claims["role"]}


def require_role(*allowed_roles: str):
    # Dependency factory: require_role("admin") gives a dependency that only lets admins in
    def check_role(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed_roles:
            raise ForbiddenError("You do not have permission to do this")
        return current_user

    return check_role
