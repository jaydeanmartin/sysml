"""Interpreter service: SysML v2 parsing, rendering and execution via OpenSysML."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import opensysml as osys
from . import tools
from .graph import SourceFile, build_graph, summarize

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("interpreter")

app = FastAPI(
    title="SysML v2 Interpreter Service",
    version="0.1.0",
    description="Parses, renders and executes SysML v2 textual models with OpenMBEE OpenSysML.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MAX_TRACE = 5000


class FileIn(BaseModel):
    name: str = "model.sysml"
    text: str


class ParseRequest(BaseModel):
    files: list[FileIn] = Field(min_length=1)
    includeElements: bool = False


class ExecuteRequest(BaseModel):
    files: list[FileIn] = Field(min_length=1)
    kind: Literal[
        "calc", "analysis", "requirement", "constraint", "satisfy", "action", "state", "evaluate", "validate"
    ]
    target: str | None = None
    args: list[str] = []
    subject: str | None = None
    expression: str | None = None
    instantiate: list[str] = []
    advance: float | None = None
    seed: int | None = None
    trace: bool = True
    runId: str | None = None


class RenderRequest(BaseModel):
    files: list[FileIn] = Field(min_length=1)
    view: str = Field(description="A declared view, or '#kind:Qualified::Name' e.g. '#interconnection:Pkg::Vehicle'")
    form: Literal["mermaid", "dot", "plantuml", "d2", "text", "markdown"] = "mermaid"


def _files(req_files: list[FileIn]) -> list[osys.ModelFile]:
    return [osys.ModelFile(f.name, f.text) for f in req_files]


@app.get("/health")
def health() -> dict[str, Any]:
    path = osys.binary_path()
    return {"status": "ok" if path else "degraded", "opensysml": osys.version() if path else None}


@app.get("/engines")
def engines() -> dict[str, Any]:
    with osys.workspace([]) as (ws, _), tools.tools_dir() as (tdir, names):
        res = osys.run(["-engines"], cwd=ws, env=tools.tool_env(tdir), expect_json=False)
    return {"tools": names, "table": res.stdout.splitlines()}


@app.post("/parse")
def parse(req: ParseRequest) -> dict[str, Any]:
    """Validate the model and return diagnostics plus the diagram graph."""
    files = _files(req.files)
    with osys.workspace(files) as (ws, names):
        val = osys.run(["-validate", "-json", "."], cwd=ws)
        diags = osys.map_diagnostics((val.report or {}).get("diagnostics"), names)
        if not val.report:
            diags += osys.stderr_diagnostics(val.stderr)
        has_error = any(d["severity"] == "error" for d in diags)

        elements: list[dict[str, Any]] = []
        graph: dict[str, Any] = {"nodes": [], "edges": []}
        conv_error = None
        conv = osys.run(["-convert", "api-json", "-o", "api.json", *names], cwd=ws, expect_json=False)
        api_file = ws / "api.json"
        if conv.returncode == 0 and api_file.exists():
            elements = json.loads(api_file.read_text())
            graph = build_graph(elements, [SourceFile(names.get(n, n), (ws / n).read_text()) for n in names])
        else:
            conv_error = "\n".join(
                l for l in (conv.stderr or conv.stdout).splitlines() if l.strip() and not l.startswith("note:")
            )[-2000:] or "conversion failed"
    return {
        "ok": not has_error and conv_error is None,
        "diagnostics": diags,
        "graph": graph,
        "summary": summarize(graph),
        "elementCount": len(elements),
        "elements": elements if req.includeElements else None,
        "convertError": conv_error,
        "elapsedMs": val.elapsed_ms + conv.elapsed_ms,
    }


def _call(target: str, args: list[str]) -> str:
    if "(" in target or not args:
        return target
    return f"{target}({', '.join(args)})"


def _build_args(req: ExecuteRequest) -> list[str]:
    a: list[str] = [] if req.kind == "evaluate" else ["-json"]
    if req.trace and req.kind in ("action", "state", "calc", "analysis"):
        a.append("-trace")
    k = req.kind
    if k in ("calc", "analysis", "requirement", "constraint", "action", "state") and not req.target:
        raise HTTPException(422, f"'{k}' needs a target")
    for inst in req.instantiate:
        a += ["-instantiate", inst]
    if k == "validate":
        a.append("-validate")
    elif k == "evaluate":
        exprs = [e.strip() for e in (req.expression or "").split("\n") if e.strip()]
        if not exprs:
            raise HTTPException(422, "'evaluate' needs an expression")
        for e in exprs:
            a += ["-e", e]
    elif k == "satisfy":
        a.append(f"-satisfy={req.target}" if req.target else "-satisfy")
    elif k == "calc":
        a += ["-calc", _call(req.target, req.args)]
    else:
        call = _call(req.target, req.args) if k in ("analysis", "requirement", "constraint") else req.target
        if req.subject:
            call = f"{call} {req.subject}"
        a += [f"-{k}", call]
    if k in ("action", "state") and req.advance is not None:
        a += ["-advance", str(req.advance)]
    if req.seed is not None:
        a += ["-seed", str(req.seed)]
    a.append(".")
    return a


_STEP = re.compile(r"step (\d+): token (\d+)@(\S+)")
_ENTER = re.compile(r"^(enter|exit): (\S+)")
_TRANS = re.compile(r"^transition: (\S+) -> (\S+)(?: \(event: (.*)\))?")


def parse_trace(lines: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Split OpenSysML's [trace] lines from the report lines and structure them."""
    events: list[dict[str, Any]] = []
    rest: list[str] = []
    for raw in lines:
        if not raw.startswith("[trace]"):
            rest.append(raw)
            continue
        t = raw[len("[trace]") :].strip()
        if m := _STEP.search(t):
            events.append({"type": "step", "step": int(m.group(1)), "token": int(m.group(2)), "node": m.group(3)})
        elif m := _TRANS.match(t):
            events.append({"type": "transition", "from": m.group(1), "to": m.group(2), "event": m.group(3)})
        elif m := _ENTER.match(t):
            events.append({"type": m.group(1), "node": m.group(2)})
        elif t.startswith("stmt ") or t.startswith("tool") or "tool" in t[:30]:
            events.append({"type": "stmt", "text": t})
        if len(events) > MAX_TRACE:
            break
    return events, rest


