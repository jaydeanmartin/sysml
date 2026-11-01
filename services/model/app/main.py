"""Model service: projects, versioned SysML v2 models, their interpretation and runs.

The SysML v2 text is the source of truth. Models are stored in a SysML v2 API server
(the OMG Systems Modeling API & Services, see store.py): a project is an API project,
each save is a commit carrying the model's text and its interpreted elements (only what
changed). The interpreter service validates the text and returns the elements and a
diagram graph. Executions are proxied to the interpreter and recorded in PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import catalog, db, legacy, store

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per SysML v2 API request otherwise
log = logging.getLogger("model")

INTERPRETER_URL = os.environ.get("INTERPRETER_SERVICE_URL", "http://interpreter:8002")
ANALYSIS_URL = os.environ.get("ANALYSIS_SERVICE_URL", "http://analysis:8003")
EXAMPLES_DIR = Path(os.environ.get("EXAMPLES_DIR", "/app/examples"))
SEED_DEMO = os.environ.get("SEED_DEMO", "true").lower() == "true"
MIGRATE_LEGACY = os.environ.get("MIGRATE_LEGACY", "true").lower() == "true"

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
    template: Literal["empty", "vehicle", "library", "architecture"] = "empty"


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


_model_project: dict[str, str] = {}  # model id -> project id (models live inside their project)
_payloads: dict[str, tuple[str, dict[str, Any]]] = {}  # model id -> (commit, payloads last committed)
_derived = store.LRU(256)  # interpretation results by the texts they were made from


def _state(project_id: str) -> tuple[str | None, store.Snapshot, list[store.Commit]]:
    """Head commit, models and history of a project (404 if there is no such project)."""
    try:
        if project_id in db.deleted_projects():
            raise KeyError(project_id)
        head = store.head(project_id)
    except KeyError:
        raise HTTPException(404, "project not found") from None
    snap = store.snapshot(project_id, head)
    for mid in snap.models:
        _model_project[mid] = project_id
    return head, snap, store.history(project_id, head)


def _versions(hist: list[store.Commit], model_id: str) -> list[store.Commit]:
    """The commits that saved this model, oldest first (its versions 1..n)."""
    out: list[store.Commit] = []
    for c in hist:
        if c.model_id == model_id:
            out = [] if "Deleted" in c.trailers else [*out, c]
    return out


def _model(project_id: str, sm: store.StoredModel, head: str | None, snap: store.Snapshot, hist: list[store.Commit]) -> dict[str, Any]:
    commits = _versions(hist, sm.id) or [c for c in hist[-1:]]  # written by another API client: one version
    return {
        "id": sm.id, "project_id": project_id, "name": sm.name, "description": sm.description,
        "head_version": len(commits), "commits": commits, "head": head, "snapshot": snap,
    }


def _model_row(model_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(model_id)
    except ValueError as exc:
        raise HTTPException(404, "model not found") from exc
    known = _model_project.get(model_id)
    candidates = [known] if known else []
    for pid in candidates + [p["@id"] for p in _projects() if p["@id"] != known]:
        try:
            head, snap, hist = _state(pid)
        except HTTPException:
            continue
        if model_id in snap.models:
            return _model(pid, snap.models[model_id], head, snap, hist)
        if pid == known:
            _model_project.pop(model_id, None)
    raise HTTPException(404, "model not found")


def _valid(c: store.Commit) -> bool:
    return c.trailers.get("Valid", "true") == "true"


def _version(m: dict[str, Any], version: int | None = None) -> dict[str, Any] | None:
    """A version of the model: its text and the commit that saved it (default: the head)."""
    commits = m["commits"]
    n = version or len(commits)
    if not 1 <= n <= len(commits):
        return None
    c = commits[n - 1]
    latest = n == len(commits)
    # the head version is read in the project's current state, older ones as they were committed
    snap = m["snapshot"] if latest else store.snapshot(m["project_id"], c.id)
    sm = snap.models.get(m["id"])
    if not sm:
        return None
    return {
        "version": n, "commit": c, "source": sm.source, "message": c.message, "valid": _valid(c),
        "element_count": int(c.trailers.get("Elements") or 0), "created_at": store.created(c),
        "files": _files(snap, m["id"], m["name"], sm.source),
    }


def _files(snap: store.Snapshot, model_id: str, name: str, source: str) -> list[dict[str, str]]:
    """The model's text plus every other model of the project, so packages in one
    model can import packages from another."""
    return [{"name": f"{name}.sysml", "text": source}] + [
        {"name": f"{o.name}.sysml", "text": o.source} for o in snap.models.values() if o.id != model_id
    ]


def _files_key(files: list[dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _model_api(m: dict[str, Any], v: dict[str, Any] | None = None, full: bool = True) -> dict[str, Any]:
    commits = m["commits"]
    out = {
        "id": m["id"],
        "projectId": m["project_id"],
        "name": m["name"],
        "description": m["description"],
        "headVersion": m["head_version"],
        "createdAt": _iso(store.created(commits[0])) if commits else None,
        "updatedAt": _iso(store.created(commits[-1])) if commits else None,
    }
    last = v["commit"] if v else commits[-1] if commits else None
    if last:
        out["valid"] = _valid(last)
        out["commitId"] = last.id
    if v and full:
        d = _interpretation(v["files"])
        out.update(
            source=v["source"],
            valid=v["valid"],
            summary=d["summary"],
            diagnostics=d["diagnostics"],
            graph=d["graph"],
            elementCount=v["element_count"],
            versionMessage=v["message"],
        )
    return out


def _interpret(files: list[dict[str, str]], include_elements: bool) -> dict[str, Any]:
    """Parse the first file in its project context and keep only what that file
    declares (the other models are context for imports only)."""
    parsed = _interpreter("/parse", {"files": files, "includeElements": include_elements})
    own = files[0]["name"]
    graph = parsed["graph"]
    own_nodes = [n for n in graph["nodes"] if (n.get("loc") or {}).get("file") in (own, None)]
    ids = {n["id"] for n in own_nodes}

    def keep_end(x: str) -> bool:
        return x in ids or x.split("::__")[0] in ids

    graph = {"nodes": own_nodes, "edges": [e for e in graph["edges"] if keep_end(e["source"]) and keep_end(e["target"])]}
    roots = {n["id"] for n in own_nodes if n["owner"] is None}
    elements = store.own_elements(parsed.get("elements") or [], roots)
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
    res = {
        "valid": not any(d["severity"] == "error" for d in diagnostics),
        "diagnostics": diagnostics,
        "graph": graph,
        "summary": summary,
        "elements": elements,
        "elapsedMs": parsed.get("elapsedMs"),
    }
    _derived.put(_files_key(files), {k: v for k, v in res.items() if k != "elements"})
    return res


def _interpretation(files: list[dict[str, str]]) -> dict[str, Any]:
    """Diagnostics, diagram graph and summary of the first file (cached by content)."""
    return _derived.get(_files_key(files)) or _interpret(files, include_elements=False)


def _stored_payloads(project_id: str, model_id: str, c: store.Commit | None) -> dict[str, Any]:
    """The API payloads of a model as committed in ``c`` (re-derived from its text
    when this process did not make that commit)."""
    if not c:
        return {}
    hit = _payloads.get(model_id)
    if hit and hit[0] == c.id:
        return hit[1]
    snap = store.snapshot(project_id, c.id)
    sm = snap.models.get(model_id)
    if not sm:
        return {}
    res = _interpret(_files(snap, model_id, sm.name, sm.source), include_elements=True)
    return store.to_api(res["elements"], project_id, model_id, sm.name, sm.description, sm.source)


def _write(
    project_id: str, model_id: str, name: str, description: str, source: str, elements: list[dict[str, Any]],
    message: str, valid: bool, authored: str | None = None,
) -> int:
    """Commit a version of a model to the SysML v2 API server: only the elements that
    changed since the model's previous version. Returns the new version number."""
    with store.lock(project_id):
        head, snap, hist = _state(project_id)
        if model_id not in snap.models and any(o.name == name for o in snap.models.values()):
            raise HTTPException(409, f"model '{name}' already exists in this project")
        previous = _versions(hist, model_id)
        after = store.to_api(elements, project_id, model_id, name, description, source)
        before = _stored_payloads(project_id, model_id, previous[-1] if previous else None)
        trailers = {
            "Model-Id": model_id, "Model-Name": name, "Valid": str(valid).lower(), "Elements": len(elements), "Authored": authored,
        }
        description_ = store.describe(message, trailers)
        try:
            c = store.commit(project_id, head, store.changes(before, after), description_)
        except store.StoreError:
            # refused, e.g. for a reference into another model whose last save did not parse
            # (so its elements are not stored): drop references the project cannot resolve
            after = store.prune(after, store.element_ids(project_id, head))
            c = store.commit(project_id, head, store.changes(before, after), description_)
        models = {**snap.models, model_id: store.StoredModel(model_id, name, description, source)}
        store.remember(project_id, c.id, store.Snapshot(c.id, models))
        _payloads[model_id] = (c.id, after)
        _model_project[model_id] = project_id
        return len(previous) + 1


