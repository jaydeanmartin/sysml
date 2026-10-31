"""Expose the analysis registry to OpenSysML as external tools.

OpenSysML performs any calc or action annotated with
``metadata AnalysisTooling::ToolExecution { toolName = "..."; }`` by running
the program a *tool manifest* registers under that name (the directory named
by ``OPENSYSML_TOOLS``).

For each analysis in the analysis service's registry this module writes one
manifest whose executable is ``bridge.py``: OpenSysML sends the bridge one JSON
request on stdin, the bridge POSTs it to
``{ANALYSIS_SERVICE_URL}/analyses/{toolName}/invoke`` and writes the reply back.
That is how a model calls REST analyses *during* its own execution.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

ANALYSIS_SERVICE_URL = os.environ.get("ANALYSIS_SERVICE_URL", "http://analysis:8003")
TOOLS_BASE = Path(os.environ.get("SYSML_TOOLS_BASE", "/tmp/sysml-tools"))
BRIDGE = Path(__file__).with_name("bridge.py").resolve()
TOOL_TIMEOUT = os.environ.get("OPENSYSML_TOOL_TIMEOUT", "60s")


def fetch_registry() -> list[dict[str, Any]]:
    try:
        r = httpx.get(f"{ANALYSIS_SERVICE_URL}/analyses", params={"enabled": "true"}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # registry down -> run without tools
        log.warning("analysis registry unavailable: %s", exc)
        return []


def manifest_for(analysis: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    variables = [v["name"] for v in analysis.get("inputs", [])] + [
        v["name"] for v in analysis.get("outputs", [])
    ]
    env = {"ANALYSIS_SERVICE_URL": ANALYSIS_SERVICE_URL}
    if run_id:
        env["SYSML_RUN_ID"] = run_id
    return {
        "toolName": analysis["name"],
        "version": str(analysis.get("version") or "1"),
        "executable": sys.executable,
        "variables": list(dict.fromkeys(variables)),
        "invocation": {
            "args": [str(BRIDGE), analysis["name"]],
            "env": env,
            "stdin": "json",
        },
    }


@contextmanager
def tools_dir(run_id: str | None = None, registry: list[dict[str, Any]] | None = None) -> Iterator[tuple[Path | None, list[str]]]:
    """A private manifest directory for one OpenSysML process.

    OpenSysML refuses a manifest directory that is group/world writable or that
    lives under the model's workspace, so it is created 0700 under TOOLS_BASE.
    """
    analyses = registry if registry is not None else fetch_registry()
    usable = [a for a in analyses if a.get("inputs") is not None and (a.get("inputs") or a.get("outputs"))]
    if not usable:
        yield None, []
        return
    TOOLS_BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(TOOLS_BASE, 0o700)
    d = Path(tempfile.mkdtemp(prefix="tools-", dir=TOOLS_BASE))
    os.chmod(d, 0o700)
    names = []
    try:
        for a in usable:
            p = d / f"{_safe(a['name'])}.json"
            p.write_text(json.dumps(manifest_for(a, run_id), indent=2))
            os.chmod(p, 0o600)
            names.append(a["name"])
        yield d, names
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def tool_env(d: Path | None) -> dict[str, str]:
    env = {"OPENSYSML_TOOL_TIMEOUT": TOOL_TIMEOUT}
    if d:
        env["OPENSYSML_TOOLS"] = str(d)
    return env
