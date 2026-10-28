-- Knowledge base: runbook and postmortem chunks for hybrid retrieval (DESIGN §7).

CREATE TABLE kb.chunks (
    chunk_id   text PRIMARY KEY,           -- '<doc_id>#<nn>'
    doc_id     text NOT NULL,              -- 'runbooks/schema_drift'
    doc_type   text NOT NULL CHECK (doc_type IN ('runbook', 'postmortem')),
    title      text NOT NULL,
    section    text NOT NULL,              -- heading path within the doc
    text       text NOT NULL,              -- embedded/indexed text, heading path prefixed
    metadata   jsonb NOT NULL DEFAULT '{}',
    embedding  vector(384) NOT NULL,
    -- Keyword index. English stemming for prose, plus a 'simple' copy with '.' and '_' split so
    -- identifiers match by their parts: 'raw.order_items' and 'i.unit_price' are single tokens to
    -- the english parser, but on-call queries often say 'order_items' or 'unit_price'.
    tsv        tsvector GENERATED ALWAYS AS (
        to_tsvector('english', title || ' ' || text)
        || to_tsvector('simple', regexp_replace(title || ' ' || text, '[._]', ' ', 'g'))
    ) STORED
);
CREATE INDEX ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb.chunks USING gin (tsv);
CREATE INDEX ON kb.chunks (doc_type);