def _save_version(m: dict[str, Any], source: str, message: str, res: dict[str, Any] | None = None) -> int:
    """Interpret the text in its project context and commit it as the model's next version."""
    res = res or _interpret(_files(m["snapshot"], m["id"], m["name"], source), include_elements=True)
    return _write(m["project_id"], m["id"], m["name"], m["description"], source, res["elements"], message, res["valid"])


def _new_model(project_id: str, name: str, description: str) -> dict[str, Any]:
    snap = _state(project_id)[1]
    return {"id": str(uuid.uuid4()), "project_id": project_id, "name": name, "description": description, "snapshot": snap}


def _migrate_version(project_id: str, model_id: str, name: str, description: str, source: str,
                     elements: list[dict[str, Any]], message: str, valid: bool, authored: str) -> None:
    _write(project_id, model_id, name, description, source, elements, message, valid, authored)


# ------------------------------------------------------------------ app
# (seed key, project, description, [(model, example file, description)]); models
# are saved in this order, so a model can import the packages of the ones before it
DEMOS = [
    (
        "demo", "Demo", "Example vehicle model calling REST analyses during execution",
        [("Vehicle", "vehicle.sysml", "Vehicle architecture, requirements, behavior and analyses")],
    ),
    (
        "product-line", "Product Line", "Component library, system architectures and configured system instances",
        [
            ("ComponentLibrary", "component_library.sysml", "Component types (#Component part defs) and their catalog instances"),
            ("Architectures", "architectures.sysml", "System architectures (#SystemArchitecture) and the system instances configured from the catalog"),
        ],
    ),
]


