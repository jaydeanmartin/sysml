"""Model service: projects, versioned SysML v2 models, interpreted elements and runs.

The SysML v2 text is the source of truth. Each save creates an immutable
version; the interpreter service validates it and returns the model as SysML v2
API JSON elements (stored one row per element, queryable with JSONB) and a
diagram graph. Executions are proxied to the interpreter and recorded.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model")

INTERPRETER_URL = os.environ.get("INTERPRETER_SERVICE_URL", "http://interpreter:8002")
ANALYSIS_URL = os.environ.get("ANALYSIS_SERVICE_URL", "http://analysis:8003")
EXAMPLES_DIR = Path(os.environ.get("EXAMPLES_DIR", "/app/examples"))
SEED_DEMO = os.environ.get("SEED_DEMO", "true").lower() == "true"

EMPTY_TEMPLATE = """package {name} {{
    private import ScalarValues::*;
    private import ISQ::*;

    requirement def <'R1'> TopLevelRequirement {{
        doc /* The system shall ... */
    }}

    part def System {{
    }}
}}
"""


# ------------------------------------------------------------------ schemas
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_][\w\- .]*$")
    description: str = ""
    source: str | None = None
    template: Literal["empty", "vehicle"] = "empty"


class SourceIn(BaseModel):
    source: str
    message: str = ""


class RunIn(BaseModel):
    kind: Literal["calc", "analysis", "requirement", "constraint", "satisfy", "action", "state", "evaluate", "validate"]
    target: str | None = None
    args: list[str] = []
    subject: str | None = None
    expression: str | None = None
    instantiate: list[str] = []
    advance: float | None = None
    seed: int | None = None
    trace: bool = True


# ------------------------------------------------------------------ helpers
def _interpreter(path: str, body: dict[str, Any], timeout: float = 180) -> dict[str, Any]:
    try:
        r = httpx.post(f"{INTERPRETER_URL}{path}", json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"interpreter service unavailable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    return r.json()


def _model_row(model_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(model_id)
    except ValueError as exc:
        raise HTTPException(404, "model not found") from exc
    with db.pool.connection() as c:
        row = c.execute("SELECT * FROM models.model WHERE id=%s", (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, "model not found")
    return row


def _head(model_id: str, version: int | None = None) -> dict[str, Any] | None:
    with db.pool.connection() as c:
        if version is None:
            return c.execute(
                """SELECT v.* FROM models.model_version v JOIN models.model m ON m.id=v.model_id
                   WHERE v.model_id=%s AND v.version=m.head_version""",
                (model_id,),
            ).fetchone()
        return c.execute(
            "SELECT * FROM models.model_version WHERE model_id=%s AND version=%s", (model_id, version)
        ).fetchone()


def _context_files(model: dict[str, Any], source: str) -> list[dict[str, str]]:
    """The model's text plus every other model of the project (head versions),
    so packages in one model can import packages from another."""
    files = [{"name": f"{model['name']}.sysml", "text": source}]
    with db.pool.connection() as c:
        rows = c.execute(
            """SELECT m.name, v.source FROM models.model m
               JOIN models.model_version v ON v.model_id=m.id AND v.version=m.head_version
               WHERE m.project_id=%s AND m.id<>%s ORDER BY m.name""",
            (model["project_id"], model["id"]),
        ).fetchall()
    files += [{"name": f"{r['name']}.sysml", "text": r["source"]} for r in rows]
    return files


def _model_api(m: dict[str, Any], v: dict[str, Any] | None, full: bool = True) -> dict[str, Any]:
    out = {
        "id": str(m["id"]),
        "projectId": str(m["project_id"]),
        "name": m["name"],
        "description": m["description"],
        "headVersion": m["head_version"],
        "createdAt": m["created_at"].isoformat(),
        "updatedAt": m["updated_at"].isoformat(),
    }
    if v:
        out["valid"] = v["valid"]
        out["summary"] = v["summary"]
        if full:
            out.update(
                source=v["source"],
                diagnostics=v["diagnostics"],
                graph=v["graph"],
                elementCount=v["element_count"],
                versionMessage=v["message"],
            )
    return out


def _interpret(model: dict[str, Any], source: str, include_elements: bool) -> dict[str, Any]:
    """Parse the model in its project context and keep only what this model's
    own file declares (other models are context for imports only)."""
    files = _context_files(model, source)
    parsed = _interpreter("/parse", {"files": files, "includeElements": include_elements})
    own = files[0]["name"]
    graph = parsed["graph"]
    own_nodes = [n for n in graph["nodes"] if (n.get("loc") or {}).get("file") in (own, None)]
    ids = {n["id"] for n in own_nodes}

    def keep_end(x: str) -> bool:
        return x in ids or x.split("::__")[0] in ids

    graph = {"nodes": own_nodes, "edges": [e for e in graph["edges"] if keep_end(e["source"]) and keep_end(e["target"])]}
    roots = [n["id"] for n in own_nodes if n["owner"] is None]
    elements = [
        e for e in (parsed.get("elements") or [])
        if any(e.get("@id", "") == r or e.get("@id", "").startswith(r + "_") for r in roots)
    ]
    diagnostics = [d for d in parsed["diagnostics"] if d.get("file") in (own, None)]
    diagnostics += [
        {**d, "message": f"{d.get('file')}: {d['message']}", "line": 1, "column": 1, "file": own}
        for d in parsed["diagnostics"]
        if d.get("file") not in (own, None) and d["severity"] == "error"
    ]
    if parsed.get("convertError") and not any(d["severity"] == "error" for d in diagnostics):
        diagnostics.append({"severity": "error", "message": parsed["convertError"], "file": own, "line": 1, "column": 1, "code": "convert"})
    summary: dict[str, int] = {}
    for n in own_nodes:
        summary[n["kind"]] = summary.get(n["kind"], 0) + 1
    return {
        "valid": not any(d["severity"] == "error" for d in diagnostics),
        "diagnostics": diagnostics,
        "graph": graph,
        "summary": summary,
        "elements": elements,
        "elapsedMs": parsed.get("elapsedMs"),
    }


def _save_version(model: dict[str, Any], source: str, message: str) -> dict[str, Any]:
    res = _interpret(model, source, include_elements=True)
    diagnostics, graph, elements, valid = res["diagnostics"], res["graph"], res["elements"], res["valid"]
    with db.pool.connection() as c, c.transaction():
        m = c.execute("SELECT head_version FROM models.model WHERE id=%s FOR UPDATE", (model["id"],)).fetchone()
        version = m["head_version"] + 1
        vid = c.execute(
            """INSERT INTO models.model_version
               (model_id, version, source, message, valid, diagnostics, graph, summary, element_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                model["id"], version, source, message, valid, Jsonb(diagnostics), Jsonb(graph),
                Jsonb(res["summary"]), len(elements),
            ),
        ).fetchone()["id"]
        if elements:
            with c.cursor().copy(
                "COPY models.element (version_id, element_id, metatype, name, qualified_name, owner_id, data) FROM STDIN"
            ) as cp:
                for e in elements:
                    if "@id" not in e:
                        continue
                    owner = e.get("owner")
                    cp.write_row(
                        (
                            vid,
                            e["@id"],
                            e.get("@type", "Element"),
                            e.get("declaredName") or e.get("name"),
                            e.get("qualifiedName"),
                            owner.get("@id") if isinstance(owner, dict) else None,
                            json.dumps(e),
                        )
                    )
        c.execute("UPDATE models.model SET head_version=%s, updated_at=now() WHERE id=%s", (version, model["id"]))
    return _head(str(model["id"]))


