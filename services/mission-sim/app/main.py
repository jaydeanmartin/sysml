"""Mission simulator: an *external* analysis service.

It is registered in the analysis registry as ``missionSimulator`` (kind
external), so it is called like any REST analysis: SysML binds to it with
``ToolExecution { toolName = "missionSimulator"; }`` metadata, and the model
service invokes it through the analysis service, which logs every call.

Request:  {"toolName", "uri", "inputs": {"steps": [...], "hoverPower": {"value": 671, "unit": "W"}, ...}, "runId"}
Response: {"outputs": {"energyUsed": {"value": 92.4, "unit": "Wh"}, ...}}

See ``simulator`` for the model.

A second analysis, ``dynamicsSimulator`` (``POST /dynamics``), flies the mission
through the propulsion chain: propeller performance curves against advance
ratio, a brushless motor model, drag, acceleration transients and battery sag.
It reports the energy and power of every behavior. See ``dynamics``.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import dynamics
from .simulator import Inputs, SimulationError, simulate

app = FastAPI(title="Mission simulator (external analysis)", version="0.1.0")

UNITS = {
    "energyUsed": "Wh", "energyPercent": "%", "energyDelivered": "Wh", "missionTime": "s",
    "endStateOfCharge": "%", "reserveMargin": "%", "peakPower": "W", "averagePower": "W",
}
DYNAMICS_UNITS = {
    "energyUsed": "Wh", "energyPercent": "%", "missionTime": "s", "endStateOfCharge": "%", "reserveMargin": "%",
    "peakPower": "W", "averagePower": "W", "distanceFlown": "m",
}
SCALARS = ("hoverPower", "takeoffMass", "payloadMass", "batteryEnergy", "batteryMaxPower", "reserve", "internalLoss", "timeStep")


class Req(BaseModel):
    toolName: str | None = None
    uri: str | None = None
    inputs: dict[str, Any]
    runId: str | None = None


def _v(x: Any) -> Any:
    return x["value"] if isinstance(x, dict) and "value" in x else x


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/simulate")
def run(req: Req) -> dict[str, Any]:
    raw = {k: _v(v) for k, v in req.inputs.items()}
    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise HTTPException(422, "inputs.steps must be a list of flown steps")
    try:
        args = {k: float(raw[k]) for k in SCALARS if raw.get(k) is not None}
        res = simulate(Inputs(steps=[{k: _v(v) for k, v in s.items()} for s in steps], **args))
    except TypeError as exc:
        raise HTTPException(422, f"missing input: {exc}") from exc
    except (SimulationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"outputs": {k: ({"value": v, "unit": UNITS[k]} if k in UNITS else v) for k, v in res.items()}}


@app.post("/dynamics")
def run_dynamics(req: Req) -> dict[str, Any]:
    raw = {k: _v(v) for k, v in req.inputs.items()}
    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise HTTPException(422, "inputs.steps must be a list of flown steps")
    names = [f.name for f in fields(dynamics.Inputs) if f.name != "steps"]
    try:
        args = {k: float(raw[k]) for k in names if raw.get(k) is not None}
        res = dynamics.simulate(dynamics.Inputs(steps=[{k: _v(v) for k, v in s.items()} for s in steps], **args))
    except TypeError as exc:
        raise HTTPException(422, f"missing input: {exc}") from exc
    except (dynamics.SimulationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"outputs": {k: ({"value": v, "unit": DYNAMICS_UNITS[k]} if k in DYNAMICS_UNITS else v) for k, v in res.items()}}
