from app import rag
from app.errors import BadRequestError

DEFAULT_TOP_K = 4
MAX_TOP_K = 10
MAX_QUERY_LENGTH = 500


# Returns the chunks that best match the question, best first.
# The checks repeat the REST validation because the gRPC layer (M5) will call this function too.
def search_knowledge(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    # Step 1: the question must have text and must not be too long
    query = query.strip()
    if query == "" or len(query) > MAX_QUERY_LENGTH:
        raise BadRequestError(f"The query must be 1 to {MAX_QUERY_LENGTH} characters")

    # Step 2: top_k must be in range
    if top_k < 1 or top_k > MAX_TOP_K:
        raise BadRequestError(f"top_k must be between 1 and {MAX_TOP_K}")

    # Step 3: search the vector store
    return rag.search_chunks(query, top_k)