# ------------------------------------------------------------------ app
def _seed_demo() -> None:
    example = EXAMPLES_DIR / "vehicle.sysml"
    with db.pool.connection() as c:
        if c.execute("SELECT 1 FROM models.project LIMIT 1").fetchone() or not example.exists():
            return
        pid = c.execute(
            "INSERT INTO models.project (name, description) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            ("Demo", "Example vehicle model calling REST analyses during execution"),
        ).fetchone()
        if not pid:
            return
        m = c.execute(
            "INSERT INTO models.model (project_id, name, description) VALUES (%s,%s,%s) RETURNING *",
            (pid["id"], "Vehicle", "Vehicle architecture, requirements, behavior and analyses"),
        ).fetchone()
    for attempt in range(60):  # the interpreter may still be starting
        try:
            _save_version(m, example.read_text(), "Seed example")
            log.info("seeded demo project")
            return
        except HTTPException as exc:
            log.info("waiting for interpreter to seed demo (%s)", exc.detail)
            time.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    if SEED_DEMO:
        threading.Thread(target=_seed_demo, daemon=True).start()
    yield
    db.pool.close()


app = FastAPI(
    title="SysML v2 Model Service",
    version="0.1.0",
    description="Projects, versioned SysML v2 models, interpreted elements (SysML v2 API JSON) and runs.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> dict[str, str]:
    with db.pool.connection() as c:
        c.execute("SELECT 1")
    return {"status": "ok"}


# ------------------------------------------------------------------ projects
@app.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        rows = c.execute(
            """SELECT p.*, count(m.id) AS model_count FROM models.project p
               LEFT JOIN models.model m ON m.project_id=p.id GROUP BY p.id ORDER BY p.created_at"""
        ).fetchall()
    return [
        {"id": str(r["id"]), "name": r["name"], "description": r["description"], "modelCount": r["model_count"],
         "createdAt": r["created_at"].isoformat()}
        for r in rows
    ]


@app.post("/projects", status_code=201)
def create_project(p: ProjectIn) -> dict[str, Any]:
    with db.pool.connection() as c:
        r = c.execute(
            "INSERT INTO models.project (name, description) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING RETURNING *",
            (p.name, p.description),
        ).fetchone()
    if not r:
        raise HTTPException(409, f"project '{p.name}' already exists")
    return {"id": str(r["id"]), "name": r["name"], "description": r["description"], "modelCount": 0,
            "createdAt": r["created_at"].isoformat()}


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID) -> None:
    with db.pool.connection() as c:
        c.execute("DELETE FROM models.project WHERE id=%s", (project_id,))


