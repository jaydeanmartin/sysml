"""One-time copy of the projects that earlier releases stored in PostgreSQL into the
SysML v2 API server, with their full history.

Every saved version becomes a commit (in the order they were saved, across the models of
a project) carrying the elements stored for it then. Model ids are kept, so recorded
runs stay attached. The old tables are left untouched; drop them once you no longer
need them (DROP TABLE models.element, models.model_version, models.model, models.project).
"""

from __future__ import annotations

import logging
from typing import Callable

from . import db, store

log = logging.getLogger("model.legacy")
DONE = "sysml-api-migration"


def migrate(write: Callable[..., None], remove_project: Callable[[str], None]) -> None:
    with db.pool.connection() as c:
        if c.execute("SELECT to_regclass('models.model_version') AS t").fetchone()["t"] is None:
            return  # nothing from an earlier release
        if c.execute("SELECT 1 FROM models.seed WHERE name=%s", (DONE,)).fetchone():
            return
        projects = c.execute("SELECT * FROM models.project ORDER BY created_at").fetchall()
        if projects and not c.execute("SELECT 1 FROM models.seed LIMIT 1").fetchone():
            # a database from before seeds were recorded: it already had its chance at the first demo
            c.execute("INSERT INTO models.seed (name) VALUES ('demo') ON CONFLICT DO NOTHING")
    deleted = db.deleted_projects()
    existing = {p.get("name") for p in store.projects() if p["@id"] not in deleted}
    for p in projects:
        marker = f"migrated:{p['id']}"
        with db.pool.connection() as c:
            if c.execute("SELECT 1 FROM models.seed WHERE name=%s", (marker,)).fetchone():
                continue
            versions = c.execute(
                """SELECT v.id, v.source, v.message, v.valid, v.created_at, m.id AS model_id, m.name, m.description
                   FROM models.model_version v JOIN models.model m ON m.id=v.model_id
                   WHERE m.project_id=%s ORDER BY v.created_at, v.id""",
                (p["id"],),
            ).fetchall()
        with db.pool.connection() as c:
            partial = c.execute("SELECT name FROM models.seed WHERE name LIKE %s", (f"migrating:{p['id']}:%",)).fetchall()
        for row in partial:  # an earlier attempt stopped half way: start over
            api_id = row["name"].rsplit(":", 1)[1]
            remove_project(api_id)
            existing.discard(p["name"])
            with db.pool.connection() as c:
                c.execute("DELETE FROM models.seed WHERE name=%s", (row["name"],))
        if p["name"] in existing:
            log.warning("not migrating project %s: the SysML v2 API server already has a project of that name", p["name"])
        else:
            pid = store.create_project(p["name"], p["description"])["@id"]
            with db.pool.connection() as c:
                c.execute("INSERT INTO models.seed (name) VALUES (%s)", (f"migrating:{p['id']}:{pid}",))
            for v in versions:
                with db.pool.connection() as c:
                    elements = [r["data"] for r in c.execute("SELECT data FROM models.element WHERE version_id=%s", (v["id"],))]
                write(
                    pid, str(v["model_id"]), v["name"], v["description"], v["source"], elements,
                    v["message"], v["valid"], v["created_at"].isoformat(),
                )
            log.info("migrated project %s (%d versions) into the SysML v2 API server", p["name"], len(versions))
        with db.pool.connection() as c:
            c.execute("DELETE FROM models.seed WHERE name LIKE %s", (f"migrating:{p['id']}:%",))
            c.execute("INSERT INTO models.seed (name) VALUES (%s) ON CONFLICT DO NOTHING", (marker,))
    with db.pool.connection() as c:
        c.execute("INSERT INTO models.seed (name) VALUES (%s) ON CONFLICT DO NOTHING", (DONE,))
