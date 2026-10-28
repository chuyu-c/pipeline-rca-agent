-- Deploy history (DESIGN §4) and the simulator's fault-injection control table (rca.faults).

CREATE TABLE ops.deploys (
    deploy_id   text        PRIMARY KEY,
    ts          timestamptz NOT NULL,
    author      text        NOT NULL,
    task_id     text        NOT NULL,
    commit_sha  text        NOT NULL,
    message     text        NOT NULL,
    diff        text        NOT NULL
);
CREATE INDEX ON ops.deploys (task_id, ts);

-- `sim` holds simulator-internal state: how the fake sources and tasks should misbehave.
-- Only the simulator (rca_app) reads it; the agent's rca_readonly role must never see it, or
-- eval scenarios would leak their own answers.
CREATE SCHEMA IF NOT EXISTS sim;

CREATE TABLE sim.faults (
    target      text        PRIMARY KEY,  -- 'source:<name>' or 'task:<task_id>'
    params      jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
GRANT USAGE ON SCHEMA sim TO rca_app;
GRANT SELECT ON sim.faults TO rca_app;

-- Reset cancels triage jobs of incidents it resolves.
ALTER TABLE ops.jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE ops.jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('queued', 'running', 'done', 'dead', 'cancelled'));
