# All RAG code for the knowledge service: chunking, embedding, storing and searching.
# Vectors are stored by LangChain's PGVector in its own tables (langchain_pg_collection, langchain_pg_embedding)
# in knowledge_db. Alembic ignores those tables (see alembic/env.py).
from langchain_core.documents import Document as LangChainDocument
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.config import settings
from app.database import engine

# Chunking: first split by markdown headings, then split long sections into pieces of about this size
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
# Every chunk of every document lives in this one PGVector collection
COLLECTION_NAME = "knowledge_base"
# text-embedding-3-small returns 1536 numbers per text; a different model needs a new collection
EMBEDDING_DIMENSIONS = 1536
# Used when a chunk has no H2 heading above it (for example the introduction under the H1 title)
DEFAULT_SECTION = "Overview"


# Opens the PGVector store. Used by the three entry points below.
# It reuses the service's SQLAlchemy engine (built from the postgresql+psycopg:// DATABASE_URL), and the first
# call creates the langchain_pg_* tables and the collection if they do not exist yet.
def open_vector_store() -> PGVector:
    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    return PGVector(
        embeddings=embeddings,
        connection=engine,
        collection_name=COLLECTION_NAME,
        embedding_length=EMBEDDING_DIMENSIONS,
        use_jsonb=True,
        create_extension=False,  # the Postgres init script already created the vector extension
    )


# Splits a document into chunks, embeds them and stores them. Safe to run again for the same document:
# chunk ids are "<document_id>:<chunk number>", the old ones are deleted first, and PGVector upserts by id.
def ingest_document_chunks(document_id: str, title: str, content: str, previous_chunk_count: int) -> int:
    vector_store = open_vector_store()

    # Step 1: delete the chunks from the previous ingestion (their ids are known from the old chunk count)
    previous_chunk_ids = [f"{document_id}:{chunk_number}" for chunk_number in range(previous_chunk_count)]
    if previous_chunk_ids:
        vector_store.delete(ids=previous_chunk_ids)

    # Step 2: split the markdown by headings (H1 goes into "title_heading", H2 into "section" metadata)
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "title_heading"), ("##", "section")])
    sections = header_splitter.split_text(content)

    # Step 3: split long sections into smaller overlapping pieces (the section metadata is kept)
    size_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    pieces = size_splitter.split_documents(sections)
    if not pieces:
        raise ValueError("The document has no text to index")

    # Step 4: build the chunks: "Title > Section" in front of the text so retrieval knows the topic,
    # plus the metadata the search results need, and a deterministic id
    chunks = []
    chunk_ids = []
    for chunk_number, piece in enumerate(pieces):
        section = piece.metadata.get("section", DEFAULT_SECTION)
        chunk_text = f"{title} > {section}\n\n{piece.page_content}"
        metadata = {"document_id": document_id, "title": title, "section": section}
        chunks.append(LangChainDocument(page_content=chunk_text, metadata=metadata))
        chunk_ids.append(f"{document_id}:{chunk_number}")

    # Step 5: embed all chunks with OpenAI and store them in PGVector (one insert for the whole document)
    vector_store.add_documents(chunks, ids=chunk_ids)

    # Step 6: return how many chunks were stored
    return len(chunks)


# Removes a document's chunks by their deterministic ids
def delete_document_chunks(document_id: str, chunk_count: int) -> None:
    # Step 1: nothing was stored if the document never finished ingesting
    if chunk_count == 0:
        return

    # Step 2: delete the ids "<document_id>:0" ... "<document_id>:<chunk_count - 1>"
    vector_store = open_vector_store()
    chunk_ids = [f"{document_id}:{chunk_number}" for chunk_number in range(chunk_count)]
    vector_store.delete(ids=chunk_ids)


# Finds the chunks most similar to the question. PGVector uses cosine distance (0 = same direction,
# 2 = opposite), so the score is 1 - distance: the cosine similarity, where higher is better (1.0 is a perfect match).
def search_chunks(query: str, top_k: int) -> list[dict]:
    # Step 1: embed the question and find the nearest chunks with their distances
    vector_store = open_vector_store()
    matches = vector_store.similarity_search_with_score(query, k=top_k)

    # Step 2: turn them into plain dicts, best match first
    results = []
    for chunk, distance in matches:
        results.append({
            "document_id": chunk.metadata["document_id"],
            "title": chunk.metadata["title"],
            "section": chunk.metadata["section"],
            "content": chunk.page_content,
            "score": round(1 - distance, 4),
        })
    return results
