from pathlib import Path

import psycopg
import pytest

from rca.db import connect
from rca.knowledge.embed import HashEmbedder
from rca.knowledge.index import index_oncall, oncall_chunks
from rca.knowledge.search import search
from tests.integration.conftest import ScratchDb

pytestmark = pytest.mark.integration

ONCALL = Path(__file__).parents[2] / "oncall"
EMBEDDER = HashEmbedder()


@pytest.fixture
def kb(test_db: ScratchDb) -> ScratchDb:
    with connect(test_db.app_url, autocommit=True) as conn:
        index_oncall(conn, ONCALL, EMBEDDER)
    return test_db


def test_index_is_complete_and_rebuild_replaces(kb: ScratchDb) -> None:
    expected = len(oncall_chunks(ONCALL))
    with connect(kb.app_url, autocommit=True) as conn:
        assert index_oncall(conn, ONCALL, EMBEDDER) == expected
        row = conn.execute(
            "SELECT count(*) AS n, count(DISTINCT doc_id) AS docs,"
            " bool_and(tsv IS NOT NULL) AS tsv_ok, min(metadata->>'embedder') AS embedder"
            " FROM kb.chunks"
        ).fetchone()
    assert row == {"n": expected, "docs": 18, "tsv_ok": True, "embedder": "hash:384"}


def test_keyword_search_matches_error_strings_and_identifier_parts(kb: ScratchDb) -> None:
    with connect(kb.app_url, autocommit=True) as conn:
        hits = search(conn, EMBEDDER, "InsufficientPrivilege permission denied", mode="keyword")
        assert hits[0].doc_id in {
            "runbooks/permission",
            "postmortems/2026-03-revoked-grant-after-role-cleanup",
        }
        # 'unit_price' matches the indexed identifier 'i.unit_price' via the split-identifier copy.
        parts = search(conn, EMBEDDER, "unit_price", mode="keyword", k=10)
        assert "runbooks/schema_drift" in {h.doc_id for h in parts}


def test_hybrid_fuses_both_rankings(kb: ScratchDb) -> None:
    query = "stg_order_items failed: UndefinedColumn: column i.unit_price does not exist"
    with connect(kb.app_url, autocommit=True) as conn:
        hits = search(conn, EMBEDDER, query, mode="hybrid", k=5)
    assert len(hits) == 5
    assert any(set(h.ranks) == {"vector", "keyword"} for h in hits)
    assert hits == sorted(hits, key=lambda h: -h.score)
    assert "runbooks/schema_drift" in {h.doc_id for h in hits}


def test_doc_type_filter(kb: ScratchDb) -> None:
    with connect(kb.app_url, autocommit=True) as conn:
        for mode in ("vector", "keyword", "hybrid"):
            hits = search(
                conn, EMBEDDER, "freshness partition missing", mode=mode, doc_type="postmortem"
            )
            assert hits and {h.doc_type for h in hits} == {"postmortem"}, mode


def test_search_refuses_an_index_built_by_another_embedder(kb: ScratchDb) -> None:
    other = HashEmbedder(dim=384)
    other_name = type("Renamed", (), {"name": "fastembed:x", "dim": 384})
    with connect(kb.app_url, autocommit=True) as conn:
        assert search(conn, other, "anything", mode="vector")  # same name: fine
        with pytest.raises(ValueError, match="built with hash:384"):
            search(conn, other_name(), "anything", mode="vector")  # type: ignore[arg-type]


def test_readonly_role_cannot_read_the_knowledge_base(kb: ScratchDb) -> None:
    with (
        connect(kb.readonly_url, autocommit=True) as ro,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        ro.execute("SELECT 1 FROM kb.chunks")
