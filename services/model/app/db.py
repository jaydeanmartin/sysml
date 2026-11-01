"""PostgreSQL holds what is not model data: runs, which demos were seeded and which
projects were deleted.

Projects, models and their versions live in the SysML v2 API server (see store.py).
The tables of earlier releases (models.project, models.model, models.model_version,
models.element) are left as they were; legacy.py copies them into the API server once.
"""

import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sysml:sysml@postgres:5432/sysml")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS models;

-- Executions of the model (calcs, analyses, requirement checks, behaviors)
CREATE TABLE IF NOT EXISTS models.run (
    id          UUID PRIMARY KEY,
    model_id    UUID NOT NULL,
    version     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT,
    request     JSONB NOT NULL,
    status      TEXT NOT NULL,
    result      JSONB,
    elapsed_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- models are no longer rows here (earlier releases referenced models.model)
ALTER TABLE models.run DROP CONSTRAINT IF EXISTS run_model_id_fkey;
CREATE INDEX IF NOT EXISTS run_model_idx ON models.run (model_id, created_at DESC);

-- Projects deleted in the studio. The pilot SysML v2 API server cannot delete a project
-- that has commits, so their models are removed by a commit and the project is hidden.
CREATE TABLE IF NOT EXISTS models.deleted_project (
    id         UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Demo projects already seeded once (so a deleted demo is not re-created), and
-- legacy projects already copied into the SysML v2 API server ('migrated:<id>')
CREATE TABLE IF NOT EXISTS models.seed (
    name       TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
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


def deleted_projects() -> set[str]:
    with pool.connection() as c:
        return {str(r["id"]) for r in c.execute("SELECT id FROM models.deleted_project")}


def mark_deleted(project_id: str) -> None:
    with pool.connection() as c:
        c.execute("INSERT INTO models.deleted_project (id) VALUES (%s) ON CONFLICT DO NOTHING", (project_id,))
