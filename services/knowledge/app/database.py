from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Step 1: one engine per service, one short-lived session per request
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


# Step 2: base class all models inherit from (Alembic reads its metadata)
class Base(DeclarativeBase):
    pass


# Step 3: FastAPI dependency that opens a session and always closes it
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
