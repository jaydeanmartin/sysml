"""Model service: projects, versioned SysML v2 models, interpreted elements and runs.

The SysML v2 text is the source of truth. Each save creates an immutable
version; the interpreter service validates it and returns the model as SysML v2
API JSON elements and a diagram graph. The elements are stored as a commit of
the project in a SysML v2 API repository (``repo``), which this service also
serves through the standard REST API under ``/sysml-v2`` (``sysml_api``).
Executions are proxied to the interpreter and recorded.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import catalog, db, repo, sysml_api

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

LIBRARY_TEMPLATE = """package {name} {{
    private import ScalarValues::*;
    private import ISQ::*;

    // The Library and Configurator tabs look for these two metadata tags.
    metadata def Component {{
        doc /* A reusable component type. Its instances form the component catalog. */
    }}
    metadata def SystemArchitecture {{
        doc /* A system-level part def. Its part usages are slots that component instances fill. */
    }}

    #Component part def ExampleComponent {{
        attribute mass : MassValue;
    }}

    // Component instances (created from the Library tab)
    package Catalog {{
    }}
}}
"""

ARCHITECTURE_TEMPLATE = """package {name} {{
    private import ScalarValues::*;
    private import ISQ::*;
{imports}
    #SystemArchitecture part def System {{
        doc /* One part usage per slot, typed by a component. */
    }}

    // System instances (created from the Configurator tab)
    package Configurations {{
    }}
}}
"""
_TAG_RE = re.compile(r"\bmetadata\s+def\s+SystemArchitecture\b")
_TOP_PKG_RE = re.compile(r"^\s*package\s+([A-Za-z_]\w*)", re.M)


# ------------------------------------------------------------------ schemas
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_][\w\- .]*$")
    description: str = ""
    source: str | None = None
    template: Literal["empty", "library", "architecture"] = "empty"


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
        out["commitId"] = v.get("commit_id") and str(v["commit_id"])
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
    roots = {n["id"] for n in own_nodes if n["owner"] is None}
    elements = repo.own_elements(parsed.get("elements") or [], roots)
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


def _save_version(model: dict[str, Any], source: str, message: str, res: dict[str, Any] | None = None) -> dict[str, Any]:
    """Interpret and store a new version: a commit of the elements that changed, plus
    the version row. An invalid text that yields no elements keeps the previous
    elements (only the stored text changes)."""
    res = res or _interpret(model, source, include_elements=True)
    diagnostics, graph, elements, valid = res["diagnostics"], res["graph"], res["elements"], res["valid"]
    with db.pool.connection() as c, c.transaction():
        m = c.execute("SELECT * FROM models.model WHERE id=%s FOR UPDATE", (model["id"],)).fetchone()
        if not m:
            raise HTTPException(404, "model not found")
        version = m["head_version"] + 1
        cm = repo.save_model(c, m, elements if elements or valid else None, source, message=message, version=version)
        c.execute(
            """INSERT INTO models.model_version
               (model_id, version, source, message, valid, diagnostics, graph, summary, element_count, commit_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                m["id"], version, source, message, valid, Jsonb(diagnostics), Jsonb(graph),
                Jsonb(res["summary"]), len(elements), cm["id"],
            ),
        )
        c.execute("UPDATE models.model SET head_version=%s, updated_at=now() WHERE id=%s", (version, m["id"]))
    return _head(str(model["id"]))