@app.get("/projects/{project_id}/models")
def list_models(project_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        rows = c.execute(
            """SELECT m.*, v.valid, v.summary FROM models.model m
               LEFT JOIN models.model_version v ON v.model_id=m.id AND v.version=m.head_version
               WHERE m.project_id=%s ORDER BY m.name""",
            (project_id,),
        ).fetchall()
    return [_model_api(r, r if r["valid"] is not None else None, full=False) for r in rows]


@app.post("/projects/{project_id}/models", status_code=201)
def create_model(project_id: uuid.UUID, body: ModelIn) -> dict[str, Any]:
    pkg = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in body.name)
    source = body.source
    if source is None:
        example = EXAMPLES_DIR / "vehicle.sysml"
        if body.template == "vehicle" and example.exists():
            # rename the package so it does not clash with other models of the project
            source = example.read_text().replace("package VehicleModel {", f"package {pkg} {{", 1)
        else:
            source = EMPTY_TEMPLATE.format(name=pkg)
    with db.pool.connection() as c:
        if not c.execute("SELECT 1 FROM models.project WHERE id=%s", (project_id,)).fetchone():
            raise HTTPException(404, "project not found")
        m = c.execute(
            """INSERT INTO models.model (project_id, name, description) VALUES (%s,%s,%s)
               ON CONFLICT (project_id, name) DO NOTHING RETURNING *""",
            (project_id, body.name, body.description),
        ).fetchone()
    if not m:
        raise HTTPException(409, f"model '{body.name}' already exists in this project")
    v = _save_version(m, source, "Created")
    return _model_api(_model_row(str(m["id"])), v)


