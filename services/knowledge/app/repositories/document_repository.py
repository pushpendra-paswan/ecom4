import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document


# Repositories only flush (send the SQL); the service decides when to commit
def create(db: Session, slug: str, title: str, content: str) -> Document:
    document = Document(slug=slug, title=title, content=content, status="pending", chunk_count=0)
    db.add(document)
    db.flush()
    return document


def get_by_id(db: Session, document_id: uuid.UUID) -> Document | None:
    return db.scalar(select(Document).where(Document.id == document_id))


def get_by_slug(db: Session, slug: str) -> Document | None:
    return db.scalar(select(Document).where(Document.slug == slug))


# Alphabetical by slug
def list_documents(db: Session) -> list[Document]:
    return list(db.scalars(select(Document).order_by(Document.slug)))


def update_status(
    db: Session,
    document: Document,
    status: str,
    chunk_count: int,
    error_message: str | None,
) -> Document:
    document.status = status
    document.chunk_count = chunk_count
    document.error_message = error_message
    document.updated_at = func.now()
    db.flush()
    return document


def delete(db: Session, document: Document) -> None:
    db.delete(document)
    db.flush()
