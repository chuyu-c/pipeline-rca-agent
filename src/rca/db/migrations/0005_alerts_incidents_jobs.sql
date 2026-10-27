-- Alerting, incidents and the work queue (DESIGN §4, §5.1). The worker that drains ops.jobs is Phase 8.

CREATE TABLE ops.incidents (
    incident_id     bigserial   PRIMARY KEY,
    status          text        NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'triaging', 'resolved')),
    root_candidate  text,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    rca             jsonb,
    trace           jsonb,
    approved_by     text,
    feedback        jsonb
);
CREATE INDEX ON ops.incidents (status, created_at);

CREATE TABLE ops.alerts (
    alert_id      bigserial   PRIMARY KEY,
    fingerprint   text        NOT NULL,
    detector      text        NOT NULL,
    dataset       text        NOT NULL,
    error_class   text        NOT NULL,
    severity      text        NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    logical_date  date        NOT NULL,
    payload       jsonb       NOT NULL DEFAULT '{}',
    first_seen    timestamptz NOT NULL,
    last_seen     timestamptz NOT NULL,
    count         integer     NOT NULL DEFAULT 1,
    incident_id   bigint      NOT NULL REFERENCES ops.incidents
);
CREATE INDEX ON ops.alerts (fingerprint, last_seen);
CREATE INDEX ON ops.alerts (incident_id);

CREATE TABLE ops.jobs (
    job_id        bigserial   PRIMARY KEY,
    kind          text        NOT NULL,
    incident_id   bigint      NOT NULL REFERENCES ops.incidents,
    payload       jsonb       NOT NULL DEFAULT '{}',
    status        text        NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'dead')),
    attempts      integer     NOT NULL DEFAULT 0,
    run_after     timestamptz NOT NULL,
    locked_until  timestamptz,
    last_error    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- Idempotent enqueue: one job of each kind per incident.
    UNIQUE (incident_id, kind)
);
CREATE INDEX ON ops.jobs (status, run_after);