# ------------------------------------------------------------------ models
@app.get("/models/{model_id}")
def get_model(model_id: str, version: int | None = None) -> dict[str, Any]:
    m = _model_row(model_id)
    return _model_api(m, _head(model_id, version))


@app.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str) -> None:
    _model_row(model_id)
    with db.pool.connection() as c:
        c.execute("DELETE FROM models.model WHERE id=%s", (model_id,))


@app.put("/models/{model_id}/source")
def save_source(model_id: str, body: SourceIn) -> dict[str, Any]:
    """Save a new version of the SysML v2 text (interpreted and stored)."""
    m = _model_row(model_id)
    head = _head(model_id)
    if head and head["source"] == body.source:
        return _model_api(m, head)
    v = _save_version(m, body.source, body.message)
    return _model_api(_model_row(model_id), v)


@app.post("/models/{model_id}/preview")
def preview(model_id: str, body: SourceIn) -> dict[str, Any]:
    """Interpret unsaved text (live editing): diagnostics + diagram graph, nothing stored."""
    m = _model_row(model_id)
    res = _interpret(m, body.source, include_elements=False)
    res.pop("elements", None)
    return res


@app.get("/models/{model_id}/versions")
def list_versions(model_id: str) -> list[dict[str, Any]]:
    _model_row(model_id)
    with db.pool.connection() as c:
        rows = c.execute(
            """SELECT version, message, valid, element_count, summary, created_at, length(source) AS size
               FROM models.model_version WHERE model_id=%s ORDER BY version DESC""",
            (model_id,),
        ).fetchall()
    return [
        {"version": r["version"], "message": r["message"], "valid": r["valid"], "elementCount": r["element_count"],
         "summary": r["summary"], "size": r["size"], "createdAt": r["created_at"].isoformat()}
        for r in rows
    ]


@app.get("/models/{model_id}/elements")
def list_elements(
    model_id: str,
    metatype: str | None = None,
    q: str | None = Query(None, description="substring of the qualified name"),
    version: int | None = None,
    limit: int = Query(500, le=5000),
) -> list[dict[str, Any]]:
    """Interpreted elements (SysML v2 API JSON) of a version, default head."""
    _model_row(model_id)
    v = _head(model_id, version)
    if not v:
        return []
    sql = "SELECT data FROM models.element WHERE version_id=%s"
    p: list[Any] = [v["id"]]
    if metatype:
        sql += " AND metatype=%s"
        p.append(metatype)
    if q:
        sql += " AND qualified_name ILIKE %s"
        p.append(f"%{q}%")
    sql += " ORDER BY qualified_name NULLS LAST LIMIT %s"
    p.append(limit)
    with db.pool.connection() as c:
        return [r["data"] for r in c.execute(sql, p).fetchall()]