def _sync_text_changes(project_id: Any, commit: dict[str, Any]) -> None:
    """A commit made through the SysML v2 API that changes a model's
    TextualRepresentation becomes a new version of that model (its elements are
    the ones the commit holds)."""
    with db.pool.connection() as c:
        changed = repo.studio_text_changes(c, project_id, commit)
    for m, text in changed:
        try:
            res = _interpret(m, text, include_elements=False)
        except HTTPException as exc:
            log.warning("could not interpret %s from commit %s: %s", m["name"], commit["id"], exc.detail)
            res = {"valid": False, "graph": {"nodes": [], "edges": []}, "summary": {},
                   "diagnostics": [{"severity": "error", "message": f"not interpreted: {exc.detail}", "file": f"{m['name']}.sysml", "line": 1, "column": 1}]}
        with db.pool.connection() as c, c.transaction():
            row = c.execute("SELECT head_version FROM models.model WHERE id=%s FOR UPDATE", (m["id"],)).fetchone()
            if not row:
                continue
            count = len(repo.model_elements(c, commit["id"], m["id"]))
            c.execute(
                """INSERT INTO models.model_version
                   (model_id, version, source, message, valid, diagnostics, graph, summary, element_count, commit_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    m["id"], row["head_version"] + 1, text, commit["description"] or "Commit through the SysML v2 API",
                    res["valid"], Jsonb(res["diagnostics"]), Jsonb(res["graph"]), Jsonb(res["summary"]), count, commit["id"],
                ),
            )
            c.execute("UPDATE models.model SET head_version=head_version+1, updated_at=now() WHERE id=%s", (m["id"],))


# ------------------------------------------------------------------ app
# (seed key, project, description, [(model, example file, description)]); models
# are saved in this order, so a model can import the packages of the ones before it
DEMOS = [
    (
        "delivery-drones", "Delivery Drones", "Configurable multirotor parcel delivery drones",
        [
            ("DroneLibrary", "drone_library.sysml", "Frames, batteries, motors, propellers and payload modules, with their catalog instances"),
            ("DroneArchitectures", "drone_architectures.sysml", "Quad, hexa and octo architectures, sizing requirements and configured drones"),
            ("DroneRequirements", "drone_requirements.sysml", "Requirement types with parameters, and the specification hierarchy of their instances"),
            ("DroneMissions", "drone_missions.sysml", "Behavior library and delivery missions built from ordered behaviors"),
            ("DroneControl", "drone_control.sysml", "Controllability: flight-controller tuning and closed-loop manoeuvre simulation"),
        ],
    ),
]


def _seed_demo() -> None:
    for key, project, description, models in DEMOS:
        if not all((EXAMPLES_DIR / f).exists() for _, f, _ in models):
            continue
        with db.pool.connection() as c, c.transaction():
            if not c.execute("INSERT INTO models.seed (name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING name", (key,)).fetchone():
                continue  # seeded before (and maybe deleted since), or another worker is seeding it
            try:
                pid = repo.create_project(c, project, description)
            except repo.RepoError:
                continue
            rows = [
                (
                    c.execute(
                        "INSERT INTO models.model (project_id, name, description) VALUES (%s,%s,%s) RETURNING *",
                        (pid["id"], name, desc),
                    ).fetchone(),
                    (EXAMPLES_DIR / f).read_text(),
                )
                for name, f, desc in models
            ]
        for m, text in rows:
            for attempt in range(60):  # the interpreter may still be starting
                try:
                    _save_version(m, text, "Seed example")
                    break
                except HTTPException as exc:
                    log.info("waiting for interpreter to seed %s (%s)", project, exc.detail)
                    time.sleep(2)
        log.info("seeded demo project %s", project)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init(repo.migrate)
    if SEED_DEMO:
        threading.Thread(target=_seed_demo, daemon=True).start()
    yield
    db.pool.close()


app = FastAPI(
    title="SysML v2 Model Service",
    version="0.1.0",
    description="Projects, versioned SysML v2 models, interpreted elements and runs. The repository is also "
    "served through the standard SysML v2 API (Systems Modeling API and Services 1.0) under /sysml-v2.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["Link"])
sysml_api.install(app)
sysml_api.on_commit = lambda project_id, commit: _sync_text_changes(project_id, commit)


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
    with db.pool.connection() as c, c.transaction():
        r = repo.create_project(c, p.name, p.description)
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


def _library_imports(project_id: uuid.UUID) -> str:
    """Imports of the project's packages that declare the #SystemArchitecture tag."""
    with db.pool.connection() as c:
        rows = c.execute(
            """SELECT v.source FROM models.model m
               JOIN models.model_version v ON v.model_id=m.id AND v.version=m.head_version
               WHERE m.project_id=%s ORDER BY m.name""",
            (project_id,),
        ).fetchall()
    pkgs = [m.group(1) for r in rows if _TAG_RE.search(r["source"]) for m in [_TOP_PKG_RE.search(r["source"])] if m]
    if not pkgs:
        return "    // No component library in this project yet: create one from the 'Component library' template.\n"
    return "".join(f"    private import {p}::*;\n" for p in dict.fromkeys(pkgs))


@app.post("/projects/{project_id}/models", status_code=201)
def create_model(project_id: uuid.UUID, body: ModelIn) -> dict[str, Any]:
    pkg = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in body.name)
    source = body.source
    if source is None:
        if body.template == "library":
            source = LIBRARY_TEMPLATE.format(name=pkg)
        elif body.template == "architecture":
            imports = _library_imports(project_id)
            source = ARCHITECTURE_TEMPLATE.format(name=pkg, imports=imports)
            if "import" not in imports:  # nothing declares the tag yet
                source = source.replace("#SystemArchitecture part def", "part def")
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
    m = _model_row(model_id)
    with db.pool.connection() as c, c.transaction():
        repo.delete_model(c, m)  # a commit deleting its elements; the history stays in the project
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
            """SELECT version, message, valid, element_count, summary, created_at, length(source) AS size, commit_id
               FROM models.model_version WHERE model_id=%s ORDER BY version DESC""",
            (model_id,),
        ).fetchall()
    return [
        {"version": r["version"], "message": r["message"], "valid": r["valid"], "elementCount": r["element_count"],
         "summary": r["summary"], "size": r["size"], "createdAt": r["created_at"].isoformat(),
         "commitId": r["commit_id"] and str(r["commit_id"])}
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
    """Elements (SysML v2 API JSON) of a version, default head: the model's elements
    in the commit of that version."""
    _model_row(model_id)
    v = _head(model_id, version)
    if not v or not v["commit_id"]:
        return []
    sql = """SELECT dv.payload FROM models.commit_elements ce JOIN models.data_versions dv ON dv.id=ce.version_id
             WHERE ce.commit_id=%s AND dv.model_id=%s"""
    p: list[Any] = [v["commit_id"], model_id]
    if metatype:
        sql += " AND dv.payload->>'@type'=%s"
        p.append(metatype)
    if q:
        sql += " AND dv.payload->>'qualifiedName' ILIKE %s"
        p.append(f"%{q}%")
    sql += " ORDER BY dv.payload->>'qualifiedName' NULLS LAST, ce.element_id LIMIT %s"
    p.append(limit)
    with db.pool.connection() as c:
        return [r["payload"] for r in c.execute(sql, p).fetchall()]


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


# ------------------------------------------------------------------ component library & configurator
class FeatureIn(BaseModel):
    name: str
    type: str = ""


class ComponentIn(BaseModel):
    name: str
    package: str = Field(description="qualified name of the package to declare the component in")
    kind: Literal["part", "action"] = Field("part", description="part: a #Component part def; action: a #Behavior action def")
    specializes: str | None = None
    abstract: bool = False
    doc: str = ""
    attributes: list[FeatureIn] = []
    ports: list[FeatureIn] = []
    params: list[FeatureIn] = Field(default=[], description="a behavior's in parameters, set by the system that performs it")


class InstanceIn(BaseModel):
    component: str = Field(description="qualified name of the component part def")
    name: str
    doc: str = ""
    values: dict[str, str] = Field(default={}, description="attribute or parameter name -> SysML expression, e.g. '140 [SI::kg]'")
    package: str | None = Field(None, description="default: the Catalog (Behaviors) package next to the component (behavior)")
    replace: str | None = Field(None, description="qualified name of an existing instance to rewrite")


class ConfigurationIn(BaseModel):
    architecture: str = Field(description="qualified name of the #SystemArchitecture part def")
    name: str
    doc: str = ""
    assignments: dict[str, str | None] = Field(default={}, description="slot name -> qualified name of a component instance")
    behaviors: dict[str, str | None] = Field(default={}, description="behavior slot -> qualified name of a complete behavior instance; every slot is required")
    package: str | None = Field(None, description="default: the Configurations package next to the architecture")
    replace: str | None = Field(None, description="qualified name of an existing system instance to rewrite")


class StepIn(BaseModel):
    behavior: str = Field(description="qualified name of the #Behavior action def")
    values: dict[str, str] = Field(default={}, description="quantity -> SysML expression; every quantity is required")


class MissionIn(BaseModel):
    missionType: str = Field(description="qualified name of the #Mission action def")
    name: str
    doc: str = ""
    steps: list[StepIn] = Field(default=[], description="the steps, in the order they are performed")
    package: str | None = Field(None, description="default: the Missions package next to the mission type")
    replace: str | None = Field(None, description="qualified name of an existing mission to rewrite")


class MissionCheckIn(BaseModel):
    missionType: str
    draft: list[StepIn] | None = Field(None, description="steps of an unsaved mission to check as well (key 'draft')")


def _project_heads(project_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        if not c.execute("SELECT 1 FROM models.project WHERE id=%s", (project_id,)).fetchone():
            raise HTTPException(404, "project not found")
        return c.execute(
            """SELECT m.id, m.name, v.source FROM models.model m
               JOIN models.model_version v ON v.model_id=m.id AND v.version=m.head_version
               WHERE m.project_id=%s ORDER BY m.name""",
            (project_id,),
        ).fetchall()


def _project_catalog(project_id: uuid.UUID) -> tuple[dict[str, Any], dict[str, str]]:
    """Catalog of a project from one parse of all its models (head versions).
    Also returns {model id -> head source}."""
    rows = _project_heads(project_id)
    sources = {str(r["id"]): r["source"] for r in rows}
    if not rows:
        return catalog.build({"nodes": [], "edges": []}, {}) | {"diagnostics": []}, sources
    files = [{"name": f"{r['name']}.sysml", "text": r["source"]} for r in rows]
    parsed = _interpreter("/parse", {"files": files})
    file_models = {f"{r['name']}.sysml": {"id": str(r["id"]), "name": r["name"]} for r in rows}
    cat = catalog.build(parsed["graph"], file_models, {f"{r['name']}.sysml": r["source"] for r in rows})
    cat["diagnostics"] = [d for d in parsed["diagnostics"] if d["severity"] == "error"]
    return cat, sources


def _find(items: list[dict[str, Any]], qn: str | None, what: str) -> dict[str, Any]:
    item = next((i for i in items if i["qualifiedName"] == qn), None)
    if not item:
        raise HTTPException(404, f"{what} '{qn}' not found")
    return item


def _package(cat: dict[str, Any], qn: str) -> dict[str, Any]:
    pkg = next((p for p in cat["packages"] if p["qualifiedName"] == qn), None)
    if not pkg or not pkg["modelId"] or not pkg["loc"]:
        raise HTTPException(404, f"package '{qn}' not found")
    return pkg


def _default_package(cat: dict[str, Any], near: dict[str, Any], name: str) -> str:
    """A package called ``name`` in the same model (nearest to ``near``), else ``near``'s own package."""
    pkgs = [p["qualifiedName"] for p in cat["packages"] if p["modelId"] == near["modelId"] and p["qualifiedName"].rsplit("::", 1)[-1] == name]
    root = near["qualifiedName"].split("::", 1)[0]
    pkgs.sort(key=lambda q: (not q.startswith(root + "::"), q.count("::")))
    return pkgs[0] if pkgs else near["package"]


def _all_qns(cat: dict[str, Any]) -> set[str]:
    out = {p["qualifiedName"] for p in cat["packages"]}
    for key in ("components", "instances", "architectures", "configurations", "missionTypes", "missions"):
        out |= {i["qualifiedName"] for i in cat[key]}
    return out


def _commit(model_id: str, source: str, message: str) -> dict[str, Any]:
    """Save edited model text, refusing an edit that adds errors (in this model
    or in a model that refers to it)."""
    m = _model_row(model_id)
    head = _head(model_id)
    before = sum(1 for d in (head["diagnostics"] if head else []) if d["severity"] == "error")
    res = _interpret(m, source, include_elements=True)
    errors = [d for d in res["diagnostics"] if d["severity"] == "error"]
    if len(errors) > before:
        detail = "; ".join(f"line {d['line']}: {d['message']}" for d in errors[:3])
        raise HTTPException(422, f"The change would make '{m['name']}' invalid, so it was not saved. {detail}")
    v = _save_version(m, source, message, res)
    return {"modelId": model_id, "modelName": m["name"], "version": v["version"], "valid": v["valid"]}


def _place(cat: dict[str, Any], sources: dict[str, str], name: str, package: str, replace: dict[str, Any] | None):
    """(model id, package qualified name, function block -> new source) for an insert or rewrite."""
    if replace:
        model_id, pkg_qn = replace["modelId"], replace["package"]
        new_qn = f"{pkg_qn}::{name}"
        if new_qn != replace["qualifiedName"] and new_qn in _all_qns(cat):
            raise HTTPException(409, f"'{new_qn}' already exists")
        return model_id, pkg_qn, lambda block: catalog.replace_element(sources[model_id], replace["loc"], block)
    pkg = _package(cat, package)
    if f"{package}::{name}" in _all_qns(cat):
        raise HTTPException(409, f"'{package}::{name}' already exists")
    model_id = pkg["modelId"]
    return model_id, package, lambda block: catalog.insert_member(sources[model_id], pkg["loc"], block)


@app.get("/projects/{project_id}/catalog")
def get_catalog(project_id: uuid.UUID) -> dict[str, Any]:
    """Component types, their instances, system architectures (with slots and the
    instances each slot accepts) and configured system instances of a project."""
    return _project_catalog(project_id)[0]


@app.post("/projects/{project_id}/catalog/components", status_code=201)
def create_component(project_id: uuid.UUID, body: ComponentIn) -> dict[str, Any]:
    """Declare a new ``#Component part def`` (or, with kind ``action``, a ``#Behavior action def``)."""
    cat, sources = _project_catalog(project_id)
    tag_name = catalog.COMPONENT_TAG if body.kind == "part" else catalog.BEHAVIOR_TAG
    tag = cat["tags"].get(tag_name)
    if not tag:
        raise HTTPException(409, f"No 'metadata def {tag_name}' in this project: declare it first")
    if body.kind == "action" and any(f.name.strip() for f in body.ports):
        raise HTTPException(422, "a behavior has no ports")
    if body.kind == "part" and any(f.name.strip() for f in body.params):
        raise HTTPException(422, "a component has no parameters")
    general = next((c for c in cat["components"] if body.specializes and c["qualifiedName"].endswith(body.specializes)), None)
    model_id, pkg_qn, apply = _place(cat, sources, body.name, body.package, None)
    try:
        block = catalog.component_text(
            body.name, catalog.ref(tag, pkg_qn), body.specializes, [f.model_dump() for f in body.attributes],
            [f.model_dump() for f in body.ports], body.doc, body.abstract, body.kind,
            [f.model_dump() for f in body.params], [p["name"] for p in general["params"]] if general else [],
        )
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _commit(model_id, source, f"Add component {body.name}") | {"qualifiedName": f"{pkg_qn}::{body.name}", "text": "\n".join(block)}


@app.post("/projects/{project_id}/catalog/instances", status_code=201)
def save_instance(project_id: uuid.UUID, body: InstanceIn) -> dict[str, Any]:
    """Create (or, with ``replace``, rewrite) a component instance in the catalog."""
    cat, sources = _project_catalog(project_id)
    comp = _find(cat["components"], body.component, "component")
    if comp["isAbstract"] and comp["kind"] == "part":  # abstract behaviors get instances: their parameters set
        raise HTTPException(422, f"'{comp['name']}' is abstract: instantiate one of its specializations")
    params = frozenset(p["name"] for p in comp["params"])
    unknown = set(body.values) - {a["name"] for a in comp["attributes"]} - params
    if unknown:
        raise HTTPException(422, f"{comp['name']} has no attribute {', '.join(sorted(unknown))}")
    replace = _find(cat["instances"], body.replace, "instance") if body.replace else None
    package = body.package or _default_package(cat, comp, "Catalog" if comp["kind"] == "part" else "Behaviors")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, package, replace)
    try:
        block = catalog.instance_text(body.name, catalog.ref(comp["qualifiedName"], pkg_qn), body.values, body.doc, comp["kind"], params)
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    verb = "Update" if replace else "Add"
    return _commit(model_id, source, f"{verb} instance {body.name} : {comp['name']}") | {
        "qualifiedName": f"{pkg_qn}::{body.name}", "text": "\n".join(block),
    }


@app.post("/projects/{project_id}/catalog/configurations", status_code=201)
def save_configuration(project_id: uuid.UUID, body: ConfigurationIn) -> dict[str, Any]:
    """Create (or rewrite) a system instance: an architecture whose slots are
    filled with component instances from the catalog."""
    cat, sources = _project_catalog(project_id)
    arch = _find(cat["architectures"], body.architecture, "architecture")
    slots = {s["name"]: s for s in arch["slots"]}
    inst_by_qn = {i["qualifiedName"]: i for i in cat["instances"]}
    for slot, inst_qn in body.assignments.items():
        if slot not in slots:
            raise HTTPException(422, f"{arch['name']} has no slot '{slot}'")
        if not inst_qn:
            continue
        inst = inst_by_qn.get(inst_qn)
        if not inst:
            raise HTTPException(404, f"instance '{inst_qn}' not found")
        if inst["id"] not in slots[slot]["candidates"]:
            raise HTTPException(422, f"'{inst['name']}' is a {inst['component']}, which does not fit slot {slot} : {slots[slot]['type']}")
    performs = {b["name"]: b for b in arch["behaviors"]}
    for slot in set(body.behaviors) - set(performs):
        raise HTTPException(422, f"{arch['name']} performs no '{slot}'")
    unset = [b for b in performs if not body.behaviors.get(b)]
    if unset:
        raise HTTPException(422, f"A {arch['name']} must perform {', '.join(unset)}: choose a behavior instance for each")
    for slot, inst_qn in body.behaviors.items():
        inst = inst_by_qn.get(inst_qn or "")
        if not inst:
            raise HTTPException(404, f"behavior instance '{inst_qn}' not found")
        if inst["missing"]:
            raise HTTPException(422, f"'{inst['name']}' does not set {', '.join(inst['missing'])}: a performed behavior needs every parameter")
        if inst["id"] not in performs[slot]["candidates"]:
            raise HTTPException(422, f"'{inst['name']}' is a {inst['component']}, which does not fit {slot} : {performs[slot]['type']}")
    replace = _find(cat["configurations"], body.replace, "system instance") if body.replace else None
    package = body.package or _default_package(cat, arch, "Configurations")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, package, replace)
    ordered = {s: catalog.ref(body.assignments[s], pkg_qn) for s in slots if body.assignments.get(s)}
    behaviors = {b: catalog.ref(body.behaviors[b], pkg_qn) for b in performs}
    try:
        block = catalog.configuration_text(body.name, catalog.ref(arch["qualifiedName"], pkg_qn), ordered, body.doc, behaviors)
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    verb = "Update" if replace else "Configure"
    return _commit(model_id, source, f"{verb} system {body.name} : {arch['name']}") | {
        "qualifiedName": f"{pkg_qn}::{body.name}", "text": "\n".join(block),
    }


