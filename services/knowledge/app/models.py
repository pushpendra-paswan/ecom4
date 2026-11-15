import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# A policy or FAQ document. Its vectors live in PGVector's own tables (langchain_pg_*), not here.
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)  # markdown
    status: Mapped[str] = mapped_column(String(20))  # pending | processing | ready | failed
    error_message: Mapped[str | None] = mapped_column(Text)  # last ingestion error, only when failed
    chunk_count: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set again whenever the status changes
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
