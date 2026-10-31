"""Analysis service: a registry of analyses callable over REST.

Three kinds of analysis are supported:

* ``builtin``  - Python functions in ``builtins.py`` hosted by this service
* ``formula``  - arithmetic formulas stored in the registry (safe evaluator)
* ``external`` - any HTTP endpoint; this service forwards the call to it

The interpreter service exposes every enabled analysis to OpenSysML as an
external tool, so SysML v2 calcs/actions annotated with
``metadata ToolExecution { toolName = "<name>"; }`` invoke
``POST /analyses/<name>/invoke`` while the model executes. Every invocation is
logged with the run id of the model execution that made it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db, formula
from .builtins import REGISTRY, to_si

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("analysis")

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class Variable(BaseModel):
    name: str
    unit: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("variable names must be identifiers")
        return v


class AnalysisIn(BaseModel):
    name: str
    kind: Literal["external", "formula"]
    description: str = ""
    version: str = "1"
    endpointUrl: str | None = None
    headers: dict[str, str] = {}
    formulas: dict[str, str] = {}
    inputs: list[Variable] = Field(default_factory=list)
    outputs: list[Variable] = Field(min_length=1)
    timeoutSeconds: float = Field(30, gt=0, le=600)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("analysis names must be identifiers (letters, digits, _)")
        return v

    @model_validator(mode="after")
    def _check(self) -> "AnalysisIn":
        names = [v.name for v in self.inputs] + [v.name for v in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError("input and output names must be distinct")
        if self.kind == "external":
            if not self.endpointUrl or not re.match(r"^https?://", self.endpointUrl):
                raise ValueError("external analyses need an http(s) endpointUrl")
        if self.kind == "formula":
            missing = [o.name for o in self.outputs if o.name not in self.formulas]
            if missing:
                raise ValueError(f"no formula for outputs: {missing}")
            scope = {v.name for v in self.inputs}
            for out, expr in self.formulas.items():
                formula.validate(expr, scope)
        return self


class InvokeIn(BaseModel):
    """OpenSysML's tool request object, or a plain REST call."""

    toolName: str | None = None
    uri: str | None = None
    inputs: dict[str, Any] = {}
    runId: str | None = None


def _row_to_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r["name"],
        "kind": r["kind"],
        "description": r["description"],
        "version": r["version"],
        "endpointUrl": r["endpoint_url"],
        "headers": {k: "***" for k in (r["headers"] or {})},
        "formulas": r["formulas"],
        "inputs": r["inputs"],
        "outputs": r["outputs"],
        "timeoutSeconds": r["timeout_s"],
        "enabled": r["enabled"],
        "createdAt": r["created_at"].isoformat(),
        "updatedAt": r["updated_at"].isoformat(),
    }


def _upsert_builtins() -> None:
    with db.pool.connection() as c:
        for b in REGISTRY.values():
            c.execute(
                """INSERT INTO analysis.analysis (name, kind, description, version, inputs, outputs)
                   VALUES (%s, 'builtin', %s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET kind='builtin', description=EXCLUDED.description,
                     version=EXCLUDED.version, inputs=EXCLUDED.inputs, outputs=EXCLUDED.outputs, updated_at=now()""",
                (b.name, b.description, b.version, Jsonb(b.inputs), Jsonb(b.outputs)),
            )


def _seed() -> None:
    path = os.environ.get("SEED_ANALYSES")
    if not path or not Path(path).exists():
        return
    for item in json.loads(Path(path).read_text()):
        try:
            a = AnalysisIn(**item)
        except Exception as exc:
            log.warning("skipping seed analysis %s: %s", item.get("name"), exc)
            continue
        _insert(a, on_conflict_nothing=True)