@app.post("/projects/{project_id}/catalog/missions", status_code=201)
def save_mission(project_id: uuid.UUID, body: MissionIn) -> dict[str, Any]:
    """Create (or rewrite) a mission: an ordered list of behaviors, each with its quantities set."""
    cat, sources = _project_catalog(project_id)
    mtype = _find(cat["missionTypes"], body.missionType, "mission type")
    _check_steps(cat, body.steps)
    replace = _find(cat["missions"], body.replace, "mission") if body.replace else None
    package = body.package or _default_package(cat, mtype, "Missions")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, package, replace)
    names = catalog.step_names([s.behavior for s in body.steps])
    steps = [(n, catalog.ref(s.behavior, pkg_qn), s.values) for n, s in zip(names, body.steps)]
    try:
        block = catalog.mission_text(body.name, catalog.ref(mtype["qualifiedName"], pkg_qn), steps, body.doc)
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    verb = "Update" if replace else "Plan"
    return _commit(model_id, source, f"{verb} mission {body.name} : {mtype['name']}") | {
        "qualifiedName": f"{pkg_qn}::{body.name}", "text": "\n".join(block),
    }


def _quantities(behavior: dict[str, Any]) -> list[str]:
    """Attributes a mission step sets: those without a value in the behavior."""
    return [a["name"] for a in behavior["attributes"] if not a["value"]]


def _check_steps(cat: dict[str, Any], steps: list[StepIn]) -> None:
    behaviors = {c["qualifiedName"]: c for c in cat["components"] if c["kind"] == "action"}
    for i, s in enumerate(steps, 1):
        b = behaviors.get(s.behavior)
        if not b:
            raise HTTPException(404, f"step {i}: behavior '{s.behavior}' not found")
        quantities = _quantities(b)
        unknown = set(s.values) - set(quantities)
        if unknown:
            raise HTTPException(422, f"step {i} ({b['name']}) sets {', '.join(sorted(unknown))}, which is not one of its quantities ({', '.join(quantities) or 'none'})")
        unset = [q for q in quantities if not (s.values.get(q) or "").strip()]
        if unset:
            raise HTTPException(422, f"step {i} ({b['name']}) needs {', '.join(unset)}")


_NUM_RE = re.compile(r"-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")


def _number(v: str | None) -> float | None:
    m = _NUM_RE.search(v or "")
    return float(m.group(0)) if m else None


