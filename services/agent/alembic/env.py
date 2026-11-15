from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.database import Base
from app import models  # noqa: F401  (importing the models registers their tables)

config = context.config

# Step 1: set up logging from alembic.ini
fileConfig(config.config_file_name, disable_existing_loggers=False)

# Step 2: the tables Alembic compares against (used by `alembic revision --autogenerate`)
target_metadata = Base.metadata


# Step 3: LangGraph's Postgres checkpointer creates and owns its tables (checkpoints, checkpoint_blobs,
# checkpoint_writes, checkpoint_migrations), so autogenerate must never try to drop or change them.
def include_object(object, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name.startswith("checkpoint"):
        return False
    return True


# Step 4: run the migrations against the database from DATABASE_URL
engine = create_engine(settings.database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()
