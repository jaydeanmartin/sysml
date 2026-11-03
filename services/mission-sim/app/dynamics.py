"""Dynamic mission simulation of a battery-powered multirotor.

Where the mission simulator scales hover power by a fixed factor per behavior,
this one flies each behavior through the physics of the propulsion chain, so
the power of a behavior follows from its speeds, the drone's mass and drag,
and where the propeller and motor operate on their performance curves.
Still air (no wind) and a constant air density are assumed.

Each time slice (``timeStep`` s):

* **Vehicle** (point mass in the vertical plane). The velocity follows the
  current step's ``groundSpeed`` and ``verticalSpeed``, changing at most
  ``maxAcceleration`` (m/s²), so starting and stopping are transients with
  their own power. The rotors provide ``m·a + drag + weight``; the drone tilts
  so the thrust points that way. Drag is ``½ ρ CdA v²`` (``dragArea`` = CdA).
* **Propeller** (each rotor carries thrust / rotorCount). Thrust and power
  coefficients are quadratics in the advance ratio ``J = Va / (n D)``, where Va
  is the velocity along the rotor axis (climb, and forward speed tilted into
  the disk)::

      CT(J) = ct0 + ct1 J + ct2 J²      T = CT ρ n² D⁴
      CP(J) = cp0 + cp1 J + cp2 J²      P = CP ρ n³ D⁵ = 2π n Q
      η = J CT / CP

  Thrust is quadratic in n for a given Va, so the rotor speed is solved in
  closed form. Descending flight (Va < 0) is treated as hover (J = 0): vortex
  ring state and windmilling are not modelled.
* **Motor** (brushless DC, with ``Kv`` in rpm/V, winding ``resistance`` and
  ``noLoadCurrent``): ``I = Q Kv' + I0``, ``V = ω / Kv' + I R`` (Kv' in
  rad/s/V), efficiency ``Q ω / (V I)``. The speed controller loses
  ``1 - escEfficiency``. Throttle is the motor voltage over the bus voltage.
* **Battery**: open-circuit voltage falls from 1.135 to 0.89 × nominal as it
  discharges (a LiPo between 4.2 and 3.3 V per cell), behind an internal
  resistance that sags the voltage 10 % at ``batteryMaxPower``. Energy drawn
  is the open-circuit voltage times the current, so the resistive loss counts.
* A step that is not ``airborne`` (on the ground) draws ``avionicsPower`` only.
  After a step that ``releasesPayload`` the drone is ``payloadMass`` lighter.

Results: energy and power per step and per behavior, the mission totals, the
propeller and motor performance curves with each step's operating point on
them, and a timeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

G = 9.81
IN = 0.0254  # m per inch
MAX_SAMPLES = 600
CURVE_POINTS = 41


class SimulationError(ValueError):
    """Inputs that cannot be simulated (HTTP 422)."""


@dataclass
class Inputs:
    steps: list[dict[str, Any]]
    rotorCount: float
    takeoffMass: float           # kg
    payloadMass: float           # kg, carried until a step releases it
    dragArea: float              # m², drag coefficient × frontal area
    propDiameter: float          # in
    ct0: float
    ct1: float
    ct2: float
    cp0: float
    cp1: float
    cp2: float
    motorKv: float               # rpm/V
    motorResistance: float       # Ω
    motorNoLoadCurrent: float    # A
    motorMaxCurrent: float       # A
    batteryVoltage: float        # V nominal
    batteryEnergy: float         # Wh
    batteryMaxPower: float       # W
    reserve: float = 0.2
    airDensity: float = 1.225    # kg/m³
    escEfficiency: float = 0.95
    avionicsPower: float = 15.0  # W, always drawn
    maxAcceleration: float = 2.0 # m/s²
    timeStep: float = 0.2        # s


def _num(step: dict[str, Any], key: str, default: float | None = None, signed: bool = False) -> float:
    v = step.get(key, default)
    if isinstance(v, bool) or v is None:
        if default is None:
            raise SimulationError(f"step '{step.get('name', '?')}' has no {key}")
        return default
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise SimulationError(f"step '{step.get('name', '?')}': {key} is not a number") from exc
    if not math.isfinite(x) or (x < 0 and not signed):
        raise SimulationError(f"step '{step.get('name', '?')}': {key} must be a finite{'' if signed else ', non-negative'} number")
    return x


def _flag(step: dict[str, Any], key: str, default: bool) -> bool:
    v = step.get(key, default)
    return v in (True, "true") if v is not None else default


# ------------------------------------------------------------------ component models
class Propeller:
    def __init__(self, i: Inputs):
        self.D = i.propDiameter * IN
        self.rho = i.airDensity
        self.ct = (i.ct0, i.ct1, i.ct2)
        self.cp = (i.cp0, i.cp1, i.cp2)
        if i.ct0 <= 0 or i.cp0 <= 0:
            raise SimulationError("ct0 and cp0 (static thrust and power coefficients) must be positive")
        # advance ratio where the thrust coefficient reaches zero: the end of the curves
        a, b, c = i.ct2, i.ct1, i.ct0
        if abs(a) < 1e-12:
            self.j_max = -c / b if b < 0 else 2.0
        else:
            disc = b * b - 4 * a * c
            roots = [(-b + s * math.sqrt(disc)) / (2 * a) for s in (1, -1)] if disc >= 0 else []
            pos = [r for r in roots if r > 0]
            self.j_max = min(pos) if pos else 2.0
        if any(self.CP(j) <= 0 for j in (0, self.j_max * 0.999)):
            raise SimulationError("the power coefficient must stay positive up to the zero-thrust advance ratio")

    def CT(self, j: float) -> float:
        return self.ct[0] + self.ct[1] * j + self.ct[2] * j * j

    def CP(self, j: float) -> float:
        return self.cp[0] + self.cp[1] * j + self.cp[2] * j * j

    def speed_for(self, thrust: float, va: float) -> float:
        """Rotor speed (rev/s) giving ``thrust`` (N) at axial velocity ``va`` (m/s):
        ρ D⁴ (ct0 n² + ct1 (va/D) n + ct2 (va/D)²) = thrust."""
        u = max(va, 0.0) / self.D
        a, b, c = self.ct[0], self.ct[1] * u, self.ct[2] * u * u - thrust / (self.rho * self.D ** 4)
        disc = max(b * b - 4 * a * c, 0.0)
        n = (-b + math.sqrt(disc)) / (2 * a)
        # at least fast enough to stay inside the curves (J <= j_max)
        return max(n, u / self.j_max if self.j_max > 0 else 0.0, 1e-6)

    def curves(self) -> list[dict[str, float]]:
        out = []
        for k in range(CURVE_POINTS):
            j = self.j_max * k / (CURVE_POINTS - 1)
            ct, cp = self.CT(j), self.CP(j)
            out.append({"J": j, "CT": ct, "CP": cp, "CQ": cp / (2 * math.pi), "CTCP": ct / cp, "efficiency": j * ct / cp})
        return out


class Motor:
    def __init__(self, i: Inputs):
        if i.motorKv <= 0 or i.motorResistance < 0 or i.motorNoLoadCurrent < 0 or i.motorMaxCurrent <= 0:
            raise SimulationError("motorKv and motorMaxCurrent must be positive, motorResistance and motorNoLoadCurrent not negative")
        self.kv = i.motorKv * 2 * math.pi / 60  # rad/s per V
        self.R = i.motorResistance
        self.I0 = i.motorNoLoadCurrent
        self.i_max = i.motorMaxCurrent

    def operate(self, torque: float, omega: float) -> tuple[float, float]:
        """Current (A) and terminal voltage (V) for a shaft torque and speed."""
        current = torque * self.kv + self.I0
        return current, omega / self.kv + current * self.R

    def curves(self, voltage: float) -> list[dict[str, float]]:
        """Full-throttle characteristics at ``voltage``, against current."""
        out = []
        i_stall = voltage / self.R if self.R > 0 else self.i_max
        top = min(self.i_max, i_stall)
        for k in range(CURVE_POINTS):
            cur = self.I0 + (top - self.I0) * k / (CURVE_POINTS - 1)
            omega = max((voltage - cur * self.R) * self.kv, 0.0)
            torque = max(cur - self.I0, 0.0) / self.kv
            p_out = torque * omega
            out.append({
                "current": cur, "speed": omega * 60 / (2 * math.pi), "torque": torque,
                "power": p_out, "efficiency": p_out / (voltage * cur) if cur > 0 else 0.0,
            })
        return out


# ------------------------------------------------------------------ simulation
class _Acc:
    """Time-weighted sums of a step's (or behavior's) operating quantities."""

    KEYS = ("power", "J", "CT", "CP", "CQ", "CTCP", "propEfficiency", "thrustPerPower", "rpm", "torque", "thrust",
            "shaftPower", "current", "motorVoltage", "motorEfficiency", "throttle", "tilt", "speed")

    def __init__(self) -> None:
        self.t = 0.0
        self.air_t = 0.0
        self.energy = 0.0
        self.peak = 0.0
        self.max_throttle = 0.0
        self.sum = dict.fromkeys(self.KEYS, 0.0)

    def add(self, h: float, power: float, energy: float, op: dict[str, float] | None) -> None:
        self.t += h
        self.energy += energy
        self.peak = max(self.peak, power)
        self.sum["power"] += power * h
        if op:
            self.air_t += h
            self.max_throttle = max(self.max_throttle, op["throttle"])
            for k in self.KEYS[1:]:
                self.sum[k] += op[k] * h

    def summary(self) -> dict[str, Any]:
        avg = {k: (v / self.air_t if self.air_t else None) for k, v in self.sum.items() if k != "power"}
        return {
            "duration": self.t,
            "energy": self.energy / 3600,                       # Wh drawn from the pack
            "averagePower": self.sum["power"] / self.t if self.t else 0.0,
            "peakPower": self.peak,
            "maxThrottle": self.max_throttle,
            "airborne": self.air_t > 0,
            "operatingPoint": avg if self.air_t else None,      # time-averaged, airborne only
        }


def simulate(i: Inputs) -> dict[str, Any]:
    n_rot = int(round(i.rotorCount))
    if n_rot < 1 or i.takeoffMass <= 0 or i.batteryEnergy <= 0 or i.batteryMaxPower <= 0 or i.batteryVoltage <= 0:
        raise SimulationError("rotorCount, takeoffMass, batteryVoltage, batteryEnergy and batteryMaxPower must be positive")
    if not 0 <= i.reserve < 1:
        raise SimulationError("reserve is a fraction between 0 and 1")
    if i.airDensity <= 0 or i.dragArea < 0 or i.propDiameter <= 0 or not 0 < i.escEfficiency <= 1:
        raise SimulationError("airDensity and propDiameter must be positive, dragArea not negative, escEfficiency in (0, 1]")
    prop = Propeller(i)
    motor = Motor(i)
    dt = min(max(0.02, i.timeStep), 2.0)
    a_max = max(i.maxAcceleration, 0.1)
    capacity = i.batteryEnergy * 3600            # J
    r_batt = 0.1 * i.batteryVoltage ** 2 / i.batteryMaxPower
    rho, D = i.airDensity, prop.D

    used = 0.0
    t = 0.0
    mass = i.takeoffMass
    vh = vz = 0.0
    alt = dist = 0.0
    depleted_at: float | None = None
    reserve_at: tuple[float, str] | None = None
    saturated: dict[str, float] = {}             # step -> s above full throttle
    overcurrent: dict[str, float] = {}           # step -> s above the motor's max current
    overload: dict[str, float] = {}              # step -> s the pack cannot deliver
    total = _Acc()
    by_behavior: dict[str, _Acc] = {}
    steps_out: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = [{"t": 0.0, "soc": 100.0, "power": i.avionicsPower, "speed": 0.0, "altitude": 0.0, "rpm": 0.0, "throttle": 0.0, "step": None}]

    def operate(ax: float, az: float) -> dict[str, float]:
        """The propulsion chain's operating point for accelerations ax, az."""
        drag_h = 0.5 * rho * i.dragArea * vh * abs(vh)
        drag_z = 0.5 * rho * i.dragArea * vz * abs(vz)
        fx = mass * ax + drag_h
        fz = mass * (G + az) + drag_z
        thrust = math.hypot(fx, fz)
        tilt = math.atan2(fx, fz)
        va = vh * math.sin(tilt) + vz * math.cos(tilt)   # along the rotor axis
        t_rot = thrust / n_rot
        n = prop.speed_for(t_rot, va)
        j = min(max(va, 0.0) / (n * D), prop.j_max)
        ct, cp = prop.CT(j), prop.CP(j)
        shaft = cp * rho * n ** 3 * D ** 5
        omega = 2 * math.pi * n
        torque = shaft / omega
        current, volts = motor.operate(torque, omega)
        elec = volts * current
        return {
            "J": j, "CT": ct, "CP": cp, "CQ": cp / (2 * math.pi), "CTCP": ct / cp, "propEfficiency": j * ct / cp,
            "thrustPerPower": t_rot / shaft, "rpm": n * 60, "torque": torque, "thrust": t_rot, "shaftPower": shaft,
            "current": current, "motorVoltage": volts, "motorEfficiency": shaft / elec, "tilt": math.degrees(tilt),
            "speed": math.hypot(vh, vz), "busPower": n_rot * elec / i.escEfficiency,
        }

    for k, s in enumerate(i.steps):
        name = str(s.get("name") or f"step{k + 1}")
        behavior = str(s.get("behavior") or name)
        duration = _num(s, "duration")
        target_h = _num(s, "groundSpeed", 0.0)
        target_z = _num(s, "verticalSpeed", 0.0, signed=True)
        airborne = _flag(s, "airborne", True)
        acc = _Acc()
        start_t, start_soc, start_mass = t, 100 * (1 - used / capacity), mass
        remaining = duration
        while remaining > 1e-9 and depleted_at is None:
            h = min(dt, remaining)
            op = None
            if airborne:
                dvh = max(-a_max * h, min(a_max * h, target_h - vh))
                dvz = max(-a_max * h, min(a_max * h, target_z - vz))
                ax, az = dvh / h, dvz / h
                vh += dvh
                vz += dvz
                op = operate(ax, az)
                power = op["busPower"] + i.avionicsPower
            else:
                vh = vz = 0.0
                power = i.avionicsPower
            soc = 1 - used / capacity
            v_oc = i.batteryVoltage * (0.89 + 0.245 * soc)
            disc = v_oc * v_oc - 4 * r_batt * power
            if disc < 0:  # beyond what the pack can deliver: count it, draw at the limit
                overload[name] = overload.get(name, 0.0) + h
                disc = 0.0
            i_batt = (v_oc - math.sqrt(disc)) / (2 * r_batt)
            v_bus = v_oc - i_batt * r_batt
            if op:
                op["throttle"] = op["motorVoltage"] / v_bus if v_bus > 0 else float("inf")
                if op["throttle"] > 1:
                    saturated[name] = saturated.get(name, 0.0) + h
                if op["current"] > motor.i_max:
                    overcurrent[name] = overcurrent.get(name, 0.0) + h
            draw = v_oc * i_batt * h
            if used + draw >= capacity:
                h *= (capacity - used) / draw if draw else 1
                draw = capacity - used
                depleted_at = t + h
            used += draw
            t += h
            remaining -= h
            alt = max(alt + vz * h, 0.0)
            dist += vh * h
            acc.add(h, power, draw, op)
            total.add(h, power, draw, op)
            by_behavior.setdefault(behavior, _Acc()).add(h, power, draw, op)
            soc_pct = 100 * (1 - used / capacity)
            if reserve_at is None and soc_pct < 100 * i.reserve:
                reserve_at = (t, name)
            samples.append({
                "t": t, "soc": soc_pct, "power": power, "speed": math.hypot(vh, vz), "altitude": alt,
                "rpm": op["rpm"] if op else 0.0, "throttle": op["throttle"] if op else 0.0, "step": name,
            })
        steps_out.append({
            "name": name, "behavior": behavior, "start": start_t, "mass": start_mass,
            "startSoc": start_soc, "endSoc": 100 * (1 - used / capacity), **acc.summary(),
        })
        if depleted_at is not None:
            break
        if _flag(s, "releasesPayload", False):
            mass = max(mass - i.payloadMass, 0.1 * i.takeoffMass)

    end_soc = 100 * (1 - used / capacity)
    tot = total.summary()
    issues = []
    if depleted_at is not None:
        issues.append(f"battery empty at {depleted_at:.0f} s, during {steps_out[-1]['name']}")
    for label, d in (("above full throttle (motors cannot follow)", saturated), ("above the motors' maximum current", overcurrent),
                     ("more power than the battery delivers", overload)):
        if d:
            issues.append(f"{label} in " + ", ".join(f"{k} ({v:.0f} s)" for k, v in d.items()))
    if reserve_at:
        issues.append(f"below the {100 * i.reserve:.0f} % reserve at {reserve_at[0]:.0f} s, during {reserve_at[1]}")
    limiting = (steps_out[-1]["name"] if depleted_at is not None else next(iter(saturated or overcurrent or overload), None)
                or (reserve_at[1] if reserve_at else ""))
    stride = max(1, math.ceil(len(samples) / MAX_SAMPLES))
    timeline = samples[::stride] + ([samples[-1]] if (len(samples) - 1) % stride else [])
    hover = prop.speed_for(i.takeoffMass * G / n_rot, 0.0) * 60
    return {
        "energyUsed": used / 3600,
        "energyPercent": 100 * used / capacity,
        "missionTime": t,
        "endStateOfCharge": end_soc,
        "reserveMargin": end_soc - 100 * i.reserve,
        "peakPower": tot["peakPower"],
        "averagePower": tot["averagePower"],
        "maxThrottle": tot["maxThrottle"],
        "distanceFlown": dist,
        "completed": depleted_at is None,
        "feasible": depleted_at is None and reserve_at is None and not (saturated or overcurrent or overload),
        "limitingStep": limiting,
        "issues": issues,
        "behaviors": [{"behavior": b, **a.summary()} for b, a in by_behavior.items()],
        "flownSteps": steps_out,
        "propellerCurves": prop.curves(),
        "motorCurves": motor.curves(i.batteryVoltage),
        "reference": {"hoverRpm": hover, "propDiameter": D, "airDensity": rho, "batteryVoltage": i.batteryVoltage, "motorMaxCurrent": motor.i_max},
        "timeline": timeline,
    }