class _Flights:
    """Missions of a mission type flown by every system instance that performs
    behaviors, as a scratch package: each step a system can fly becomes an action
    that subsets the system's performed behavior and the mission step (see
    ``catalog.flown_step``), so the behavior's attributes evaluate with both.

    ``flights[mission key][system qn]`` is ``{"problems": [...], "steps": [{"name",
    "behavior", "performedBy", "var"}]}``; the key is the mission's qualified name,
    or ``draft`` for unsaved steps."""

    SCRATCH = "MissionCheck__"

    def __init__(self, cat: dict[str, Any], mtype: dict[str, Any], draft: list[StepIn] | None, only: str | None = None):
        self.cat = cat
        comps = {c["qualifiedName"]: c for c in cat["components"]}
        ancestors: dict[str, set[str]] = {}

        def lineage(qn: str) -> set[str]:  # the behavior and every behavior it specializes
            if qn not in ancestors:
                ancestors[qn] = {qn}.union(*(lineage(g) for g in comps.get(qn, {}).get("specializes", []) if g in comps))
            return ancestors[qn]

        missions = {
            m["qualifiedName"]: (m["qualifiedName"], m["steps"])
            for m in cat["missions"]
            if m["missionType"] == mtype["qualifiedName"] and (only is None or m["qualifiedName"] == only)
        }
        self.lines = [f"package {self.SCRATCH} {{", "    private import ScalarValues::*;"]
        if draft is not None:
            _check_steps(cat, draft)
            names = catalog.step_names([s.behavior for s in draft])
            draft_steps = [(n, s.behavior, s.values) for n, s in zip(names, draft)]
            self.lines += [f"    {l}" for l in catalog.mission_text("draft", mtype["qualifiedName"], draft_steps)]
            missions["draft"] = (f"{self.SCRATCH}::draft", [{"name": n, "behavior": b} for n, b, _ in draft_steps])
        self.archs = {a["id"]: a for a in cat["architectures"]}
        self.flights: dict[str, dict[str, dict[str, Any]]] = {}
        n = 0
        for key, (mission_qn, steps) in missions.items():
            self.flights[key] = {}
            for cfg in cat["configurations"]:
                arch = self.archs[cfg["architectureId"]]
                if not arch["behaviors"]:
                    continue
                problems, flown = set(), []
                for s in steps:
                    slot = next((b for b in arch["behaviors"] if s["behavior"] and b["type"] in lineage(s["behavior"])), None)
                    if not slot:
                        problems.add(f"{arch['name']} does not perform {(s['behavior'] or '?').rsplit('::', 1)[-1]}")
                    elif not cfg["behaviors"].get(slot["name"]):
                        problems.add(f"no behavior chosen for {slot['name']}")
                    else:
                        n += 1
                        var = f"{self.SCRATCH}::f{n}"
                        self.lines.append("    " + catalog.flown_step(f"f{n}", f"{cfg['qualifiedName']}.{slot['name']}", f"{mission_qn}.{s['name']}"))
                        flown.append({"name": s["name"], "behavior": s["behavior"], "performedBy": cfg["behaviors"][slot["name"]], "var": var})
                self.flights[key][cfg["qualifiedName"]] = {"problems": sorted(problems), "steps": [] if problems else flown}

    def evaluate(self, project_id: uuid.UUID, exprs: list[str]) -> tuple[dict[str, str | None], list[str]]:
        """Evaluate expressions with the project's models and the scratch package."""
        return _evaluate(project_id, exprs, [{"name": f"{self.SCRATCH}.sysml", "text": "\n".join([*self.lines, "}"]) + "\n"}])


def _evaluate(project_id: uuid.UUID, exprs: list[str], extra: list[dict[str, str]] | None = None) -> tuple[dict[str, str | None], list[str]]:
    """Evaluate expressions in one run, with the project's models (head versions)
    and ``extra`` files. Returns (expression -> value text, errors)."""
    if not exprs:
        return {}, []
    rows = _project_heads(project_id)
    files = [{"name": f"{r['name']}.sysml", "text": r["source"]} for r in rows] + (extra or [])
    run = _interpreter("/execute", {"files": files, "kind": "evaluate", "expression": "\n".join(dict.fromkeys(exprs)), "trace": False})
    got = {c["subject"]: (c["values"][0]["value"] if c["values"] else None) for c in run.get("checks") or []}
    return got, run.get("errors") or []


