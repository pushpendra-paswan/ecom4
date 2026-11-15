import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Slugs are lowercase words of letters and digits joined by single hyphens, e.g. "returns-policy"
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=1, max_length=100)]
# Text fields must contain at least one visible character (surrounding spaces are removed)
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DocumentCreate(BaseModel):
    slug: Slug
    title: Title
    content: Content  # markdown: an H1 title and H2 sections work best


# Used in the list (no content, to keep it small)
class DocumentSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


# Used for a single document (with content)
class DocumentResponse(DocumentSummaryResponse):
    content: str


class SearchRequest(BaseModel):
    query: Query
    top_k: int = Field(default=4, ge=1, le=10)


# One matching chunk. M5 copies this shape into the .proto file.
class SearchResult(BaseModel):
    document_id: uuid.UUID
    title: str
    section: str
    content: str
    score: float  # cosine similarity (1 - cosine distance): higher is better, 1.0 is a perfect match


class SearchResponse(BaseModel):
    results: list[SearchResult]
