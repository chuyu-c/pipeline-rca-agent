-- Schemas, extensions and roles (DESIGN §4).
-- Roles are cluster-wide, so creation is idempotent; grants are per-database.
-- Passwords are local dev placeholders matching .env.example.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS lineage;
CREATE SCHEMA IF NOT EXISTS kb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rca_app') THEN
        CREATE ROLE rca_app LOGIN PASSWORD 'rca_app';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rca_readonly') THEN
        CREATE ROLE rca_readonly LOGIN PASSWORD 'rca_readonly';
    END IF;
END
$$;

-- Second layer behind the SQL guard (DESIGN §8): agent queries can't run long.
ALTER ROLE rca_readonly SET statement_timeout = '5s';

-- rca_app: read/write data in every application schema, no DDL.
GRANT USAGE ON SCHEMA raw, stg, mart, ops, lineage, kb TO rca_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, stg, mart, ops, lineage, kb
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rca_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, stg, mart, ops, lineage, kb
    GRANT USAGE, SELECT ON SEQUENCES TO rca_app;

-- rca_readonly: SELECT on the warehouse only. No access to ops/lineage/kb.
GRANT USAGE ON SCHEMA raw, stg, mart TO rca_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, stg, mart
    GRANT SELECT ON TABLES TO rca_readonly;