def _seed_demo() -> None:
    for key, project, description, models in DEMOS:
        if not all((EXAMPLES_DIR / f).exists() for _, f, _ in models):
            continue
        with db.pool.connection() as c:
            if not c.execute("INSERT INTO models.seed (name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING name", (key,)).fetchone():
                continue  # seeded before (and maybe deleted since)
        if any(p.get("name") == project for p in _projects()):
            continue
        pid = store.create_project(project, description)["@id"]
        for name, f, desc in models:
            m = _new_model(pid, name, desc)
            for attempt in range(60):  # the interpreter may still be starting
                try:
                    _save_version(m, (EXAMPLES_DIR / f).read_text(), "Seed example")
                    break
                except HTTPException as exc:
                    log.info("waiting for interpreter to seed %s (%s)", project, exc.detail)
                    time.sleep(2)
        log.info("seeded demo project %s", project)


def _startup() -> None:
    for attempt in range(150):  # the SysML v2 API server takes a while to start
        try:
            store.ping()
            break
        except store.StoreError as exc:
            if attempt % 10 == 0:
                log.info("waiting for the SysML v2 API server (%s)", exc)
            time.sleep(2)
    try:
        if MIGRATE_LEGACY:
            legacy.migrate(_migrate_version, _remove_project)
        if SEED_DEMO:
            _seed_demo()
    except Exception:
        log.exception("startup tasks failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    threading.Thread(target=_startup, daemon=True).start()
    yield
    db.pool.close()


app = FastAPI(
    title="SysML v2 Model Service",
    version="0.2.0",
    description="Projects and versioned SysML v2 models, stored in a SysML v2 API server; interpretation and runs.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(store.StoreError)
async def _store_error(_: Any, exc: store.StoreError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    with db.pool.connection() as c:
        c.execute("SELECT 1")
    store.ping()
    return {"status": "ok", "sysmlApi": store.API_URL}


# ------------------------------------------------------------------ projects
def _project_api(p: dict[str, Any], model_count: int) -> dict[str, Any]:
    return {"id": p["@id"], "name": p.get("name") or "", "description": p.get("description") or "",
            "modelCount": model_count, "createdAt": p.get("created")}


_project_names = threading.Lock()


def _projects() -> list[dict[str, Any]]:
    deleted = db.deleted_projects()
    return [p for p in store.projects() if p["@id"] not in deleted]


@app.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    """The projects of the SysML v2 API server."""
    out = []
    for p in sorted(_projects(), key=lambda p: p.get("created") or ""):
        try:
            out.append(_project_api(p, len(_state(p["@id"])[1].models)))
        except HTTPException:
            continue  # deleted meanwhile
    return out


@app.post("/projects", status_code=201)
def create_project(p: ProjectIn) -> dict[str, Any]:
    with _project_names:
        if any(x.get("name") == p.name for x in _projects()):
            raise HTTPException(409, f"project '{p.name}' already exists")
        return _project_api(store.create_project(p.name, p.description), 0)


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID) -> None:
    pid = str(project_id)
    try:
        models = list(_state(pid)[1].models)
    except HTTPException:
        return
    _remove_project(pid)
    with db.pool.connection() as c:
        c.execute("DELETE FROM models.run WHERE model_id = ANY(%s)", (models,))
    for mid in models:
        _model_project.pop(mid, None)
        _payloads.pop(mid, None)


def _remove_project(project_id: str) -> None:
    """Delete a project from the API server or, where it refuses, commit the removal of
    its models and hide the project (its history stays in the API server)."""
    if store.delete_project(project_id):
        return
    for mid in list(_state(project_id)[1].models):
        _delete_model(project_id, mid)
    db.mark_deleted(project_id)


@app.get("/projects/{project_id}/models")
def list_models(project_id: uuid.UUID) -> list[dict[str, Any]]:
    pid = str(project_id)
    head, snap, hist = _state(pid)
    return [_model_api(_model(pid, sm, head, snap, hist), full=False) for sm in snap.models.values()]


def _library_imports(snap: store.Snapshot) -> str:
    """Imports of the project's packages that declare the #SystemArchitecture tag."""
    sources = [sm.source for sm in snap.models.values()]
    pkgs = [m.group(1) for s in sources if _TAG_RE.search(s) for m in [_TOP_PKG_RE.search(s)] if m]
    if not pkgs:
        return "    // No component library in this project yet: create one from the 'Component library' template.\n"
    return "".join(f"    private import {p}::*;\n" for p in dict.fromkeys(pkgs))


@app.post("/projects/{project_id}/models", status_code=201)
def create_model(project_id: uuid.UUID, body: ModelIn) -> dict[str, Any]:
    m = _new_model(str(project_id), body.name, body.description)
    if any(o.name == body.name for o in m["snapshot"].models.values()):
        raise HTTPException(409, f"model '{body.name}' already exists in this project")
    pkg = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in body.name)
    source = body.source
    if source is None:
        example = EXAMPLES_DIR / "vehicle.sysml"
        if body.template == "vehicle" and example.exists():
            # rename the package so it does not clash with other models of the project
            source = example.read_text().replace("package VehicleModel {", f"package {pkg} {{", 1)
        elif body.template == "library":
            source = LIBRARY_TEMPLATE.format(name=pkg)
        elif body.template == "architecture":
            imports = _library_imports(m["snapshot"])
            source = ARCHITECTURE_TEMPLATE.format(name=pkg, imports=imports)
            if "import" not in imports:  # nothing declares the tag yet
                source = source.replace("#SystemArchitecture part def", "part def")
        else:
            source = EMPTY_TEMPLATE.format(name=pkg)
    _save_version(m, source, "Created")
    m = _model_row(m["id"])
    return _model_api(m, _version(m))


# ------------------------------------------------------------------ models
@app.get("/models/{model_id}")
def get_model(model_id: str, version: int | None = None) -> dict[str, Any]:
    m = _model_row(model_id)
    v = _version(m, version)
    if version and not v:
        raise HTTPException(404, f"model has no version {version}")
    return _model_api(m, v)


@app.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str) -> None:
    """Commit the removal of the model's elements, its text and its root Namespace."""
    m = _model_row(model_id)
    _delete_model(m["project_id"], model_id)
    with db.pool.connection() as c:
        c.execute("DELETE FROM models.run WHERE model_id=%s", (model_id,))


