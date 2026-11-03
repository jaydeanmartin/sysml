"""Geometry service: 3-D geometry of system instances, as binary glTF.

It reads a project's catalog from the model service (one parse of all its
models), so the geometry always follows the saved SysML text: the component
instance in each slot and its attribute values. See ``generators`` for how
parts are laid out.

* ``GET /projects/{id}/geometry.glb?system=QN``: the scene (model/gltf-binary);
  each node carries its slot, element index and component instance in
  ``extras``, the scene the description below;
* ``GET /projects/{id}/geometry?system=QN``: the same description as JSON,
  without the meshes.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Response

from . import generators

MODEL_SERVICE_URL = os.environ.get("MODEL_SERVICE_URL", "http://model:8001")

app = FastAPI(title="Geometry service", version="0.1.0")


def _catalog(project_id: str) -> dict[str, Any]:
    try:
        r = httpx.get(f"{MODEL_SERVICE_URL}/projects/{project_id}/catalog", timeout=120)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"model service unreachable: {exc}") from exc
    if r.status_code == 404:
        raise HTTPException(404, "project not found")
    if r.status_code >= 400:
        raise HTTPException(502, f"model service: {r.status_code} {r.text[:200]}")
    return r.json()


def _build(project_id: str, system: str) -> tuple[generators.Result, dict[str, Any]]:
    cat = _catalog(project_id)
    try:
        res = generators.build(cat, system)
    except generators.GeometryError as exc:
        raise HTTPException(404, str(exc)) from exc
    cfg = next(c for c in cat["configurations"] if c["qualifiedName"] == system)
    info = {
        "system": system,
        "architecture": cfg["architecture"],
        "generator": res.generator,
        "metrics": res.metrics,
        "warnings": res.warnings,
        "parts": [{"name": p.name, "material": p.material, **p.extras} for p in res.scene.parts],
    }
    return res, info


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/projects/{project_id}/geometry")
def geometry(project_id: str, system: str) -> dict[str, Any]:
    """What the scene of a system instance holds: generator, metrics, warnings, parts."""
    return _build(project_id, system)[1]


@app.get("/projects/{project_id}/geometry.glb")
def geometry_glb(project_id: str, system: str) -> Response:
    """The scene of a system instance as binary glTF 2.0."""
    res, info = _build(project_id, system)
    return Response(res.scene.to_glb(info), media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})
