"""Time-stepped mission simulation of a battery-powered multirotor.

A mission is a list of flown steps, each with a ``duration`` (s) and a
``powerFactor`` (power draw as a multiple of hover power), as the system that
flies it performs them. The simulator integrates the battery state over time:

* electrical power at time t is ``hoverPower * powerFactor * mass / takeoffMass``
  (hover power proportional to mass, as the SysML architecture computes it);
  after a step that ``releasesPayload`` the mass drops by ``payloadMass``, so the
  return leg needs less power;
* the battery delivers that power with an efficiency that falls with load,
  ``1 - internalLoss * power / batteryMaxPower`` (resistive losses), so the
  energy drawn from the pack exceeds the energy delivered;
* the mission fails if the pack empties, or if more power is asked than the
  pack can deliver; it is within limits if the state of charge never drops
  below the ``reserve``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MAX_SAMPLES = 400


class SimulationError(ValueError):
    """Inputs that cannot be simulated (HTTP 422)."""


@dataclass
class Inputs:
    steps: list[dict[str, Any]]
    hoverPower: float          # W at takeoff mass
    takeoffMass: float         # kg
    payloadMass: float         # kg, carried until a step releases it
    batteryEnergy: float       # Wh
    batteryMaxPower: float     # W
    reserve: float = 0.2       # fraction of the pack kept back
    internalLoss: float = 0.05 # efficiency loss at full battery power
    timeStep: float = 1.0      # s


def _num(step: dict[str, Any], key: str, default: float | None = None) -> float:
    v = step.get(key, default)
    if isinstance(v, bool) or v is None:
        if default is None:
            raise SimulationError(f"step '{step.get('name', '?')}' has no {key}")
        return default
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise SimulationError(f"step '{step.get('name', '?')}': {key} is not a number") from exc
    if not math.isfinite(x) or x < 0:
        raise SimulationError(f"step '{step.get('name', '?')}': {key} must be a finite, non-negative number")
    return x


def simulate(i: Inputs) -> dict[str, Any]:
    if i.hoverPower <= 0 or i.takeoffMass <= 0 or i.batteryEnergy <= 0 or i.batteryMaxPower <= 0:
        raise SimulationError("hoverPower, takeoffMass, batteryEnergy and batteryMaxPower must be positive")
    if not 0 <= i.reserve < 1:
        raise SimulationError("reserve is a fraction between 0 and 1")
    dt = max(0.05, i.timeStep)
    capacity = i.batteryEnergy * 3600  # J
    used = 0.0                          # J drawn from the pack
    delivered = 0.0                     # J delivered to the motors
    t = 0.0
    mass = i.takeoffMass
    peak = 0.0
    depleted_at: float | None = None
    reserve_at: tuple[float, str] | None = None
    overload: list[str] = []
    samples: list[dict[str, Any]] = [{"t": 0.0, "soc": 100.0, "power": 0.0, "step": None}]
    steps_out = []

    for k, s in enumerate(i.steps):
        name = str(s.get("name") or f"step{k + 1}")
        duration = _num(s, "duration")
        factor = _num(s, "powerFactor")
        power = i.hoverPower * factor * mass / i.takeoffMass
        if power > i.batteryMaxPower:
            overload.append(name)
        efficiency = max(0.5, 1 - i.internalLoss * min(power / i.batteryMaxPower, 1.5))
        start_soc = 100 * (1 - used / capacity)
        start_t, step_used = t, 0.0
        remaining = duration
        while remaining > 1e-9 and depleted_at is None:
            h = min(dt, remaining)
            draw = power / efficiency * h
            if used + draw >= capacity:  # the pack empties within this slice
                h = (capacity - used) * efficiency / power if power else h
                draw = capacity - used
                depleted_at = t + h
            used += draw
            step_used += draw
            delivered += power * h
            t += h
            remaining -= h
            soc = 100 * (1 - used / capacity)
            if reserve_at is None and soc < 100 * i.reserve:
                reserve_at = (t, name)
            samples.append({"t": t, "soc": soc, "power": power, "step": name})
        peak = max(peak, power)
        steps_out.append(
            {
                "name": name,
                "behavior": s.get("behavior"),
                "start": start_t,
                "duration": t - start_t,
                "power": power,
                "mass": mass,
                "energy": step_used / 3600,
                "startSoc": start_soc,
                "endSoc": 100 * (1 - used / capacity),
            }
        )
        if depleted_at is not None:
            break
        if s.get("releasesPayload") in (True, "true"):
            mass = max(mass - i.payloadMass, 0.1 * i.takeoffMass)

    end_soc = 100 * (1 - used / capacity)
    feasible = depleted_at is None and reserve_at is None and not overload
    limiting = ""
    if depleted_at is not None:
        limiting = steps_out[-1]["name"]
    elif overload:
        limiting = overload[0]
    elif reserve_at:
        limiting = reserve_at[1]
    stride = max(1, math.ceil(len(samples) / MAX_SAMPLES))
    timeline = samples[::stride] + ([samples[-1]] if (len(samples) - 1) % stride else [])
    return {
        "energyUsed": used / 3600,                  # Wh drawn from the battery
        "energyPercent": 100 * used / capacity,     # of the pack's total energy
        "energyDelivered": delivered / 3600,        # Wh delivered to the motors
        "missionTime": t,                           # s (up to depletion)
        "endStateOfCharge": end_soc,                # %
        "reserveMargin": end_soc - 100 * i.reserve, # % points above the reserve
        "peakPower": peak,                          # W
        "averagePower": delivered / t if t else 0.0,
        "completed": depleted_at is None,
        "feasible": feasible,
        "limitingStep": limiting,
        "issues": (
            ([f"battery empty at {depleted_at:.0f} s, during {limiting}"] if depleted_at is not None else [])
            + ([f"needs more power than the battery delivers in {', '.join(overload)}"] if overload else [])
            + ([f"below the {100 * i.reserve:.0f} % reserve at {reserve_at[0]:.0f} s, during {reserve_at[1]}"] if reserve_at else [])
        ),
        "flownSteps": steps_out,
        "timeline": timeline,
    }