def _delete_model(pid: str, model_id: str) -> None:
    with store.lock(pid):
        head, snap, hist = _state(pid)
        sm = snap.models.get(model_id)
        if sm:
            previous = _versions(hist, model_id)
            before = _stored_payloads(pid, model_id, previous[-1] if previous else None)
            before = before or store.to_api([], pid, model_id, sm.name, sm.description, "")
            trailers = {"Model-Id": model_id, "Model-Name": sm.name, "Deleted": "true"}
            c = store.commit(pid, head, store.changes(before, {}), store.describe(f"Delete model {sm.name}", trailers))
            models = {k: v for k, v in snap.models.items() if k != model_id}
            store.remember(pid, c.id, store.Snapshot(c.id, models))
    _payloads.pop(model_id, None)
    _model_project.pop(model_id, None)


@app.put("/models/{model_id}/source")
def save_source(model_id: str, body: SourceIn) -> dict[str, Any]:
    """Save a new version of the SysML v2 text (interpreted and committed)."""
    m = _model_row(model_id)
    v = _version(m)
    if not (v and v["source"] == body.source):
        _save_version(m, body.source, body.message)
        m = _model_row(model_id)
        v = _version(m)
    return _model_api(m, v)


@app.post("/models/{model_id}/preview")
def preview(model_id: str, body: SourceIn) -> dict[str, Any]:
    """Interpret unsaved text (live editing): diagnostics + diagram graph, nothing stored."""
    m = _model_row(model_id)
    res = _interpret(_files(m["snapshot"], m["id"], m["name"], body.source), include_elements=False)
    res.pop("elements", None)
    return res


