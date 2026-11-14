import logging

from fastapi import FastAPI

from app.config import settings

# Step 1: log to stdout at INFO level (read with `docker compose logs`)
logging.basicConfig(level=logging.INFO)

# Step 2: create the app; root_path is the Nginx prefix so /docs works through Nginx
app = FastAPI(title=f"{settings.service_name} service", root_path=settings.root_path)


# Step 3: health check
@app.get("/health")
def health() -> dict:
    return {"service": settings.service_name, "status": "ok"}
