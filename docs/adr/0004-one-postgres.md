# 0004 — One Postgres for warehouse, ops, lineage, knowledge and the queue

Status: accepted

## Context
The system needs a warehouse to break, scheduler metadata, alerts/incidents, a lineage graph, vector +
keyword retrieval, and (designed) a job queue. The conventional stack would be several services:
a warehouse, a queue (Redis/Kafka), a vector store (Qdrant), a search engine (OpenSearch).

## Decision
One Postgres 16 with pgvector, separated by schema: `raw`/`stg`/`mart` (warehouse), `ops`, `lineage`,
`kb` (HNSW embeddings + `tsvector`), and `sim` (fault switches). Two roles enforce the boundary:
`rca_app` for the system, `rca_readonly` (SELECT on raw/stg/mart only, 5s statement timeout) for every
query the LLM writes.

## Consequences
- One container to run; `make up` is the whole infrastructure. Integration tests create a scratch
  database per test in seconds.
- Alerts, incidents and jobs change in one transaction (an advisory lock serialises alert ingest), so
  correlation can't race into duplicate incidents.
- The read-only role is a real second layer behind the SQL guard; tests run the guard's bypass cases
  against the role directly and see `InsufficientPrivilege`.
- Hybrid retrieval is one SQL query per retriever. Keyword ranking needed care: unnormalised
  `ts_rank_cd` favoured long documents (keyword recall@5 0.44 → 1.00 with length normalisation).
- Where it breaks: warehouse-scale data, high alert throughput, or a corpus beyond what HNSW in
  Postgres serves comfortably. The schema boundaries are where a split would happen.