def _invoke_tool(sim: dict[str, Any], sent: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Call a simulation's external analysis through the analysis service (which
    logs the call under ``run_id``): ``{"outputs", "units"}`` or ``{"error"}``."""
    try:
        r = httpx.post(
            f"{ANALYSIS_URL}/analyses/{sim['toolName']}/invoke",
            json={"toolName": sim["toolName"], "uri": f"analysis://{sim['toolName']}", "inputs": sent, "runId": run_id},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        return {"error": f"analysis service unreachable: {exc}"}
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        return {"error": str(detail)}
    outs = r.json().get("outputs") or {}
    return {
        "outputs": {k: (v.get("value") if isinstance(v, dict) else v) for k, v in outs.items()},
        "units": {k: v["unit"] for k, v in outs.items() if isinstance(v, dict) and v.get("unit")},
    }


def _mission_checks(cat: dict[str, Any], arch: dict[str, Any], totals: list[str]) -> list[dict[str, Any]]:
    """How each mission requirement of an architecture is checked: by the mission
    simulation that verifies it, when one applies to the architecture and its
    out parameters supply every parameter of the requirement; else by the
    mission totals, when its parameters are all totals; else not at all."""
    reqs = {r["qualifiedName"]: r for r in cat["requirements"]}
    sims = {s["qualifiedName"]: s for s in cat["simulations"]}
    out = []
    for rq in arch["missionRequirements"]:
        params = reqs[rq]["params"]
        sim = next((q for q in arch["simulations"] if rq in sims[q]["verifies"] and set(params) <= set(sims[q]["outputs"])), None)
        if sim:
            out.append({"requirement": rq, "params": params, "method": "simulation", "by": sim})
        elif set(params) <= set(totals):
            out.append({"requirement": rq, "params": params, "method": "evaluation", "by": None})
    return out


def _literal(x: float) -> str:
    return format(x, ".6f")


def _run_missions(
    project_id: uuid.UUID, cat: dict[str, Any], mtype: dict[str, Any], draft: list[StepIn] | None,
    only: str | None = None, systems: list[str] | None = None, simulate: str | bool = False,
) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    """Fly missions with every system instance and check the mission requirements.

    One evaluate run gives each flown step's totals, the requirements checked by
    totals, and the inputs of the simulations needed: those verifying a
    requirement, plus ``simulate`` (a simulation def's qualified name, or True
    for the first that applies to each system). Each system's results are under
    ``simulations[simulation def]``. The simulations are then called
    through the analysis service (logged under the returned run id), and a
    second evaluate run checks the requirements they verify with their results.
    Returns ``(results[mission key][system qn], errors, run id)``."""
    totals = [t["name"] for t in mtype["totals"]]
    fl = _Flights(cat, mtype, draft, only)
    sims = {s["qualifiedName"]: s for s in cat["simulations"]}
    comps = {c["qualifiedName"]: c for c in cat["components"]}
    cfgs = {c["qualifiedName"]: c for c in cat["configurations"]}
    out: dict[str, dict[str, Any]] = {}
    exprs: list[str] = []
    plans: dict[tuple[str, str], dict[str, Any]] = {}

    def static_expr(rq: str, cfg_qn: str, params: list[str], steps: list[dict[str, Any]]) -> str:
        args = [" + ".join(f"{s['var']}.{p}" for s in steps) or "0" for p in params]
        return f"{rq}({', '.join([cfg_qn, *args])})"

    for key, per_cfg in fl.flights.items():
        out[key] = {}
        for cfg_qn, f in per_cfg.items():
            if systems is not None and cfg_qn not in systems:
                continue
            arch = fl.archs[cfgs[cfg_qn]["architectureId"]]
            checks = _mission_checks(cat, arch, totals)
            res: dict[str, Any] = {
                "problems": f["problems"], "steps": [], "totals": {}, "verdicts": {},
                "verification": {c["requirement"]: {"method": c["method"], "by": c["by"]} for c in checks},
                "checks": checks, "simulations": {},
            }
            out[key][cfg_qn] = res
            if f["problems"]:
                continue
            exprs += [f"{s['var']}.{t}" for s in f["steps"] for t in totals]
            exprs += [static_expr(c["requirement"], cfg_qn, c["params"], f["steps"]) for c in checks if c["method"] == "evaluation"]
            needed = list(dict.fromkeys(c["by"] for c in checks if c["method"] == "simulation"))
            if simulate is True and arch["simulations"] and arch["simulations"][0] not in needed:
                needed.append(arch["simulations"][0])
            elif isinstance(simulate, str) and simulate not in needed:
                if simulate in arch["simulations"]:
                    needed.append(simulate)
                else:
                    res["simulations"][simulate] = {"simulation": simulate, "error": f"{short_name(simulate)} does not apply to {arch['name']}"}
            if simulate is True and not arch["simulations"]:
                res["simulations"][""] = {"error": f"no mission simulation applies to {arch['name']}"}
            if not needed:
                continue
            feats = [[a["name"] for a in comps[s["behavior"]]["attributes"]] + [p["name"] for p in comps[s["behavior"]]["params"]] for s in f["steps"]]
            exprs += [f"{s['var']}.{x}" for s, fs in zip(f["steps"], feats) for x in fs]
            for sim_q in needed:
                sim = sims[sim_q]
                subject = re.compile(rf"(?<![\w:.]){re.escape(sim['subject'])}\b")
                inputs = {i["name"]: subject.sub(cfg_qn, i["value"]) for i in sim["inputs"]}
                exprs += list(inputs.values())
                plans[(key, cfg_qn, sim_q)] = {"sim": sim, "inputs": inputs, "steps": list(zip(f["steps"], feats))}
    got, errors = fl.evaluate(project_id, list(dict.fromkeys(exprs)))

    # totals and the requirements checked by them
    for key, per_cfg in out.items():
        for cfg_qn, res in per_cfg.items():
            f = fl.flights[key][cfg_qn]
            if f["problems"]:
                continue
            res["steps"] = [
                {"name": s["name"], "performedBy": s["performedBy"], "values": {t: _number(got.get(f"{s['var']}.{t}")) for t in totals}}
                for s in f["steps"]
            ]
            vals = {t: [s["values"][t] for s in res["steps"]] for t in totals}
            res["totals"] = {t: None if None in v else sum(v) for t, v in vals.items()}
            for c in res["checks"]:
                if c["method"] == "evaluation":
                    v = got.get(static_expr(c["requirement"], cfg_qn, c["params"], f["steps"]))
                    res["verdicts"][c["requirement"]] = "holds" if v == "true" else "fails" if v == "false" else "error"
                    res["verification"][c["requirement"]]["values"] = {p: res["totals"].get(p) for p in c["params"]}

    # the simulations, through the analysis service (or a stored result with the same inputs)
    run_id = str(uuid.uuid4())
    with db.pool.connection() as c:
        commit_id = repo.head(c, project_id)
    versions = _tool_versions({p["sim"]["toolName"] for p in plans.values()})

    def call(item: tuple[tuple[str, str, str], dict[str, Any]]) -> tuple[tuple[str, str, str], dict[str, Any]]:
        k, p = item
        sim = p["sim"]
        inputs: dict[str, Any] = {name: _value(got.get(expr)) for name, expr in p["inputs"].items()}
        res: dict[str, Any] = {"simulation": sim["qualifiedName"], "toolName": sim["toolName"], "toolVersion": versions.get(sim["toolName"]), "inputs": inputs}
        missing = [name for name, v in inputs.items() if _unset(v)]
        if missing:
            return k, res | {"error": f"could not evaluate {', '.join(missing)}"}
        steps = [
            {
                "name": s["name"],
                "behavior": s["behavior"].rsplit("::", 1)[-1],
                "performedBy": s["performedBy"].rsplit("::", 1)[-1],
                **{x: _value(got.get(f"{s['var']}.{x}")) for x in fs},
            }
            for s, fs in p["steps"]
        ]
        sent = {**inputs, "steps": steps}
        res["_hash"] = _input_hash(sim["toolName"], res["toolVersion"], sent)
        res["_sent"] = sent
        stored = _stored_result(project_id, commit_id, k[0], k[1], sim["qualifiedName"], sim["toolName"], res["_hash"])
        if stored:
            same = stored["commit_id"] == commit_id and stored["mission"] == k[0] and stored["system"] == k[1]
            return k, res | {
                "outputs": stored["outputs"], "units": stored["units"],
                "_stored": str(stored["id"]) if same else None, "_reused": str(stored["id"]),
            }
        return k, res | _invoke_tool(sim, sent, run_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for (key, cfg_qn, sim_q), sres in pool.map(call, plans.items()):
            out[key][cfg_qn]["simulations"][sim_q] = sres

    # the requirements the simulations verify, checked with their results
    pending: list[tuple[dict[str, Any], str, str]] = []
    for per_cfg in out.values():
        for cfg_qn, res in per_cfg.items():
            for c in res["checks"]:
                if c["method"] != "simulation" or res["problems"]:
                    continue
                o = (res["simulations"].get(c["by"]) or {}).get("outputs") or {}
                if o.get("completed") is False:
                    # results of a flight that never finished verify nothing
                    res["verdicts"][c["requirement"]] = "fails"
                    res["verification"][c["requirement"]]["note"] = "the simulated mission did not complete"
                    continue
                vals = [o.get(p) for p in c["params"]]
                if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vals):
                    res["verdicts"][c["requirement"]] = "error"
                    continue
                res["verification"][c["requirement"]]["values"] = dict(zip(c["params"], vals))
                expr = f"{c['requirement']}({', '.join([cfg_qn, *(_literal(x) for x in vals)])})"
                pending.append((res, c["requirement"], expr))
    if pending:
        got2, errors2 = fl.evaluate(project_id, [e for _, _, e in pending])
        errors += errors2
        for res, rq, expr in pending:
            v = got2.get(expr)
            res["verdicts"][rq] = "holds" if v == "true" else "fails" if v == "false" else "error"

    # every simulation result into the results store (outside the SysML v2 repository)
    draft_steps = [s.model_dump() for s in draft] if draft is not None else None
    with db.pool.connection() as c, c.transaction():
        for key, per_cfg in out.items():
            for cfg_qn, res in per_cfg.items():
                for sim_q, sres in res["simulations"].items():
                    if "_hash" not in sres:
                        continue  # nothing was sent: no result to keep
                    mine = {rq: v for rq, v in res["verification"].items() if v.get("by") == sim_q}
                    rid = sres.pop("_stored", None)
                    if not rid:
                        rid = str(uuid.uuid4())
                        c.execute(
                            """INSERT INTO models.simulation_result
                               (id, run_id, project_id, commit_id, mission_type, mission, draft_steps, system, simulation,
                                tool_name, tool_version, input_hash, inputs, outputs, units, verdicts, verification, error, reused_from)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (
                                rid, run_id, project_id, commit_id, mtype["qualifiedName"], key,
                                Jsonb(draft_steps) if key == "draft" else None, cfg_qn, sim_q, sres["toolName"], sres["toolVersion"],
                                sres["_hash"], Jsonb(sres["_sent"]), Jsonb(sres.get("outputs")), Jsonb(sres.get("units") or {}),
                                Jsonb({rq: res["verdicts"].get(rq) for rq in mine}), Jsonb(mine), sres.get("error"), sres.get("_reused"),
                            ),
                        )
                    sres["resultId"] = rid
                    sres["reused"] = bool(sres.pop("_reused", None))
                    sres["commitId"] = str(commit_id) if commit_id else None
                    sres.pop("_hash"), sres.pop("_sent")
    for per_cfg in out.values():
        for res in per_cfg.values():
            res.pop("checks")
    return out, errors, run_id


def _tool_versions(tools: set[str]) -> dict[str, str | None]:
    """The registry's version of each analysis: a result is only reused for the same one."""
    out: dict[str, str | None] = {}
    for t in tools:
        try:
            r = httpx.get(f"{ANALYSIS_URL}/analyses/{t}", timeout=10)
            out[t] = str(r.json().get("version")) if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            out[t] = None
    return out


def _input_hash(tool: str, version: str | None, sent: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"tool": tool, "version": version, "inputs": sent}, sort_keys=True, default=str).encode()).hexdigest()


def _stored_result(project_id: Any, commit_id: Any, mission: str, system: str, simulation: str, tool: str, input_hash: str) -> dict[str, Any] | None:
    """A stored result for the same simulator inputs: this very flight at this
    commit first, else any with outputs (the tool's version is in the hash)."""
    with db.pool.connection() as c:
        return c.execute(
            """SELECT id, commit_id, mission, system, outputs, units FROM models.simulation_result
               WHERE tool_name=%s AND input_hash=%s AND outputs IS NOT NULL
               ORDER BY (project_id=%s AND commit_id IS NOT DISTINCT FROM %s AND mission=%s AND system=%s AND simulation=%s) DESC,
                        created_at DESC
               LIMIT 1""",
            (tool, input_hash, project_id, commit_id, mission, system, simulation),
        ).fetchone()


@app.post("/projects/{project_id}/catalog/missions/evaluate")
def evaluate_missions(project_id: uuid.UUID, body: MissionCheckIn) -> dict[str, Any]:
    """Fly every mission of a mission type (and an unsaved draft) with every system
    instance that performs behaviors and check the mission requirements: those a
    mission simulation verifies with its results, the others with the mission
    totals (see ``_mission_checks``). Each result says how each requirement was
    verified; the simulation summary leaves out its timeline and steps."""
    cat, _ = _project_catalog(project_id)
    mtype = _find(cat["missionTypes"], body.missionType, "mission type")
    out, errors, run_id = _run_missions(project_id, cat, mtype, body.draft)
    for per_cfg in out.values():
        for res in per_cfg.values():
            for sim in res["simulations"].values():
                if sim.get("outputs"):
                    sim["outputs"] = {k: v for k, v in sim["outputs"].items() if k not in BULKY_OUTPUTS}
    return {"missionType": mtype["qualifiedName"], "totals": [t["name"] for t in mtype["totals"]], "missions": out, "runId": run_id, "errors": errors}


# simulation outputs left out of mission evaluations (the simulate endpoint returns them)
BULKY_OUTPUTS = ("timeline", "flownSteps", "propellerCurves", "motorCurves")