def _insert(a: AnalysisIn, on_conflict_nothing: bool = False) -> None:
    conflict = "ON CONFLICT (name) DO NOTHING" if on_conflict_nothing else ""
    with db.pool.connection() as c:
        c.execute(
            f"""INSERT INTO analysis.analysis
                (name, kind, description, version, endpoint_url, headers, formulas, inputs, outputs, timeout_s, enabled)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) {conflict}""",
            (
                a.name, a.kind, a.description, a.version, a.endpointUrl, Jsonb(a.headers), Jsonb(a.formulas),
                Jsonb([v.model_dump(exclude_none=True) for v in a.inputs]),
                Jsonb([v.model_dump(exclude_none=True) for v in a.outputs]),
                a.timeoutSeconds, a.enabled,
            ),
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    _upsert_builtins()
    _seed()
    yield
    db.pool.close()


app = FastAPI(
    title="SysML v2 Analysis Service",
    version="0.1.0",
    description="Registry and REST execution of analyses called by SysML v2 models during execution.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> dict[str, str]:
    with db.pool.connection() as c:
        c.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/analyses")
def list_analyses(enabled: bool | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM analysis.analysis"
    params: tuple = ()
    if enabled is not None:
        q += " WHERE enabled = %s"
        params = (enabled,)
    with db.pool.connection() as c:
        rows = c.execute(q + " ORDER BY kind, name", params).fetchall()
    return [_row_to_api(r) for r in rows]


def _get(name: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        r = c.execute("SELECT * FROM analysis.analysis WHERE name = %s", (name,)).fetchone()
    if not r:
        raise HTTPException(404, f"analysis '{name}' is not registered")
    return r


@app.get("/analyses/{name}")
def get_analysis(name: str) -> dict[str, Any]:
    return _row_to_api(_get(name))


@app.post("/analyses", status_code=201)
def create_analysis(a: AnalysisIn) -> dict[str, Any]:
    if a.name in REGISTRY:
        raise HTTPException(409, f"'{a.name}' is a built-in analysis")
    with db.pool.connection() as c:
        if c.execute("SELECT 1 FROM analysis.analysis WHERE name=%s", (a.name,)).fetchone():
            raise HTTPException(409, f"analysis '{a.name}' already exists")
    _insert(a)
    return get_analysis(a.name)


@app.put("/analyses/{name}")
def update_analysis(name: str, a: AnalysisIn) -> dict[str, Any]:
    row = _get(name)
    if row["kind"] == "builtin":
        raise HTTPException(409, "built-in analyses are defined in code")
    if a.name != name:
        raise HTTPException(422, "the name in the body must match the path")
    headers = a.headers or row["headers"]  # keep stored secrets when the UI sends none
    with db.pool.connection() as c:
        c.execute(
            """UPDATE analysis.analysis SET kind=%s, description=%s, version=%s, endpoint_url=%s, headers=%s,
               formulas=%s, inputs=%s, outputs=%s, timeout_s=%s, enabled=%s, updated_at=now() WHERE name=%s""",
            (
                a.kind, a.description, a.version, a.endpointUrl, Jsonb(headers), Jsonb(a.formulas),
                Jsonb([v.model_dump(exclude_none=True) for v in a.inputs]),
                Jsonb([v.model_dump(exclude_none=True) for v in a.outputs]),
                a.timeoutSeconds, a.enabled, name,
            ),
        )
    return get_analysis(name)


@app.patch("/analyses/{name}")
def toggle_analysis(name: str, enabled: bool = Query(...)) -> dict[str, Any]:
    _get(name)
    with db.pool.connection() as c:
        c.execute("UPDATE analysis.analysis SET enabled=%s, updated_at=now() WHERE name=%s", (enabled, name))
    return get_analysis(name)


@app.delete("/analyses/{name}", status_code=204)
def delete_analysis(name: str) -> None:
    row = _get(name)
    if row["kind"] == "builtin":
        raise HTTPException(409, "built-in analyses cannot be deleted (disable them instead)")
    with db.pool.connection() as c:
        c.execute("DELETE FROM analysis.analysis WHERE name=%s", (name,))


# ------------------------------------------------------------------ invocation
def _split(v: Any) -> tuple[Any, str | None]:
    if isinstance(v, dict) and "value" in v:
        return v["value"], v.get("unit")
    return v, None


def _out(value: Any, unit: str | None, declared: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, tuple):
        value, unit = value
    if isinstance(value, dict) and "value" in value:
        value, unit = value["value"], value.get("unit", unit)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"output '{declared['name']}' is not finite")
    o: dict[str, Any] = {"value": value}
    u = unit or declared.get("unit")
    if u and isinstance(value, (int, float)) and not isinstance(value, bool):
        o["unit"] = u
    return o


def _run_local(row: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for var in row["inputs"]:
        raw, unit = _split(inputs[var["name"]])
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            raw = to_si(float(raw), unit, var.get("unit"))
        values[var["name"]] = raw
    if row["kind"] == "builtin":
        b = REGISTRY.get(row["name"])
        if not b:
            raise HTTPException(500, f"built-in '{row['name']}' has no implementation in this build")
        return b.fn(values)
    return {name: formula.evaluate(expr, values) for name, expr in row["formulas"].items()}


def _run_external(row: dict[str, Any], body: InvokeIn) -> dict[str, Any]:
    payload = {"toolName": row["name"], "uri": body.uri, "inputs": body.inputs, "runId": body.runId}
    try:
        r = httpx.post(row["endpoint_url"], json=payload, headers=row["headers"] or {}, timeout=row["timeout_s"])
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"external analysis unreachable: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(502, f"external analysis returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        reply = r.json()
    except ValueError as exc:
        raise HTTPException(502, "external analysis did not return JSON") from exc
    if isinstance(reply, dict) and reply.get("error"):
        raise HTTPException(502, f"external analysis error: {reply['error']}")
    if isinstance(reply, dict) and isinstance(reply.get("outputs"), dict):
        return reply["outputs"]
    if isinstance(reply, dict):
        return reply
    raise HTTPException(502, "external analysis reply must be a JSON object")


def _log(name: str, body: InvokeIn, response: Any, status: str, error: str | None, ms: int) -> None:
    try:
        with db.pool.connection() as c:
            c.execute(
                """INSERT INTO analysis.invocation (analysis_name, run_id, uri, request, response, status, error, duration_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (name, body.runId, body.uri, Jsonb(body.model_dump()), Jsonb(response) if response is not None else None,
                 status, error, ms),
            )
    except Exception as exc:  # logging must never fail the analysis
        log.warning("could not log invocation: %s", exc)


@app.post("/analyses/{name}/invoke")
def invoke(name: str, body: InvokeIn) -> dict[str, Any]:
    """Run an analysis. Accepts OpenSysML's tool request object
    ``{"toolName", "uri", "inputs": {var: {"value", "unit"}}}`` or plain values
    ``{"inputs": {var: 3.2}}``; returns ``{"outputs": {var: {"value", "unit"}}}``."""
    row = _get(name)
    start = time.monotonic()
    try:
        if not row["enabled"]:
            raise HTTPException(409, f"analysis '{name}' is disabled")
        if row["kind"] != "external":
            missing = [v["name"] for v in row["inputs"] if v["name"] not in body.inputs]
            if missing:
                raise HTTPException(422, f"missing inputs: {', '.join(missing)}")
        try:
            raw = _run_external(row, body) if row["kind"] == "external" else _run_local(row, body.inputs)
        except HTTPException:
            raise
        except (ValueError, ArithmeticError, TypeError, KeyError, formula.FormulaError) as exc:
            raise HTTPException(422, f"analysis '{name}' failed: {exc}") from exc
        outputs: dict[str, Any] = {}
        for var in row["outputs"]:
            if var["name"] not in raw:
                raise HTTPException(502, f"analysis '{name}' did not return output '{var['name']}'")
            try:
                outputs[var["name"]] = _out(raw[var["name"]], None, var)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        result = {"outputs": outputs}
        _log(name, body, result, "ok", None, int((time.monotonic() - start) * 1000))
        return result
    except HTTPException as exc:
        _log(name, body, None, "error", str(exc.detail), int((time.monotonic() - start) * 1000))
        raise


@app.get("/invocations")
def invocations(
    runId: str | None = None, analysis: str | None = None, limit: int = Query(100, le=1000)
) -> list[dict[str, Any]]:
    q = "SELECT * FROM analysis.invocation WHERE TRUE"
    p: list[Any] = []
    if runId:
        q += " AND run_id = %s"
        p.append(runId)
    if analysis:
        q += " AND analysis_name = %s"
        p.append(analysis)
    q += " ORDER BY id DESC LIMIT %s"
    p.append(limit)
    with db.pool.connection() as c:
        rows = c.execute(q, p).fetchall()
    return [
        {
            "id": r["id"],
            "analysis": r["analysis_name"],
            "runId": r["run_id"],
            "uri": r["uri"],
            "inputs": (r["request"] or {}).get("inputs"),
            "outputs": (r["response"] or {}).get("outputs"),
            "status": r["status"],
            "error": r["error"],
            "durationMs": r["duration_ms"],
            "createdAt": r["created_at"].isoformat(),
        }
        for r in rows
    ]