@app.post("/execute")
def execute(req: ExecuteRequest) -> dict[str, Any]:
    """Run a calc / analysis / requirement / action / state machine / expression.

    Analyses registered in the analysis service are exposed as OpenSysML tools,
    so calcs and actions carrying ``ToolExecution`` metadata call them over REST
    while the model executes.
    """
    run_id = req.runId or str(uuid.uuid4())
    args = _build_args(req)
    files = _files(req.files)
    with osys.workspace(files) as (ws, names), tools.tools_dir(run_id) as (tdir, tool_names):
        try:
            res = osys.run(args, cwd=ws, env=tools.tool_env(tdir))
        except osys.OpenSysMLError as exc:
            raise HTTPException(504, str(exc)) from exc
    if req.kind == "evaluate":
        report = _evaluate_report(res)
    else:
        report = res.report or {}
    checks = []
    all_trace: list[dict[str, Any]] = []
    for c in report.get("checks") or []:
        trace, lines = parse_trace(c.get("lines") or [])
        all_trace += trace
        checks.append(
            {
                "subject": c.get("subject"),
                "status": c.get("status"),
                "values": c.get("values") or [],
                "lines": lines,
                "rows": c.get("rows"),
                "standing": (c.get("plan") or {}).get("standing"),
            }
        )
    output_trace, output = parse_trace(report.get("output") or [])
    all_trace = output_trace + all_trace
    errors = list(report.get("errors") or [])
    if not report:
        errors = [l for l in (res.stderr or res.stdout).splitlines() if l.strip()][-20:]
    elif req.kind != "evaluate":
        errors += [l[6:].strip() for l in res.stderr.splitlines() if l.startswith("sysml:")]
    return {
        "runId": run_id,
        "status": report.get("status") or ("error" if res.returncode else "unknown"),
        "exit": report.get("exit", res.returncode),
        "elapsedMs": res.elapsed_ms,
        "command": ["sysml", *args],
        "checks": checks,
        "diagnostics": osys.map_diagnostics(report.get("diagnostics"), names),
        "output": output,
        "errors": errors,
        "trace": all_trace,
        "tools": tool_names,
    }


def _evaluate_report(res: osys.RunResult) -> dict[str, Any]:
    """-e has no JSON report: turn its '✓ expr / = value' lines into checks."""
    checks: list[dict[str, Any]] = []
    output: list[str] = []
    cur: dict[str, Any] | None = None
    for line in res.stdout.splitlines():
        if line.startswith("✓ ") and not line.startswith("✓ package") and "Created instance" not in line:
            cur = {"subject": line[2:].strip(), "status": "holds", "values": [], "lines": [line]}
            checks.append(cur)
        elif cur is not None and line.strip().startswith("= "):
            cur["values"].append({"name": "value", "value": line.strip()[2:]})
            cur["lines"].append(line)
        elif cur is not None and line.startswith("  "):
            cur["lines"].append(line)
        else:
            output.append(line)
    errors = [l[6:].strip() for l in res.stderr.splitlines() if l.startswith("sysml:")]
    status = "holds" if res.returncode == 0 else ("error" if errors else "fails")
    return {"status": status, "exit": res.returncode, "checks": checks, "output": output, "errors": errors}


@app.post("/render")
def render(req: RenderRequest) -> dict[str, Any]:
    """OpenSysML's own view rendering (mermaid / dot / plantuml / d2 / text)."""
    with osys.workspace(_files(req.files)) as (ws, _):
        res = osys.run(
            ["-render", req.view, "-render-form", req.form, "-o", "view.out", "."], cwd=ws, expect_json=False
        )
        out = ws / "view.out"
        text = out.read_text() if out.exists() else None
    if text is None:
        raise HTTPException(422, (res.stderr or res.stdout).strip()[-2000:] or "render failed")
    return {"form": req.form, "view": req.view, "text": text}