@app.get("/models/{model_id}/versions")
def list_versions(model_id: str) -> list[dict[str, Any]]:
    m = _model_row(model_id)
    return [
        {"version": n, "message": c.message, "valid": _valid(c), "elementCount": int(c.trailers.get("Elements") or 0),
         "createdAt": _iso(store.created(c)), "commitId": c.id}
        for n, c in reversed(list(enumerate(m["commits"], 1)))
    ]


@app.get("/models/{model_id}/elements")
def list_elements(
    model_id: str,
    metatype: str | None = None,
    q: str | None = Query(None, description="substring of the qualified name"),
    version: int | None = None,
    limit: int = Query(500, le=5000),
) -> list[dict[str, Any]]:
    """The model's elements as the SysML v2 API server holds them at a version (default head)."""
    m = _model_row(model_id)
    v = _version(m, version)
    if not v:
        return []
    ids = set(_stored_payloads(m["project_id"], model_id, v["commit"]))
    els = [e for e in store.elements(m["project_id"], v["commit"].id, metatype) if e.get("@id") in ids]
    if q:
        els = [e for e in els if q.lower() in (e.get("qualifiedName") or "").lower()]
    els.sort(key=lambda e: (e.get("qualifiedName") is None, e.get("qualifiedName") or ""))
    return els[:limit]


@app.get("/models/{model_id}/requirements")
def requirements(model_id: str) -> list[dict[str, Any]]:
    """Requirements with their text, constraints, subject, satisfying elements and
    the latest verdict from a satisfy / requirement run."""
    m = _model_row(model_id)
    v = _version(m)
    if not v:
        return []
    graph = _interpretation(v["files"])["graph"]
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
    head = _version(m)
    if not head:
        raise HTTPException(409, "model has no version yet")
    run_id = str(uuid.uuid4())
    files = head["files"]
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
    specializes: str | None = None
    abstract: bool = False
    doc: str = ""
    attributes: list[FeatureIn] = []
    ports: list[FeatureIn] = []


class InstanceIn(BaseModel):
    component: str = Field(description="qualified name of the component part def")
    name: str
    doc: str = ""
    values: dict[str, str] = Field(default={}, description="attribute name -> SysML expression, e.g. '140 [SI::kg]'")
    package: str | None = Field(None, description="default: the Catalog package next to the component")
    replace: str | None = Field(None, description="qualified name of an existing instance to rewrite")


