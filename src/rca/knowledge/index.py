"""Build `kb.chunks` from the `oncall/` docs: chunk → embed → replace the index atomically."""

from collections.abc import Sequence
from pathlib import Path

from psycopg.types.json import Jsonb

from rca.db import Connection
from rca.knowledge.chunking import Chunk, chunk_document, load_oncall
from rca.knowledge.embed import Embedder


def to_pgvector(vec: Sequence[float]) -> str:
    """pgvector text literal ('[0.1,0.2,...]'); cast with ::vector in SQL."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def oncall_chunks(oncall_dir: Path) -> list[Chunk]:
    return [c for doc in load_oncall(oncall_dir) for c in chunk_document(doc)]


def index_oncall(conn: Connection, oncall_dir: Path, embedder: Embedder) -> int:
    chunks = oncall_chunks(oncall_dir)
    vectors = embedder.embed_documents([c.text for c in chunks])
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM kb.chunks")
        cur.executemany(
            "INSERT INTO kb.chunks"
            " (chunk_id, doc_id, doc_type, title, section, text, metadata, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
            [
                (
                    c.chunk_id, c.doc_id, c.doc_type, c.title, c.section, c.text,
                    Jsonb({**c.metadata, "embedder": embedder.name}), to_pgvector(v),
                )
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )  # fmt: skip
    return len(chunks)


def indexed_embedder(conn: Connection) -> str | None:
    """Name of the embedder the current index was built with (None if empty)."""
    row = conn.execute("SELECT metadata->>'embedder' AS name FROM kb.chunks LIMIT 1").fetchone()
    return row["name"] if row else None
