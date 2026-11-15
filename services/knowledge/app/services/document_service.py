import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import rag
from app.celery_app import celery_app
from app.errors import ConflictError, NotFoundError
from app.models import Document
from app.repositories import document_repository

logger = logging.getLogger(__name__)

# Tasks are sent by name, so this module does not import app.tasks (which imports this module)
INGEST_TASK_NAME = "app.tasks.ingest_document"
# Long error messages (for example a full API error) are cut to this length before they are saved
MAX_ERROR_MESSAGE_LENGTH = 1000


# Saves a new document as "pending" and queues its ingestion. The API answers straight away (202).
def create_document(db: Session, slug: str, title: str, content: str) -> Document:
    # Step 1: slugs are unique
    if document_repository.get_by_slug(db, slug) is not None:
        raise ConflictError(f"A document with slug '{slug}' already exists")

    # Step 2: save it; the unique constraint catches two simultaneous creates with the same slug
    try:
        document = document_repository.create(db, slug, title, content)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(f"A document with slug '{slug}' already exists")

    # Step 3: queue the ingestion only after the commit, so the worker can find the row
    celery_app.send_task(INGEST_TASK_NAME, args=[str(document.id)])
    logger.info("Document %s (%s) created, ingestion queued", document.id, slug)
    return document


def list_documents(db: Session) -> list[Document]:
    return document_repository.list_documents(db)


def get_document(db: Session, document_id: uuid.UUID) -> Document:
    # Step 1: load the document
    document = document_repository.get_by_id(db, document_id)
    if document is None:
        raise NotFoundError("Document not found")

    # Step 2: return it
    return document


# Queues the ingestion again (for example after a failure). Safe at any time: ingestion replaces the old chunks.
def reingest_document(db: Session, document_id: uuid.UUID) -> Document:
    # Step 1: load the document
    document = document_repository.get_by_id(db, document_id)
    if document is None:
        raise NotFoundError("Document not found")

    # Step 2: back to "pending" (the chunk count stays until the new ingestion replaces the chunks)
    document_repository.update_status(db, document, "pending", document.chunk_count, None)
    db.commit()

    # Step 3: queue the ingestion after the commit
    celery_app.send_task(INGEST_TASK_NAME, args=[str(document.id)])
    logger.info("Document %s re-ingestion queued", document.id)
    return document


def delete_document(db: Session, document_id: uuid.UUID) -> None:
    # Step 1: load the document
    document = document_repository.get_by_id(db, document_id)
    if document is None:
        raise NotFoundError("Document not found")

    # Step 2: remove its chunks from the vector store first, so no orphaned chunks stay searchable
    rag.delete_document_chunks(str(document.id), document.chunk_count)

    # Step 3: remove the row
    document_repository.delete(db, document)
    db.commit()
    logger.info("Document %s deleted with %s chunk(s)", document_id, document.chunk_count)


# Called by the Celery task. Re-raises any error so Celery can retry.
def process_ingestion(db: Session, document_id: uuid.UUID) -> None:
    # Step 1: load the document; it may have been deleted after the task was queued
    document = document_repository.get_by_id(db, document_id)
    if document is None:
        logger.warning("Document %s no longer exists, skipping ingestion", document_id)
        return

    # Step 2: mark it as processing
    previous_chunk_count = document.chunk_count
    document_repository.update_status(db, document, "processing", previous_chunk_count, None)
    db.commit()
    logger.info("Ingesting document %s (%s)", document.id, document.slug)

    # Step 3: chunk, embed and store (rag.py replaces the previous chunks)
    try:
        chunk_count = rag.ingest_document_chunks(
            str(document.id), document.title, document.content, previous_chunk_count
        )
    except Exception as error:
        # Step 4 (failure): save "failed" with the error, then re-raise for Celery's retry.
        # The chunk count is 0 because rag.py removes the old chunks before embedding the new ones.
        db.rollback()
        error_message = f"{type(error).__name__}: {error}"[:MAX_ERROR_MESSAGE_LENGTH]
        document_repository.update_status(db, document, "failed", 0, error_message)
        db.commit()
        logger.error("Ingestion of document %s failed: %s", document.id, error_message)
        raise

    # Step 4 (success): mark it ready with the number of chunks
    document_repository.update_status(db, document, "ready", chunk_count, None)
    db.commit()
    logger.info("Document %s is ready with %s chunk(s)", document.id, chunk_count)
