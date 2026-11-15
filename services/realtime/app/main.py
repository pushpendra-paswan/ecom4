import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import events
from app.config import settings
from app.routers import ws_router

# Step 1: log to stdout at INFO level (read with `docker compose logs`)
logging.basicConfig(level=logging.INFO)


# Step 2: start the two background tasks with the app, and stop them when the app stops
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger(__name__).info("[%s] realtime instance starting", settings.instance_id)
    subscriber_task = asyncio.create_task(events.subscribe_redis_events())
    consumer_task = asyncio.create_task(events.consume_rabbitmq_events())
    yield
    consumer_task.cancel()
    subscriber_task.cancel()
    await asyncio.gather(consumer_task, subscriber_task, return_exceptions=True)


# Step 3: create the app; root_path is the Nginx prefix so /docs works through Nginx
app = FastAPI(title=f"{settings.service_name} service", root_path=settings.root_path, lifespan=lifespan)

# Step 4: add the WebSocket endpoint
app.include_router(ws_router.router)


# Step 5: health check
@app.get("/health")
def health() -> dict:
    return {"service": settings.service_name, "status": "ok", "instance": settings.instance_id}