def short_name(qn: str) -> str:
    return qn.rsplit("::", 1)[-1]


class SimulateIn(BaseModel):
    missionType: str
    simulation: str | None = Field(None, description="qualified name of the simulation def (default: the first that applies to each system)")
    mission: str | None = Field(None, description="qualified name of a saved mission; omit to simulate the draft")
    draft: list[StepIn] | None = None
    systems: list[str] | None = Field(None, description="system instances to simulate (default: every one a simulation applies to)")


def _unset(v: Any) -> bool:
    """An input that could not be evaluated: no value, or one the model leaves
    undetermined (e.g. a slot with no component chosen)."""
    return v is None or (isinstance(v, str) and "undetermined" in v)


def _value(v: str | None) -> Any:
    """An evaluated value as JSON: booleans, numbers, else the text."""
    if v in ("true", "false"):
        return v == "true"
    n = _number(v)
    return v if n is None else n


@app.post("/projects/{project_id}/catalog/missions/simulate")
def simulate_mission(project_id: uuid.UUID, body: SimulateIn) -> dict[str, Any]:
    """Simulate one mission (or the draft) for every system instance that can fly
    it and that a mission simulation applies to, and check the requirements the
    simulation verifies with its results. The simulation def's in parameters are
    evaluated with the system as subject, every attribute and parameter of each
    flown step is evaluated, and the result is sent to the simulation's external
    analysis through the analysis service (which logs the call under the
    returned run id)."""
    if (body.mission is None) == (body.draft is None):
        raise HTTPException(422, "give either a mission or draft steps")
    cat, _ = _project_catalog(project_id)
    mtype = _find(cat["missionTypes"], body.missionType, "mission type")
    if body.mission:
        _find(cat["missions"], body.mission, "mission")
    if body.simulation:
        _find(cat["simulations"], body.simulation, "simulation")
    out, errors, run_id = _run_missions(
        project_id, cat, mtype, body.draft, only=body.mission or "", systems=body.systems, simulate=body.simulation or True,
    )
    key = body.mission or "draft"
    systems = {}
    for cfg_qn, res in out.get(key, {}).items():
        # the simulation asked for, else the first that applies (a system may also
        # have run others, to verify their requirements)
        chosen = body.simulation or _first_simulation(cat, cfg_qn)
        sim = res["simulations"].get(chosen or "") or res["simulations"].get("") or {}
        by = sim.get("simulation")
        systems[cfg_qn] = {
            "problems": res["problems"], **sim,
            "verdicts": {rq: v for rq, v in res["verdicts"].items() if res["verification"][rq]["by"] == by and by},
            "verification": {rq: v for rq, v in res["verification"].items() if v["by"] == by and by},
        }
    return {"mission": key, "simulation": body.simulation, "runId": run_id, "systems": systems, "errors": errors}


def _first_simulation(cat: dict[str, Any], cfg_qn: str) -> str | None:
    cfg = next((c for c in cat["configurations"] if c["qualifiedName"] == cfg_qn), None)
    arch = next((a for a in cat["architectures"] if cfg and a["id"] == cfg["architectureId"]), None)
    return arch["simulations"][0] if arch and arch["simulations"] else None


# ------------------------------------------------------------------ simulation results and evidence
EVIDENCE_MODEL = "VerificationEvidence"


def _result_json(r: dict[str, Any], full: bool = True) -> dict[str, Any]:
    outputs = r["outputs"]
    if outputs and not full:
        outputs = {k: v for k, v in outputs.items() if k not in BULKY_OUTPUTS}
    out = {
        "id": str(r["id"]), "runId": str(r["run_id"]), "projectId": str(r["project_id"]),
        "commitId": str(r["commit_id"]) if r["commit_id"] else None,
        "missionType": r["mission_type"], "mission": r["mission"], "system": r["system"], "simulation": r["simulation"],
        "toolName": r["tool_name"], "toolVersion": r["tool_version"], "inputHash": r["input_hash"],
        "outputs": outputs, "units": r["units"], "verdicts": r["verdicts"], "verification": r["verification"],
        "error": r["error"], "reusedFrom": str(r["reused_from"]) if r["reused_from"] else None,
        "createdAt": r["created_at"].isoformat(),
    }
    if full:
        out["inputs"] = r["inputs"]
        out["draftSteps"] = r["draft_steps"]
    return out


@app.get("/results/{result_id}")
def get_result(result_id: uuid.UUID) -> dict[str, Any]:
    """A stored simulation result: what was sent to the simulator, everything it returned, and the verdicts."""
    with db.pool.connection() as c:
        r = c.execute("SELECT * FROM models.simulation_result WHERE id=%s", (result_id,)).fetchone()
    if not r:
        raise HTTPException(404, "result not found")
    return _result_json(r)


