"""Built-in analyses hosted by the analysis service.

Register a Python function with ``@analysis(...)``; on startup it is upserted
into the registry and becomes callable from a SysML v2 model through
``metadata ToolExecution { toolName = "<name>"; }``.

Each function receives ``{variable: value}`` with quantities already converted
to the SI unit declared for that input, and returns ``{output: value}`` (or
``{output: (value, unit)}``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Builtin:
    name: str
    description: str
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    fn: Callable[[dict[str, float]], dict[str, Any]]
    version: str = "1"


REGISTRY: dict[str, Builtin] = {}


def analysis(name: str, description: str, inputs: list[dict], outputs: list[dict], version: str = "1"):
    def wrap(fn):
        REGISTRY[name] = Builtin(name, description, inputs, outputs, fn, version)
        return fn

    return wrap


# ---------------------------------------------------------------- unit helpers
# Factors to the SI base unit of each quantity kind used by the built-ins.
UNIT_FACTORS: dict[str, dict[str, float]] = {
    "kg": {"kg": 1, "g": 1e-3, "t": 1000, "mg": 1e-6, "lb": 0.45359237},
    "m": {"m": 1, "km": 1000, "cm": 0.01, "mm": 1e-3, "ft": 0.3048},
    "m/s": {"m/s": 1, "km/h": 1 / 3.6, "mph": 0.44704},
    "W": {"W": 1, "kW": 1e3, "MW": 1e6},
    "K": {"K": 1},
    "s": {"s": 1, "min": 60, "h": 3600},
    "m**2": {"m**2": 1, "m^2": 1, "m²": 1},
}


def to_si(value: float, unit: str | None, si: str | None) -> float:
    if not si or not unit or unit == si:
        return value
    u = unit.replace("SI::", "").replace(" ", "").replace("'", "")
    factors = UNIT_FACTORS.get(si, {})
    if u in factors:
        return value * factors[u]
    if u == si:
        return value
    if si == "K" and u in ("°C", "degC"):
        return value + 273.15
    raise ValueError(f"cannot convert unit '{unit}' to '{si}'")


# ---------------------------------------------------------------- analyses
@analysis(
    "massRollup",
    "Sums the masses of a vehicle's major subsystems.",
    inputs=[
        {"name": "engineMass", "unit": "kg"},
        {"name": "tankMass", "unit": "kg"},
        {"name": "transmissionMass", "unit": "kg"},
    ],
    outputs=[{"name": "total", "unit": "kg"}],
)
def mass_rollup(v: dict[str, float]) -> dict[str, Any]:
    return {"total": (v.get("engineMass", 0) + v.get("tankMass", 0) + v.get("transmissionMass", 0), "kg")}


@analysis(
    "rangeEstimate",
    "Estimates driving range (km) from tank capacity (L), engine efficiency (0-1) and vehicle mass.",
    inputs=[{"name": "capacity"}, {"name": "efficiency"}, {"name": "mass", "unit": "kg"}],
    outputs=[{"name": "range"}],
)
def range_estimate(v: dict[str, float]) -> dict[str, Any]:
    energy_mj = v["capacity"] * 34.2 * v["efficiency"]  # gasoline: 34.2 MJ/L
    # rolling + aero losses scale roughly with mass: ~0.00029 MJ per km per kg + 0.25 MJ/km aero
    per_km = 0.00029 * v["mass"] + 0.25
    return {"range": round(energy_mj / per_km, 1)}


@analysis(
    "dragForce",
    "Aerodynamic drag force F = 0.5 * rho * Cd * A * v^2.",
    inputs=[
        {"name": "cd"},
        {"name": "area", "unit": "m**2"},
        {"name": "speed", "unit": "m/s"},
        {"name": "rho"},
    ],
    outputs=[{"name": "force", "unit": "N"}],
)
def drag_force(v: dict[str, float]) -> dict[str, Any]:
    rho = v.get("rho") or 1.225
    return {"force": (0.5 * rho * v["cd"] * v["area"] * v["speed"] ** 2, "N")}


@analysis(
    "rocketDeltaV",
    "Tsiolkovsky rocket equation: dv = Isp * g0 * ln(m0 / mf).",
    inputs=[{"name": "isp", "unit": "s"}, {"name": "wetMass", "unit": "kg"}, {"name": "dryMass", "unit": "kg"}],
    outputs=[{"name": "deltaV", "unit": "m/s"}],
)
def rocket_delta_v(v: dict[str, float]) -> dict[str, Any]:
    if v["dryMass"] <= 0 or v["wetMass"] <= v["dryMass"]:
        raise ValueError("wetMass must exceed dryMass > 0")
    return {"deltaV": (v["isp"] * 9.80665 * math.log(v["wetMass"] / v["dryMass"]), "m/s")}
