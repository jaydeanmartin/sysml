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

A fourth, ``controlSimulator`` (``POST /control``), simulates the closed-loop
control of the drone: a command mixer, cascaded PID loops down to each motor's
ESC, reaction torques and inertia. It tunes the gains from open-loop tests or
verifies the model's gains on a manoeuvre per behavior. See ``control``.

A third, ``componentCurves`` (``POST /curves``), returns the performance curves
of one propeller or motor from its attribute values, as the dynamic simulation
uses them, so the Library can chart a component without flying a mission.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import control, dynamics
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


@app.post("/curves")
def run_curves(req: Req) -> dict[str, Any]:
    raw = {k: _v(v) for k, v in req.inputs.items()}
    component = raw.get("component")
    voltage = raw.get("voltage")
    try:
        res = dynamics.component_curves(str(component), raw, float(voltage) if voltage is not None else None)
    except (dynamics.SimulationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"outputs": res}


CONTROL_UNITS = {
    "phaseMargin": "deg", "gainMargin": "dB", "climbSettlingTime": "s", "speedSettlingTime": "s", "speedOvershoot": "%",
    "headingSettlingTime": "s", "gustDeviation": "m", "saturation": "%",
}


@app.post("/control")
def run_control(req: Req) -> dict[str, Any]:
    """Controllability: tune the flight controller from open-loop tests, or verify
    the model's gains, and fly the control tests given. See ``control``."""
    raw = {k: _v(v) for k, v in req.inputs.items()}
    for key in ("behaviors", "tests"):  # entries may carry {"value", "unit"} objects too
        if isinstance(raw.get(key), list):
            raw[key] = [{k: _v(v) for k, v in b.items()} for b in raw[key] if isinstance(b, dict)]
    try:
        res = control.simulate(raw)
    except (dynamics.SimulationError, ValueError, ZeroDivisionError, np.linalg.LinAlgError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"outputs": {k: ({"value": v, "unit": CONTROL_UNITS[k]} if k in CONTROL_UNITS and v is not None else v) for k, v in res.items()}}
