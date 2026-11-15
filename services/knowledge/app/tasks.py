import uuid

from app.celery_app import celery_app
from app.database import SessionLocal
from app.services import document_service


# Thin entry point: chunk, embed and store one document. All the work happens in document_service.
# Any error is retried with exponential backoff (about 1s, 2s, 4s with jitter, never more than 60s), 3 retries
# after the first attempt; after the last one the document stays "failed" with the last error message.
@celery_app.task(
    name="app.tasks.ingest_document",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def ingest_document(document_id: str) -> None:
    # Step 1: open a database session just for this task
    db = SessionLocal()
    try:
        # Step 2: run the ingestion
        document_service.process_ingestion(db, uuid.UUID(document_id))
    finally:
        # Step 3: always close the session
        db.close()
