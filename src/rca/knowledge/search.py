"""Vector, keyword and hybrid (Reciprocal Rank Fusion) search over `kb.chunks` (DESIGN §7).

Error strings and identifiers (`UndefinedColumn`, `raw.order_items`) match lexically far better than
semantically; prose symptoms ("dashboard empty after the feed was late") the other way round. RRF
combines the two rankings without having to calibrate their scores against each other.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from psycopg import sql

from rca.db import Connection
from rca.knowledge.chunking import DocType
from rca.knowledge.embed import Embedder
from rca.knowledge.index import indexed_embedder, to_pgvector

Mode = Literal["vector", "keyword", "hybrid"]
RRF_K = 60


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    doc_type: str
    title: str
    section: str
    text: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # mode -> 1-based rank in that list


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = Σ_lists 1 / (k + rank_d), rank 1-based.

    Items missing from a list contribute nothing for it. Ties break by first appearance across
    the lists (in list order), so the output is deterministic.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    order = {item: i for i, item in enumerate(dict.fromkeys(i for r in rankings for i in r))}
    return sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def _filter(doc_type: DocType | None) -> tuple[sql.Composable, tuple[str, ...]]:
    if doc_type is None:
        return sql.SQL(""), ()
    return sql.SQL("AND doc_type = %s"), (doc_type,)


def _keyword_query(conn: Connection, query: str) -> str | None:
    """OR of the query's lexemes (english-stemmed + identifier parts), as tsquery text.

    plainto_tsquery ANDs every term, which almost never matches a long symptom description.
    """
    row = conn.execute(
        "SELECT plainto_tsquery('english', %s)::text AS en,"
        " plainto_tsquery('simple', regexp_replace(%s, '[._]', ' ', 'g'))::text AS simple",
        (query, query),
    ).fetchone()
    assert row is not None
    parts = [p.replace(" & ", " | ") for p in (row["en"], row["simple"]) if p]
    return " | ".join(f"({p})" for p in parts) or None


_COLUMNS = sql.SQL("chunk_id, doc_id, doc_type, title, section, text")


def vector_search(
    conn: Connection, embedder: Embedder, query: str, k: int, doc_type: DocType | None = None
) -> list[SearchHit]:
    where, params = _filter(doc_type)
    q = sql.SQL(
        "SELECT {cols}, 1 - (embedding <=> %s::vector) AS score FROM kb.chunks"
        " WHERE true {where} ORDER BY embedding <=> %s::vector LIMIT %s"
    ).format(cols=_COLUMNS, where=where)
    vec = to_pgvector(embedder.embed_query(query))
    rows = conn.execute(q, (vec, *params, vec, k)).fetchall()
    return [SearchHit(**r, ranks={"vector": i}) for i, r in enumerate(rows, 1)]


def keyword_search(
    conn: Connection, query: str, k: int, doc_type: DocType | None = None
) -> list[SearchHit]:
    tsquery = _keyword_query(conn, query)
    if tsquery is None:
        return []
    where, params = _filter(doc_type)
    q = sql.SQL(
        # Normalisation 2 divides by document length. Without it, whole-document postmortem chunks
        # out-rank short runbook sections just by containing more words; on the scenario symptoms
        # this took keyword recall@5 from 0.44 to 1.00 (MRR 0.64 -> 0.93).
        "SELECT {cols}, ts_rank_cd(tsv, %s::tsquery, 2) AS score FROM kb.chunks"
        " WHERE tsv @@ %s::tsquery {where} ORDER BY score DESC, chunk_id LIMIT %s"
    ).format(cols=_COLUMNS, where=where)
    rows = conn.execute(q, (tsquery, tsquery, *params, k)).fetchall()
    return [SearchHit(**r, ranks={"keyword": i}) for i, r in enumerate(rows, 1)]


def search(
    conn: Connection,
    embedder: Embedder,
    query: str,
    *,
    mode: Mode = "hybrid",
    k: int = 5,
    doc_type: DocType | None = None,
    candidates: int = 20,
) -> list[SearchHit]:
    """Top-k chunks. Hybrid fuses the top `candidates` of each retriever with RRF."""
    if mode != "keyword" and (built := indexed_embedder(conn)) not in (None, embedder.name):
        raise ValueError(f"index was built with {built}, but querying with {embedder.name}")
    if mode == "vector":
        return vector_search(conn, embedder, query, k, doc_type)
    if mode == "keyword":
        return keyword_search(conn, query, k, doc_type)

    by_mode = {
        "vector": vector_search(conn, embedder, query, candidates, doc_type),
        "keyword": keyword_search(conn, query, candidates, doc_type),
    }
    hits: dict[str, SearchHit] = {}
    for mode_name, mode_hits in by_mode.items():
        for hit in mode_hits:
            merged = hits.setdefault(hit.chunk_id, hit)
            merged.ranks[mode_name] = hit.ranks[mode_name]
    fused = rrf([[h.chunk_id for h in mode_hits] for mode_hits in by_mode.values()])
    results = []
    for chunk_id, score in fused[:k]:
        hit = hits[chunk_id]
        hit.score = score
        results.append(hit)
    return results
