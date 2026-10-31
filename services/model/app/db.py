import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sysml:sysml@postgres:5432/sysml")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS models;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS models.project (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS models.model (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES models.project(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    head_version INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

-- Every save of the SysML v2 text is an immutable version (the commit history)
CREATE TABLE IF NOT EXISTS models.model_version (
    id            BIGSERIAL PRIMARY KEY,
    model_id      UUID NOT NULL REFERENCES models.model(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    source        TEXT NOT NULL,
    message       TEXT NOT NULL DEFAULT '',
    valid         BOOLEAN NOT NULL,
    diagnostics   JSONB NOT NULL DEFAULT '[]'::jsonb,
    graph         JSONB NOT NULL DEFAULT '{"nodes": [], "edges": []}'::jsonb,
    summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    element_count INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, version)
);

-- The interpreted model, as SysML v2 API JSON elements (one row per element)
CREATE TABLE IF NOT EXISTS models.element (
    version_id     BIGINT NOT NULL REFERENCES models.model_version(id) ON DELETE CASCADE,
    element_id     TEXT NOT NULL,
    metatype       TEXT NOT NULL,
    name           TEXT,
    qualified_name TEXT,
    owner_id       TEXT,
    data           JSONB NOT NULL,
    PRIMARY KEY (version_id, element_id)
);
CREATE INDEX IF NOT EXISTS element_type_idx ON models.element (version_id, metatype);
CREATE INDEX IF NOT EXISTS element_qn_idx ON models.element (qualified_name);
CREATE INDEX IF NOT EXISTS element_data_idx ON models.element USING GIN (data jsonb_path_ops);

-- Executions of the model (calcs, analyses, requirement checks, behaviors)
CREATE TABLE IF NOT EXISTS models.run (
    id          UUID PRIMARY KEY,
    model_id    UUID NOT NULL REFERENCES models.model(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT,
    request     JSONB NOT NULL,
    status      TEXT NOT NULL,
    result      JSONB,
    elapsed_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_model_idx ON models.run (model_id, created_at DESC);
"""

pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row, "autocommit": True}, open=False, min_size=1, max_size=10)


def init() -> None:
    pool.open(wait=True, timeout=60)
    with pool.connection() as c:
        c.execute("SELECT pg_advisory_lock(4202)")
        try:
            c.execute(SCHEMA)
        finally:
            c.execute("SELECT pg_advisory_unlock(4202)")