@app.get("/models/{model_id}/requirements")
def requirements(model_id: str) -> list[dict[str, Any]]:
    """Requirements with their text, constraints, subject, satisfying elements and
    the latest verdict from a satisfy / requirement run."""
    _model_row(model_id)
    v = _head(model_id)
    if not v:
        return []
    graph = v["graph"]
    nodes = {n["id"]: n for n in graph["nodes"]}
    satisfied_by: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["kind"] in ("satisfy", "verify"):
            src = nodes.get(e["source"])
            if src:
                satisfied_by.setdefault(e["target"], []).append(src["qualifiedName"] or src["name"])
    verdicts: dict[str, dict[str, Any]] = {}
    with db.pool.connection() as c:
        runs = c.execute(
            """SELECT result, created_at FROM models.run WHERE model_id=%s AND kind IN ('satisfy','requirement')
               AND result IS NOT NULL ORDER BY created_at DESC LIMIT 50""",
            (model_id,),
        ).fetchall()
    for r in reversed(runs):  # newest wins
        for chk in r["result"].get("checks") or []:
            verdicts[chk.get("subject") or ""] = {"status": chk.get("status"), "at": r["created_at"].isoformat()}
    out = []
    req_kinds = ("requirement def", "requirement", "concern def", "concern")
    for n in graph["nodes"]:
        if n["kind"] not in req_kinds:
            continue
        owner = nodes.get(n["owner"]) if n["owner"] else None
        if owner and owner["kind"] != "package" and owner["kind"] not in req_kinds:
            continue  # objectives of analysis cases etc. are not captured requirements
        subject = next((c for c in graph["nodes"] if c["owner"] == n["id"] and c["kind"] == "subject"), None)
        verdict = None
        for key, val in verdicts.items():
            if key == n["qualifiedName"] or key.startswith(f"satisfy {n['name']} by"):
                verdict = val
        out.append(
            {
                "id": n["id"],
                "kind": n["kind"],
                "name": n["name"],
                "qualifiedName": n["qualifiedName"],
                "doc": n.get("doc"),
                "constraints": n.get("constraints") or [],
                "typeName": n.get("typeName"),
                "subject": subject and {"name": subject["name"], "type": subject.get("typeName"), "value": subject.get("value")},
                "satisfiedBy": satisfied_by.get(n["id"], []),
                "verdict": verdict,
                "loc": n.get("loc"),
            }
        )
    return out


# ------------------------------------------------------------------ runs
def _run_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "modelId": str(r["model_id"]),
        "version": r["version"],
        "kind": r["kind"],
        "target": r["target"],
        "request": r["request"],
        "status": r["status"],
        "result": r["result"],
        "elapsedMs": r["elapsed_ms"],
        "createdAt": r["created_at"].isoformat(),
    }


@app.post("/models/{model_id}/runs", status_code=201)
def create_run(model_id: str, body: RunIn) -> dict[str, Any]:
    """Execute the head version and record the run.

    Calcs and actions carrying ``ToolExecution`` metadata call the analysis
    service over REST while this runs; those calls are tagged with the run id.
    """
    m = _model_row(model_id)
    head = _head(model_id)
    if not head:
        raise HTTPException(409, "model has no version yet")
    run_id = str(uuid.uuid4())
    files = _context_files(m, head["source"])
    result = _interpreter("/execute", {"files": files, **body.model_dump(), "runId": run_id})
    with db.pool.connection() as c:
        r = c.execute(
            """INSERT INTO models.run (id, model_id, version, kind, target, request, status, result, elapsed_ms)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (run_id, model_id, head["version"], body.kind, body.target or body.expression, Jsonb(body.model_dump()),
             result.get("status", "unknown"), Jsonb(result), result.get("elapsedMs")),
        ).fetchone()
    out = _run_api(r)
    out["invocations"] = _invocations(run_id)
    return out


def _invocations(run_id: str) -> list[dict[str, Any]]:
    try:
        r = httpx.get(f"{ANALYSIS_URL}/invocations", params={"runId": run_id}, timeout=5)
        return list(reversed(r.json())) if r.status_code == 200 else []
    except httpx.HTTPError:
        return []


@app.get("/models/{model_id}/runs")
def list_runs(model_id: str, limit: int = Query(50, le=500)) -> list[dict[str, Any]]:
    _model_row(model_id)
    with db.pool.connection() as c:
        rows = c.execute(
            "SELECT * FROM models.run WHERE model_id=%s ORDER BY created_at DESC LIMIT %s", (model_id, limit)
        ).fetchall()
    return [_run_api(r) for r in rows]


@app.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID) -> dict[str, Any]:
    with db.pool.connection() as c:
        r = c.execute("SELECT * FROM models.run WHERE id=%s", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "run not found")
    out = _run_api(r)
    out["invocations"] = _invocations(str(run_id))
    return out
