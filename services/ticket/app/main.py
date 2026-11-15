import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.routers import approval_router, ticket_router

# Step 1: log to stdout at INFO level (read with `docker compose logs`)
logging.basicConfig(level=logging.INFO)

# Step 2: create the app; root_path is the Nginx prefix so /docs works through Nginx
app = FastAPI(title=f"{settings.service_name} service", root_path=settings.root_path)

# Step 3: add the routers
app.include_router(ticket_router.router)
app.include_router(approval_router.router)


# Step 4: map each service error to its HTTP status code
@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, error: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.exception_handler(UnauthorizedError)
def handle_unauthorized(request: Request, error: UnauthorizedError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": str(error)},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(ForbiddenError)
def handle_forbidden(request: Request, error: ForbiddenError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(error)})


@app.exception_handler(ConflictError)
def handle_conflict(request: Request, error: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.exception_handler(BadRequestError)
def handle_bad_request(request: Request, error: BadRequestError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


# Step 5: health check
@app.get("/health")
def health() -> dict:
    return {"service": settings.service_name, "status": "ok"}
