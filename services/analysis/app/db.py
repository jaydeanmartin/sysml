import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sysml:sysml@postgres:5432/sysml")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS analysis;

CREATE TABLE IF NOT EXISTS analysis.analysis (
    name          TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('builtin', 'external', 'formula')),
    description   TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT '1',
    endpoint_url  TEXT,
    headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
    formulas      JSONB NOT NULL DEFAULT '{}'::jsonb,
    inputs        JSONB NOT NULL DEFAULT '[]'::jsonb,
    outputs       JSONB NOT NULL DEFAULT '[]'::jsonb,
    timeout_s     REAL NOT NULL DEFAULT 30,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analysis.invocation (
    id            BIGSERIAL PRIMARY KEY,
    analysis_name TEXT NOT NULL,
    run_id        TEXT,
    uri           TEXT,
    request       JSONB NOT NULL,
    response      JSONB,
    status        TEXT NOT NULL,
    error         TEXT,
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS invocation_run_idx ON analysis.invocation (run_id);
CREATE INDEX IF NOT EXISTS invocation_name_idx ON analysis.invocation (analysis_name, created_at DESC);
"""

pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row, "autocommit": True}, open=False, min_size=1, max_size=10)


def init() -> None:
    pool.open(wait=True, timeout=60)
    with pool.connection() as c:
        c.execute("SELECT pg_advisory_lock(4201)")
        try:
            c.execute(SCHEMA)
        finally:
            c.execute("SELECT pg_advisory_unlock(4201)")
