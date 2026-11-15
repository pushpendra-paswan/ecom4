import asyncio
import logging

from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger(__name__)

# How long one send may take before the client is treated as broken
SEND_TIMEOUT_SECONDS = 5

# The WebSocket connections held by THIS instance (other instances have their own):
# room "user:<user_id>" -> the sockets of that user (one user can have several tabs open)
user_rooms: dict[str, set[WebSocket]] = {}
# room "staff" -> the sockets of every agent and admin
staff_room: set[WebSocket] = set()


def add_connection(websocket: WebSocket, user_id: str, role: str) -> None:
    # Step 1: everyone joins their own user room
    if user_id not in user_rooms:
        user_rooms[user_id] = set()
    user_rooms[user_id].add(websocket)

    # Step 2: agents and admins also join the staff room
    if role in ("agent", "admin"):
        staff_room.add(websocket)


def remove_connection(websocket: WebSocket, user_id: str) -> None:
    # Step 1: leave the user room, and drop the room when it is empty
    if user_id in user_rooms:
        user_rooms[user_id].discard(websocket)
        if len(user_rooms[user_id]) == 0:
            del user_rooms[user_id]

    # Step 2: leave the staff room (does nothing for customers)
    staff_room.discard(websocket)


# rooms are names like "staff" or "user:<user_id>". Returns how many local connections got the message.
async def deliver_to_rooms(rooms: list[str], message: dict) -> int:
    # Step 1: collect the target sockets with their user id; a dict keeps each socket once even if it is in two rooms
    targets: dict[WebSocket, str] = {}
    for user_id, sockets in user_rooms.items():
        for websocket in sockets:
            if f"user:{user_id}" in rooms or ("staff" in rooms and websocket in staff_room):
                targets[websocket] = user_id

    # Step 2: send to each one; a socket that fails or hangs is removed from its rooms
    delivered = 0
    for websocket, user_id in targets.items():
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=SEND_TIMEOUT_SECONDS)
            delivered += 1
        except Exception as error:
            logger.warning("[%s] send to user %s failed (%r); removing the connection", settings.instance_id, user_id, error)
            remove_connection(websocket, user_id)
    return delivered
