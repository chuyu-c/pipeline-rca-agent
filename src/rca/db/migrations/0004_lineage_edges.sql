-- Lineage graph built by rca.lineage from DAG SQL (DESIGN §4).
-- Column columns are NULL for table-level edges; src_table is NULL when a table is loaded from
-- outside the warehouse (ingest tasks).

CREATE TABLE lineage.edges (
    src_table   text,
    src_column  text,
    dst_table   text NOT NULL,
    dst_column  text,
    task_id     text NOT NULL,
    UNIQUE NULLS NOT DISTINCT (src_table, src_column, dst_table, dst_column, task_id)
);
CREATE INDEX ON lineage.edges (dst_table);
CREATE INDEX ON lineage.edges (src_table);
