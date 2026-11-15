# Seeds the knowledge base with the policy documents in seed_docs/ (one markdown file per document).
# Run with: docker compose exec knowledge python seed.py
# Safe to run again: a document whose slug already exists is skipped, so nothing is embedded twice.
# The slug is the file name without ".md"; the title is the file's first line (the H1 heading).
from pathlib import Path

from app.database import SessionLocal
from app.repositories import document_repository
from app.services import document_service

SEED_DOCS_FOLDER = Path(__file__).parent / "seed_docs"

created_count = 0
skipped_count = 0

# Step 1: open a database session
db = SessionLocal()

# Step 2: go through the markdown files in a fixed order
for file_path in sorted(SEED_DOCS_FOLDER.glob("*.md")):
    slug = file_path.stem
    content = file_path.read_text(encoding="utf-8")

    # Step 3: skip documents that already exist (never re-embed them)
    if document_repository.get_by_slug(db, slug) is not None:
        print(f"skipped  {slug} (already exists)")
        skipped_count += 1
        continue

    # Step 4: the title is the H1 heading on the first line
    title = content.splitlines()[0].removeprefix("#").strip()

    # Step 5: save the document and queue its ingestion (the same service function the API uses)
    document = document_service.create_document(db, slug, title, content)
    print(f"created  {slug} \"{title}\" (id {document.id}, ingestion queued)")
    created_count += 1

# Step 6: close the session and print a summary
db.close()
print(f"Done: {created_count} created, {skipped_count} skipped.")
if created_count > 0:
    print("Watch the ingestion with: docker compose logs -f knowledge-worker")
