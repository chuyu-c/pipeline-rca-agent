-- Scheduler metadata written by rca.simulator.runner (DESIGN §4).
-- One task_runs row per attempt, Airflow-style (`try_number`), so retries are visible history.

CREATE TABLE ops.task_runs (
    run_id         text        PRIMARY KEY,
    dag_id         text        NOT NULL,
    task_id        text        NOT NULL,
    logical_date   date        NOT NULL,
    try_number     integer     NOT NULL,
    status         text        NOT NULL
        CHECK (status IN ('success', 'up_for_retry', 'failed', 'upstream_failed')),
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz NOT NULL,
    error_class    text,
    error_message  text
);
CREATE INDEX ON ops.task_runs (task_id, logical_date);
CREATE INDEX ON ops.task_runs (started_at);

CREATE TABLE ops.task_logs (
    id       bigserial   PRIMARY KEY,
    run_id   text        NOT NULL,
    ts       timestamptz NOT NULL,
    level    text        NOT NULL,
    message  text        NOT NULL,
    attrs    jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX ON ops.task_logs (run_id, ts);
