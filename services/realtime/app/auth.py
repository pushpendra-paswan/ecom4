# Token verification for WebSocket connections.
# Deviation from the auth.py copy contract: Realtime has no authenticated REST endpoints, so this file only has
# decode_access_token (no FastAPI dependencies). The checks are exactly the same as in the other services' auth.py.
import os

import jwt

from app.config import settings

# Step 1: read the public key once at startup; stop with a clear message if it is missing
if not os.path.isfile(settings.jwt_public_key_path):
    raise RuntimeError(
        f"Public key not found at {settings.jwt_public_key_path}. "
        "Run `python scripts/generate_keys.py` before `docker compose up`."
    )
with open(settings.jwt_public_key_path) as public_key_file:
    PUBLIC_KEY = public_key_file.read()


# Raises jwt.InvalidTokenError if the token is invalid, expired, signed with another key or missing a claim
def decode_access_token(token: str) -> dict:
    # Step 1: check signature and expiry locally with the public key
    claims = jwt.decode(
        token,
        PUBLIC_KEY,
        algorithms=["RS256"],
        options={"require": ["sub", "email", "role", "exp"]},
    )

    # Step 2: return only what Realtime needs
    return {"user_id": claims["sub"], "email": claims["email"], "role": claims["role"]}
