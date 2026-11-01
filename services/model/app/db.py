import os
from typing import Callable

from psycopg import Connection
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

-- Every save of the SysML v2 text is a version of the model, stored as a commit
-- of the project (commit_id). This row keeps the text and its interpretation for the UI.
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

-- ---- SysML v2 API (Systems Modeling API and Services 1.0) repository
ALTER TABLE models.project ADD COLUMN IF NOT EXISTS alias TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE models.project ADD COLUMN IF NOT EXISTS default_branch_id UUID;

CREATE TABLE IF NOT EXISTS models.commits (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES models.project(id) ON DELETE CASCADE,
    seq                 BIGSERIAL,
    name                TEXT,
    description         TEXT,
    alias               TEXT[] NOT NULL DEFAULT '{}',
    previous_commit_ids UUID[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS commits_project_idx ON models.commits (project_id, seq);

CREATE TABLE IF NOT EXISTS models.branches (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES models.project(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    description    TEXT,
    alias          TEXT[] NOT NULL DEFAULT '{}',
    head_commit_id UUID REFERENCES models.commits(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS models.tags (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES models.project(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    description      TEXT,
    alias            TEXT[] NOT NULL DEFAULT '{}',
    tagged_commit_id UUID NOT NULL REFERENCES models.commits(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

-- The changes of a commit: one DataVersion per created, updated or deleted (null payload)
-- element. model_id: the Studio model the element belongs to (not part of the API).
CREATE TABLE IF NOT EXISTS models.data_versions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commit_id  UUID NOT NULL REFERENCES models.commits(id) ON DELETE CASCADE,
    element_id UUID NOT NULL,
    payload    JSONB,
    model_id   UUID,
    UNIQUE (commit_id, element_id)
);
CREATE INDEX IF NOT EXISTS data_versions_payload_idx ON models.data_versions USING GIN (payload jsonb_path_ops);

-- Every element a commit holds (element id -> the DataVersion it has at that commit)
CREATE TABLE IF NOT EXISTS models.commit_elements (
    commit_id  UUID NOT NULL REFERENCES models.commits(id) ON DELETE CASCADE,
    element_id UUID NOT NULL,
    version_id UUID NOT NULL REFERENCES models.data_versions(id) ON DELETE CASCADE,
    PRIMARY KEY (commit_id, element_id)
);
CREATE INDEX IF NOT EXISTS commit_elements_version_idx ON models.commit_elements (version_id);

CREATE TABLE IF NOT EXISTS models.queries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES models.project(id) ON DELETE CASCADE,
    name        TEXT NOT NULL DEFAULT '',
    description TEXT,
    alias       TEXT[] NOT NULL DEFAULT '{}',
    query       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE models.model_version ADD COLUMN IF NOT EXISTS commit_id UUID REFERENCES models.commits(id) ON DELETE SET NULL;

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

-- Demo projects already seeded once (so a deleted demo is not re-created)
CREATE TABLE IF NOT EXISTS models.seed (
    name       TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row, "autocommit": True}, open=False, min_size=1, max_size=10)


def init(migrate: Callable[[Connection], None] | None = None) -> None:
    """Create or upgrade the schema, then run ``migrate`` in one transaction (once
    at a time across workers)."""
    pool.open(wait=True, timeout=60)
    with pool.connection() as c:
        c.execute("SELECT pg_advisory_lock(4202)")
        try:
            c.execute(SCHEMA)
            if migrate:
                with c.transaction():
                    migrate(c)
        finally:
            c.execute("SELECT pg_advisory_unlock(4202)")
