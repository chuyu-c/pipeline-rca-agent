import math

from rca.knowledge.embed import EMBEDDING_DIM, HashEmbedder, make_embedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_embedder_is_deterministic_and_normalised() -> None:
    e = HashEmbedder()
    v = e.embed_query("UndefinedColumn: column i.unit_price does not exist")
    assert len(v) == EMBEDDING_DIM == e.dim
    assert v == HashEmbedder().embed_query("UndefinedColumn: column i.unit_price does not exist")
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)
    assert e.embed_documents(["a", "b"]) == [e.embed_query("a"), e.embed_query("b")]


def test_hash_embedder_similarity_tracks_shared_words() -> None:
    e = HashEmbedder()
    q = e.embed_query("permission denied for table products")
    related = e.embed_query("InsufficientPrivilege: permission denied for table raw.products")
    unrelated = e.embed_query("inventory snapshot file has not landed yet")
    assert cosine(q, related) > cosine(q, unrelated)


def test_make_embedder_does_not_load_models() -> None:
    assert make_embedder("hash", "ignored").name == "hash:384"
    fast = make_embedder("fastembed", "BAAI/bge-small-en-v1.5")
    assert fast.name == "fastembed:BAAI/bge-small-en-v1.5"  # lazy: no download yet
