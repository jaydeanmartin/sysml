"""Propeller aerodynamics: an *external* analysis service.

Registered in the analysis registry as ``propellerAero`` (kind external). SysML
binds to it with ``ToolExecution { toolName = "propellerAero"; }`` metadata on
an analysis def whose subject is a propeller; the model service evaluates its
``in`` parameters for a catalog instance and calls it through the analysis
service, which logs the call.

Request:  {"inputs": {"diameter": 15, "bladeCount": 2, "stations": [...], "chord": [...],
           "twist": [...], "airfoil": "NACA 4412", "hubRadius": 0.12, "tipSpeed": 110, ...}}
Response: {"outputs": {"ct0": ..., "cp2": ..., "thrustPerWatt": {"value": .., "unit": "N/W"},
           "curve": [...], "sections": [...], "span": [...], "static": {...}, "warnings": [...]}}

Section polars come from XFOIL (``xfoil``), the propeller from blade-element
momentum theory (``bemt``).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import bemt, xfoil

app = FastAPI(title="Propeller aerodynamics (external analysis)", version="0.1.0")


class Req(BaseModel):
    toolName: str | None = None
    uri: str | None = None
    inputs: dict[str, Any]
    runId: str | None = None


def _v(x: Any) -> Any:
    return x["value"] if isinstance(x, dict) and "value" in x else x


def _num(raw: dict[str, Any], name: str, default: float | None = None) -> float:
    v = raw.get(name, default)
    if v is None:
        raise HTTPException(422, f"missing input: {name}")
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{name}: a number, not {v!r}") from exc


def _seq(raw: dict[str, Any], name: str) -> list[float]:
    v = raw.get(name)
    if not isinstance(v, list) or not v:
        raise HTTPException(422, f"{name}: a sequence of numbers, one per blade station")
    try:
        return [float(x) for x in v]
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{name}: a sequence of numbers") from exc


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "xfoil": xfoil.available()}


@app.post("/propeller")
def propeller(req: Req) -> dict[str, Any]:
    raw = {k: _v(v) for k, v in req.inputs.items()}
    airfoil = raw.get("airfoil")
    if not (isinstance(airfoil, str) or (isinstance(airfoil, list) and all(isinstance(a, str) for a in airfoil))):
        raise HTTPException(422, "airfoil: an XFOIL airfoil such as 'NACA 4412', or one per station")
    try:
        blade = bemt.Blade.build(
            _num(raw, "diameter"), _num(raw, "bladeCount", 2), _seq(raw, "stations"), _seq(raw, "chord"), _seq(raw, "twist"),
            airfoil, raw.get("hubRadius"),
        )
        res = bemt.analyse(
            blade,
            tip_speed=_num(raw, "tipSpeed", 110.0),
            rho=_num(raw, "airDensity", 1.225),
            mu=_num(raw, "viscosity", 1.81e-5),
            ncrit=_num(raw, "ncrit", 9.0),
            fit_j_max=float(raw["fitAdvanceRatio"]) if raw.get("fitAdvanceRatio") is not None else None,
        )
    except bemt.AeroError as exc:
        raise HTTPException(422, str(exc)) from exc
    s = res.static
    return {
        "outputs": {
            **{k: round(float(v), 5) for k, v in res.fit.items()},
            "thrustPerWatt": {"value": round(s["thrustPerWatt"], 5) if s["thrustPerWatt"] else None, "unit": "N/W"},
            "staticThrust": {"value": round(s["thrust"], 3), "unit": "N"},
            "staticPower": {"value": round(s["power"], 2), "unit": "W"},
            "referenceRpm": {"value": round(s["rpm"]), "unit": "rpm"},
            "figureOfMerit": round(s["figureOfMerit"], 4) if s["figureOfMerit"] else None,
            "fitRange": list(res.fit_range),
            "fitError": {k: round(v, 5) for k, v in res.fit_error.items()},
            "curve": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in p.items()} for p in res.curve],
            "span": res.span,
            "sections": [{"station": s_.station, "airfoil": s_.airfoil, **s_.polar.summary(), "warning": s_.warning} for s_ in res.sections],
            "warnings": res.warnings,
        }
    }