class ConfigurationIn(BaseModel):
    architecture: str = Field(description="qualified name of the #SystemArchitecture part def")
    name: str
    doc: str = ""
    assignments: dict[str, str | None] = Field(default={}, description="slot name -> qualified name of a component instance")
    package: str | None = Field(None, description="default: the Configurations package next to the architecture")
    replace: str | None = Field(None, description="qualified name of an existing system instance to rewrite")


def _project_heads(project_id: uuid.UUID) -> list[dict[str, Any]]:
    snap = _state(str(project_id))[1]
    return [{"id": sm.id, "name": sm.name, "source": sm.source} for sm in snap.models.values()]


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
    cat = catalog.build(parsed["graph"], file_models)
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
    for key in ("components", "instances", "architectures", "configurations"):
        out |= {i["qualifiedName"] for i in cat[key]}
    return out


def _commit(model_id: str, source: str, message: str) -> dict[str, Any]:
    """Save edited model text, refusing an edit that adds errors (in this model
    or in a model that refers to it)."""
    m = _model_row(model_id)
    head = _version(m)
    before = sum(1 for d in (_interpretation(head["files"])["diagnostics"] if head else []) if d["severity"] == "error")
    res = _interpret(_files(m["snapshot"], m["id"], m["name"], source), include_elements=True)
    errors = [d for d in res["diagnostics"] if d["severity"] == "error"]
    if len(errors) > before:
        detail = "; ".join(f"line {d['line']}: {d['message']}" for d in errors[:3])
        raise HTTPException(422, f"The change would make '{m['name']}' invalid, so it was not saved. {detail}")
    version = _save_version(m, source, message, res)
    return {"modelId": model_id, "modelName": m["name"], "version": version, "valid": res["valid"]}


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
    """Declare a new ``#Component part def``."""
    cat, sources = _project_catalog(project_id)
    tag = cat["tags"].get(catalog.COMPONENT_TAG)
    if not tag:
        raise HTTPException(409, "No 'metadata def Component' in this project: create a model from the component library template first")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, body.package, None)
    try:
        block = catalog.component_text(
            body.name, catalog.ref(tag, pkg_qn), body.specializes, [f.model_dump() for f in body.attributes],
            [f.model_dump() for f in body.ports], body.doc, body.abstract,
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
    if comp["isAbstract"]:
        raise HTTPException(422, f"'{comp['name']}' is abstract: instantiate one of its specializations")
    attrs = {a["name"] for a in comp["attributes"]}
    unknown = set(body.values) - attrs
    if unknown:
        raise HTTPException(422, f"{comp['name']} has no attribute {', '.join(sorted(unknown))}")
    replace = _find(cat["instances"], body.replace, "instance") if body.replace else None
    package = body.package or _default_package(cat, comp, "Catalog")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, package, replace)
    try:
        block = catalog.instance_text(body.name, catalog.ref(comp["qualifiedName"], pkg_qn), body.values, body.doc)
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
    replace = _find(cat["configurations"], body.replace, "system instance") if body.replace else None
    package = body.package or _default_package(cat, arch, "Configurations")
    model_id, pkg_qn, apply = _place(cat, sources, body.name, package, replace)
    ordered = {s: catalog.ref(body.assignments[s], pkg_qn) for s in slots if body.assignments.get(s)}
    try:
        block = catalog.configuration_text(body.name, catalog.ref(arch["qualifiedName"], pkg_qn), ordered, body.doc)
        source = apply(block)
    except catalog.CatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    verb = "Update" if replace else "Configure"
    return _commit(model_id, source, f"{verb} system {body.name} : {arch['name']}") | {
        "qualifiedName": f"{pkg_qn}::{body.name}", "text": "\n".join(block),
    }


@app.delete("/projects/{project_id}/catalog/elements")
def delete_catalog_element(project_id: uuid.UUID, qualifiedName: str) -> dict[str, Any]:
    """Remove a component, instance or system instance from its model's text.
    Refused while something else still refers to it."""
    cat, sources = _project_catalog(project_id)
    items = cat["components"] + cat["instances"] + cat["configurations"]
    item = _find(items, qualifiedName, "element")
    source = catalog.remove_element(sources[item["modelId"]], item["loc"])
    return _commit(item["modelId"], source, f"Remove {item['name']}") | {"qualifiedName": qualifiedName}
