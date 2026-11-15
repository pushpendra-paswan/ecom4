import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import connections
from app.auth import decode_access_token
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# The client must authenticate within this many seconds after connecting
AUTH_TIMEOUT_SECONDS = 5
# Close code for a missing, invalid or expired token (4000-4999 are free for applications)
CLOSE_CODE_UNAUTHORIZED = 4401


# Browser URL: ws://localhost:8080/ws/connect (Nginx strips /ws, so the path here is /connect).
# Browsers cannot set headers on a WebSocket and tokens must not go in URLs, so the token comes in the first message.
@router.websocket("/connect")
async def websocket_connect(websocket: WebSocket) -> None:
    # Step 1: accept the connection
    await websocket.accept()

    # Step 2: the first message must be {"type": "auth", "token": "<jwt>"} and arrive within 5 seconds.
    # Anything else (timeout, bad JSON, wrong type, invalid or expired token) closes the socket with 4401.
    try:
        first_message = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS)
        if first_message.get("type") != "auth":
            raise ValueError("The first message must be an auth message")
        user = decode_access_token(first_message.get("token"))
    except WebSocketDisconnect:
        return
    except Exception as error:
        logger.info("[%s] authentication failed (%s); closing with %s", settings.instance_id, type(error).__name__, CLOSE_CODE_UNAUTHORIZED)
        await websocket.close(code=CLOSE_CODE_UNAUTHORIZED, reason="Unauthorized")
        return

    # Step 3: join the rooms and confirm
    user_id = user["user_id"]
    connections.add_connection(websocket, user_id, user["role"])
    logger.info("[%s] connected: user %s (%s)", settings.instance_id, user_id, user["role"])

    try:
        await websocket.send_json(
            {"type": "auth_ok", "user_id": user_id, "role": user["role"], "instance": settings.instance_id}
        )

        # Step 4: answer pings until the client goes away (events are pushed by the Redis subscriber meanwhile)
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                await websocket.send_json({"type": "error", "message": "Messages must be JSON"})
                continue

            if isinstance(message, dict) and message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json({"type": "error", "message": "Unknown message type"})
    except WebSocketDisconnect:
        pass
    finally:
        # Step 5: leave the rooms
        connections.remove_connection(websocket, user_id)
        logger.info("[%s] disconnected: user %s", settings.instance_id, user_id)
