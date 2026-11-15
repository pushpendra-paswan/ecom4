from fastapi import APIRouter, Depends

from app.auth import require_role
from app.schemas import SearchRequest, SearchResponse
from app.services import search_service

router = APIRouter()


# Staff only on purpose: every search costs an embedding call. The AI agent will search through gRPC (M5).
@router.post("/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    current_user: dict = Depends(require_role("agent", "admin")),
):
    results = search_service.search_knowledge(body.query, body.top_k)
    return {"results": results}