@app.get("/projects/{project_id}/results")
def list_results(
    project_id: uuid.UUID, mission: str | None = None, system: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    """Stored simulation results of a project, newest first, without inputs, timelines or curves."""
    q = "SELECT * FROM models.simulation_result WHERE project_id=%s"
    args: list[Any] = [project_id]
    for col, v in (("mission", mission), ("system", system)):
        if v:
            q += f" AND {col}=%s"
            args.append(v)
    with db.pool.connection() as c:
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT %s", (*args, limit)).fetchall()
    return [_result_json(r, full=False) for r in rows]


def _evidence_status(project_id: uuid.UUID, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evidence elements of the catalog with their stored result and whether they
    are current: the newest result for the same mission, system and simulation
    has the same simulator inputs, simulator version and verdicts."""
    ids = [e["values"].get("resultId") for e in evidence if repo.is_uuid(e["values"].get("resultId"))]
    with db.pool.connection() as c:
        rows = {str(r["id"]): r for r in c.execute("SELECT * FROM models.simulation_result WHERE id = ANY(%s::uuid[])", (ids,)).fetchall()} if ids else {}
        out = []
        for e in evidence:
            r = rows.get(e["values"].get("resultId") or "")
            status, latest_id = "missing", None
            if r:
                latest = c.execute(
                    """SELECT id, input_hash, tool_version, verdicts FROM models.simulation_result
                       WHERE project_id=%s AND mission=%s AND system=%s AND simulation=%s AND outputs IS NOT NULL
                       ORDER BY created_at DESC LIMIT 1""",
                    (project_id, r["mission"], r["system"], r["simulation"]),
                ).fetchone()
                latest_id = str(latest["id"])
                same = (latest["input_hash"], latest["tool_version"], latest["verdicts"]) == (r["input_hash"], r["tool_version"], r["verdicts"])
                status = "current" if same else "outdated"
            out.append({**e, "status": status, "result": _result_json(r, full=False) if r else None, "latestResultId": latest_id})
    return out


@app.get("/projects/{project_id}/evidence")
def list_evidence(project_id: uuid.UUID) -> list[dict[str, Any]]:
    """Simulation evidence recorded in the project's models, each with its stored
    result and whether it is still current (see ``_evidence_status``)."""
    cat, _ = _project_catalog(project_id)
    return _evidence_status(project_id, cat["evidence"])


class EvidenceIn(BaseModel):
    tag: bool = Field(False, description="also tag the commit that was simulated")


@app.post("/results/{result_id}/evidence", status_code=201)
def record_evidence(result_id: uuid.UUID, body: EvidenceIn) -> dict[str, Any]:
    """Record a stored result as verification evidence in the model: a
    verification usage of the simulation, with its subject bound to the system
    instance and SimulationEvidence metadata pointing at the result. It goes into
    the project's VerificationEvidence model (created if needed) and replaces the
    evidence already recorded for the same mission, system and simulation. The
    evidence commit is a new version; the evidence names the commit simulated."""
    with db.pool.connection() as c:
        r = c.execute("SELECT * FROM models.simulation_result WHERE id=%s", (result_id,)).fetchone()
    if not r:
        raise HTTPException(404, "result not found")
    if r["mission"] == "draft":
        raise HTTPException(422, "a draft mission is not in the model: save the mission, simulate it, then record the result")
    if not r["outputs"]:
        raise HTTPException(422, f"the simulation failed, there is nothing to record: {r['error']}")
    project_id = r["project_id"]
    cat, sources = _project_catalog(project_id)
    sim = _find(cat["simulations"], r["simulation"], "simulation")
    _find(cat["configurations"], r["system"], "system instance")
    tag = cat["tags"].get(catalog.EVIDENCE_TAG)

    # the evidence model, created on first use
    pkg = next((p for p in cat["packages"] if p["modelName"] == EVIDENCE_MODEL and "::" not in p["qualifiedName"]), None)
    if not pkg:
        with db.pool.connection() as c:
            if c.execute("SELECT 1 FROM models.model WHERE project_id=%s AND name=%s", (project_id, EVIDENCE_MODEL)).fetchone():
                raise HTTPException(409, f"the {EVIDENCE_MODEL} model has no package {EVIDENCE_MODEL}: fix it in the editor")
        create_model(project_id, ModelIn(name=EVIDENCE_MODEL, description="Mission simulation results recorded as verification evidence",
                                         source=catalog.EVIDENCE_PACKAGE.format(name=EVIDENCE_MODEL)))
        cat, sources = _project_catalog(project_id)
        pkg = _package(cat, EVIDENCE_MODEL)
        tag = cat["tags"].get(catalog.EVIDENCE_TAG)
    if not tag:
        raise HTTPException(409, f"no 'metadata def {catalog.EVIDENCE_TAG}' in this project")

    verdicts = r["verdicts"] or {}
    overall = "holds" if verdicts and all(v == "holds" for v in verdicts.values()) else "fails" if verdicts else "none verified"
    mission = r["mission"]
    values = {
        "resultId": str(r["id"]),
        "uri": f"/api/models/results/{r['id']}",
        "commitId": str(r["commit_id"]) if r["commit_id"] else "",
        "mission": mission,
        "verdict": overall,
        "verdicts": "; ".join(f"{short_name(q)} {v}" for q, v in verdicts.items()),
        "recordedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if mission == SYSTEM_KEY:  # a simulation of the system on its own
        name = re.sub(r"\W", "_", f"{short_name(r['system'])}_{short_name(r['simulation'])}")
    else:
        name = re.sub(r"\W", "_", f"{short_name(r['system'])}_{short_name(mission)}")
        if len(cat["simulations"]) > 1:  # evidence of each simulation of the same flight
            name += f"_{short_name(r['simulation'])}"
    replace = next(
        (e for e in cat["evidence"] if e["values"].get("mission") == mission and e["system"] == r["system"] and e["simulation"] == r["simulation"]),
        None,
    )
    model_id, pkg_qn, apply = _place(cat, sources, name, pkg["qualifiedName"], replace)
    o = r["outputs"]
    doc = (f"{short_name(r['simulation'])} of {short_name(r['system'])}: {overall}" if mission == SYSTEM_KEY
           else f"{short_name(mission)} flown by {short_name(r['system'])}: {overall}")
    if isinstance(o.get("energyPercent"), (int, float)):
        doc += f", {o['energyPercent']:.1f} % of the battery"
    if isinstance(o.get("missionTime"), (int, float)):
        doc += f", {o['missionTime'] / 60:.1f} min"
    try:
        block = catalog.evidence_text(
            name, sim["qualifiedName"], sim["subject"], r["system"], values, doc, catalog.ref(tag, pkg_qn),
        )
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    res = _commit(model_id, source, f"Record evidence: {doc}")

    tagged = None
    if body.tag and r["commit_id"]:
        tag_name = f"verified/{short_name(mission)}/{short_name(r['system'])}/{str(r['id'])[:8]}"
        with db.pool.connection() as c:
            row = c.execute(
                """INSERT INTO models.tags (project_id, name, description, alias, tagged_commit_id) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (project_id, name) DO NOTHING RETURNING name""",
                (project_id, tag_name, f"{doc} (result {r['id']})", [], r["commit_id"]),
            ).fetchone()
        tagged = tag_name if row else None
    return res | {"qualifiedName": f"{pkg_qn}::{name}", "text": "\n".join(block), "tag": tagged}


# ------------------------------------------------------------------ simulations of a system on its own
SYSTEM_KEY = "system"  # the results store's "mission" of a system simulation


def _behavior_inputs(cat: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The behaviors a system instance performs, with each one's numeric and
    boolean values (parameters set by its behavior instance, defaults of its type)."""
    insts = {i["qualifiedName"]: i for i in cat["instances"]}
    out = []
    for slot, qn in sorted(cfg.get("behaviors", {}).items()):  # a stable order, for the input hash
        i = insts.get(qn or "")
        if not i:
            continue
        vals = {k: _value(v) for k, v in i["values"].items()}
        out.append({
            "slot": slot, "behavior": short_name(i["component"]), "instance": i["name"],
            **{k: v for k, v in vals.items() if isinstance(v, (int, float, bool))},
        })
    return out


def _run_system(
    project_id: uuid.UUID, cat: dict[str, Any], plans_in: list[tuple[str, str]],
) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    """Run system simulations: ``plans_in`` lists (system instance, simulation
    def). Each simulation's in parameters are evaluated with the system as
    subject and sent with the system's performed ``behaviors``; a stored result
    with the same inputs is reused. The requirements a simulation verifies are
    then checked with its results, and every result is stored.
    Returns ``(results[system][simulation], errors, run id)``."""
    sims = {x["qualifiedName"]: x for x in cat["simulations"]}
    cfgs = {c["qualifiedName"]: c for c in cat["configurations"]}
    reqs = {r["qualifiedName"]: r for r in cat["requirements"]}
    archs = {a["id"]: a for a in cat["architectures"]}

    def bind(expr: str, subject: str, cfg_qn: str) -> str:
        return re.sub(rf"(?<![\w:.]){re.escape(subject)}\b", cfg_qn, expr)

    plans: dict[tuple[str, str], dict[str, Any]] = {}
    exprs: list[str] = []
    for cfg_qn, sim_q in plans_in:
        sim = sims[sim_q]
        inputs = {i["name"]: bind(i["value"], sim["subject"], cfg_qn) for i in sim["inputs"]}
        exprs += list(inputs.values())
        # the control tests of the system's architecture: each attribute bound to the system
        tests = {
            rq: {k: bind(v, reqs[rq]["subject"], cfg_qn) for k, v in reqs[rq]["attributes"].items()}
            for rq in archs[cfgs[cfg_qn]["architectureId"]]["systemRequirements"] if reqs[rq]["isTest"]
        }
        exprs += [e for t in tests.values() for e in t.values()]
        plans[(cfg_qn, sim_q)] = {"sim": sim, "inputs": inputs, "tests": tests}
    got, errors = _evaluate(project_id, exprs)
    run_id = str(uuid.uuid4())
    with db.pool.connection() as c:
        commit_id = repo.head(c, project_id)
    versions = _tool_versions({p["sim"]["toolName"] for p in plans.values()})

    def call(item: tuple[tuple[str, str], dict[str, Any]]) -> tuple[tuple[str, str], dict[str, Any]]:
        (cfg_qn, sim_q), p = item
        sim = p["sim"]
        inputs = {name: _value(got.get(expr)) for name, expr in p["inputs"].items()}
        res: dict[str, Any] = {"simulation": sim_q, "toolName": sim["toolName"], "toolVersion": versions.get(sim["toolName"]), "inputs": inputs}
        missing = [name for name, v in inputs.items() if _unset(v)]
        if missing:
            return (cfg_qn, sim_q), res | {"error": f"{short_name(cfg_qn)} gives no value for {', '.join(missing)}: is every slot they depend on filled?"}
        tests = [
            {"requirement": rq, "name": short_name(rq), **{k: _value(got.get(e)) for k, e in attrs.items()}}
            for rq, attrs in p["tests"].items()
        ]
        for t in tests:  # a SysML string evaluates with its quotes
            if isinstance(t.get("command"), str):
                t["command"] = t["command"].strip('"')
        sent = {**inputs, "behaviors": _behavior_inputs(cat, cfgs[cfg_qn]), "tests": tests}
        res["_hash"] = _input_hash(sim["toolName"], res["toolVersion"], sent)
        res["_sent"] = sent
        stored = _stored_result(project_id, commit_id, SYSTEM_KEY, cfg_qn, sim_q, sim["toolName"], res["_hash"])
        if stored:
            same = stored["commit_id"] == commit_id and stored["mission"] == SYSTEM_KEY and stored["system"] == cfg_qn
            return (cfg_qn, sim_q), res | {"outputs": stored["outputs"], "units": stored["units"],
                                           "_stored": str(stored["id"]) if same else None, "_reused": str(stored["id"])}
        return (cfg_qn, sim_q), res | _invoke_tool(sim, sent, run_id)

    out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (cfg_qn, sim_q), res in pool.map(call, plans.items()):
            res["verdicts"], res["verification"] = {}, {}
            out.setdefault(cfg_qn, {})[sim_q] = res

    # the requirements each simulation verifies, with its results
    pending: list[tuple[dict[str, Any], str, str]] = []
    for cfg_qn, per_sim in out.items():
        for sim_q, res in per_sim.items():
            sim = sims[sim_q]
            o = res.get("outputs") or {}
            for rq in sim["verifies"]:
                params = reqs[rq]["params"] if rq in reqs else []
                is_test = rq in reqs and reqs[rq]["isTest"]
                # a control test is checked with its own results, the others with the simulation's outputs
                if not params or (not is_test and not set(params) <= set(sim["outputs"])):
                    continue
                ver: dict[str, Any] = {"method": "simulation", "by": sim_q}
                res["verification"][rq] = ver
                if not o:
                    res["verdicts"][rq] = "error"
                    ver["note"] = res.get("error") or "no result"
                    continue
                if is_test:
                    src = (o.get("testResults") or {}).get(rq)
                    if src is None:
                        failed = next((x for x in o.get("scenarios") or [] if x.get("requirement") == rq and x.get("error")), None)
                        res["verdicts"][rq] = "fails" if failed and "diverged" in failed["error"] else "error"
                        ver["note"] = failed["error"] if failed else "the test was not flown"
                        continue
                else:
                    if o.get("completed") is False:
                        res["verdicts"][rq] = "fails"
                        ver["note"] = "a control test could not be completed"
                        continue
                    src = o
                vals = [src.get(x) for x in params]
                ver["values"] = dict(zip(params, vals))
                if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vals):
                    res["verdicts"][rq] = "error"
                    ver["note"] = f"no value for {', '.join(x for x, v in zip(params, vals) if not isinstance(v, (int, float)))}"
                    continue
                expr = f"{rq}({', '.join([cfg_qn, *(_literal(x) for x in vals)])})"
                pending.append((res, rq, expr))
    if pending:
        got2, errors2 = _evaluate(project_id, [e for _, _, e in pending])
        errors += errors2
        for res, rq, expr in pending:
            v = got2.get(expr)
            res["verdicts"][rq] = "holds" if v == "true" else "fails" if v == "false" else "error"

    # into the results store
    with db.pool.connection() as c, c.transaction():
        for cfg_qn, per_sim in out.items():
            for sim_q, res in per_sim.items():
                if "_hash" not in res:
                    continue
                rid = res.pop("_stored", None)
                if not rid:
                    rid = str(uuid.uuid4())
                    c.execute(
                        """INSERT INTO models.simulation_result
                           (id, run_id, project_id, commit_id, mission_type, mission, draft_steps, system, simulation,
                            tool_name, tool_version, input_hash, inputs, outputs, units, verdicts, verification, error, reused_from)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            rid, run_id, project_id, commit_id, "", SYSTEM_KEY, None, cfg_qn, sim_q, res["toolName"], res["toolVersion"],
                            res["_hash"], Jsonb(res["_sent"]), Jsonb(res.get("outputs")), Jsonb(res.get("units") or {}),
                            Jsonb(res["verdicts"]), Jsonb(res["verification"]), res.get("error"), res.get("_reused"),
                        ),
                    )
                res["resultId"] = rid
                res["reused"] = bool(res.pop("_reused", None))
                res["commitId"] = str(commit_id) if commit_id else None
                res.pop("_hash"), res.pop("_sent")
    return out, errors, run_id


class SystemSimulateIn(BaseModel):
    architecture: str | None = Field(None, description="simulate every system instance of this architecture")
    systems: list[str] | None = Field(None, description="or these system instances")
    simulation: str | None = Field(None, description="a system simulation def (default: every verification def that applies)")


@app.post("/projects/{project_id}/catalog/configurations/simulate")
def simulate_systems(project_id: uuid.UUID, body: SystemSimulateIn) -> dict[str, Any]:
    """Run the system simulations (verification defs tagged #SystemSimulation)
    of system instances and check the requirements they verify with their results."""
    cat, _ = _project_catalog(project_id)
    archs = {a["id"]: a for a in cat["architectures"]}
    sims = {x["qualifiedName"]: x for x in cat["simulations"]}
    cfgs = [c for c in cat["configurations"]
            if (body.systems is None or c["qualifiedName"] in body.systems)
            and (body.architecture is None or archs[c["architectureId"]]["qualifiedName"] == body.architecture)]
    plans = []
    for c in cfgs:
        for q in archs[c["architectureId"]]["systemSimulations"]:
            if (body.simulation and q == body.simulation) or (not body.simulation and sims[q]["kind"] == "verification def"):
                plans.append((c["qualifiedName"], q))
    out, errors, run_id = _run_system(project_id, cat, plans)
    return {"runId": run_id, "systems": out, "errors": errors}


class TuneIn(BaseModel):
    system: str
    simulation: str | None = Field(None, description="a system simulation def (default: the first analysis def that applies)")


@app.post("/projects/{project_id}/catalog/configurations/tune")
def tune_system(project_id: uuid.UUID, body: TuneIn) -> dict[str, Any]:
    """Run a tuning analysis (an analysis def tagged #SystemSimulation) for one
    system instance. Its out parameters are proposals for the attributes of the
    component in one of the system's slots (the slot whose type has them all):
    they are returned with that slot, its type and the instance it holds now."""
    cat, _ = _project_catalog(project_id)
    cfg = _find(cat["configurations"], body.system, "system instance")
    arch = next(a for a in cat["architectures"] if a["id"] == cfg["architectureId"])
    sims = {x["qualifiedName"]: x for x in cat["simulations"]}
    cands = [q for q in arch["systemSimulations"] if (body.simulation == q) or (not body.simulation and sims[q]["kind"] == "analysis def")]
    if not cands:
        raise HTTPException(404, f"no tuning analysis applies to {arch['name']}")
    sim = sims[cands[0]]
    out, errors, run_id = _run_system(project_id, cat, [(cfg["qualifiedName"], sim["qualifiedName"])])
    res = out[cfg["qualifiedName"]][sim["qualifiedName"]]
    o = res.get("outputs") or {}
    proposals = {k: o[k] for k in sim["outputs"] if isinstance(o.get(k), (int, float)) and not isinstance(o.get(k), bool)}
    comps = {c["qualifiedName"]: c for c in cat["components"]}
    slot = next(
        (s for s in arch["slots"] if proposals and set(proposals) <= {a["name"] for a in comps.get(s["type"], {}).get("attributes", [])}),
        None,
    )
    insts = {i["qualifiedName"]: i for i in cat["instances"]}
    current = insts.get(cfg["assignments"].get(slot["name"]) or "") if slot else None
    return {
        "runId": run_id, "system": cfg["qualifiedName"], "simulation": sim["qualifiedName"], "result": res, "errors": errors,
        "proposals": proposals,
        "slot": slot["name"] if slot else None, "component": slot["type"] if slot else None,
        "current": {"qualifiedName": current["qualifiedName"], "values": current["values"]} if current else None,
    }


# ------------------------------------------------------------------ the specification
@app.post("/projects/{project_id}/requirements/check")
def check_requirements(project_id: uuid.UUID) -> dict[str, Any]:
    """Check the specification. Every requirement instance whose type needs no
    measured values is evaluated as ``Instance(subject)`` for each system or
    component instance it applies to, in one evaluate run. Instances that need
    measured values are left to the simulations that verify them. Statements
    are rolled up: for a subject, a statement holds when every instance under it
    that applies does, fails when one fails.
    Returns ``verdicts`` and ``rollup``, both requirement -> subject -> verdict."""
    cat, _ = _project_catalog(project_id)
    spec = cat["specification"]
    exprs: dict[tuple[str, str], str] = {}
    for r in spec:
        if r["type"] and not r["measured"]:
            for subj in r["appliesTo"]:
                exprs[(r["qualifiedName"], subj)] = f"{r['qualifiedName']}({subj})"
    got, errors = _evaluate(project_id, list(exprs.values()))
    verdicts: dict[str, dict[str, str]] = {}
    for (rq, subj), e in exprs.items():
        v = got.get(e)
        verdicts.setdefault(rq, {})[subj] = "holds" if v == "true" else "fails" if v == "false" else "error"
    for r in spec:
        if r["type"] and r["measured"]:  # a simulation verifies it (missions, control)
            verdicts[r["qualifiedName"]] = {subj: "unverified" for subj in r["appliesTo"]}
    children: dict[str, list[str]] = {}
    for r in spec:
        if r["parent"]:
            children.setdefault(r["parent"], []).append(r["qualifiedName"])
    rank = {"fails": 3, "error": 2, "unverified": 1, "holds": 0}
    rollup: dict[str, dict[str, str]] = {}

    def roll(q: str) -> dict[str, str]:
        if q in rollup:
            return rollup[q]
        out = dict(verdicts.get(q, {}))
        for c in children.get(q, []):
            for subj, v in roll(c).items():
                if subj not in out or rank[v] > rank[out[subj]]:
                    out[subj] = v
        rollup[q] = out
        return out

    for r in spec:
        roll(r["qualifiedName"])
    return {"verdicts": verdicts, "rollup": rollup, "errors": errors}


@app.delete("/projects/{project_id}/catalog/elements")
def delete_catalog_element(project_id: uuid.UUID, qualifiedName: str) -> dict[str, Any]:
    """Remove a component, behavior, instance, system instance or mission from its
    model's text. Refused while something else still refers to it."""
    cat, sources = _project_catalog(project_id)
    items = cat["components"] + cat["instances"] + cat["configurations"] + cat["missions"] + cat["evidence"]
    item = _find(items, qualifiedName, "element")
    source = catalog.remove_element(sources[item["modelId"]], item["loc"])
    return _commit(item["modelId"], source, f"Remove {item['name']}") | {"qualifiedName": qualifiedName}
