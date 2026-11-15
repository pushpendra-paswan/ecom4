import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.schemas import DocumentCreate, DocumentResponse, DocumentSummaryResponse
from app.services import document_service

router = APIRouter()


# Admin only: saves the document and answers straight away; a Celery worker ingests it in the background
@router.post("/documents", response_model=DocumentResponse, status_code=202)
def create_document(
    body: DocumentCreate,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    return document_service.create_document(db, body.slug, body.title, body.content)


# Staff: all documents with their ingestion status and chunk count
@router.get("/documents", response_model=list[DocumentSummaryResponse])
def list_documents(
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return document_service.list_documents(db)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    current_user: dict = Depends(require_role("agent", "admin")),
    db: Session = Depends(get_db),
):
    return document_service.get_document(db, document_id)


# Admin only: queue the ingestion again (for example after a failure)
@router.post("/documents/{document_id}/reingest", response_model=DocumentResponse, status_code=202)
def reingest_document(
    document_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    return document_service.reingest_document(db, document_id)


# Admin only: removes the document and its chunks
@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    document_service.delete_document(db, document_id)
    return Response(status_code=204)
