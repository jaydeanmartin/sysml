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
from types import SimpleNamespace
from typing import Any

G = 9.81            # m/s², standard gravity
IN = 0.0254         # m per inch
MAX_SAMPLES = 600   # timeline is downsampled to roughly this many points
CURVE_POINTS = 41   # points along each propeller / motor performance curve


class SimulationError(ValueError):
    """Inputs that cannot be simulated (HTTP 422)."""


@dataclass
class Inputs:
    """Vehicle parameters and the mission profile.

    Field names are camelCase because they map one-to-one onto the request
    body. Each entry of ``steps`` is a dict with ``name``, ``behavior``,
    ``duration`` (s), ``groundSpeed`` and ``verticalSpeed`` (m/s, up positive),
    and the flags ``airborne`` and ``releasesPayload``.
    """

    steps: list[dict[str, Any]]
    rotorCount: float
    takeoffMass: float           # kg
    payloadMass: float           # kg, carried until a step releases it
    dragArea: float              # m², drag coefficient × frontal area
    propDiameter: float          # in
    # propeller thrust coefficient CT(J) = ct0 + ct1 J + ct2 J²
    ct0: float
    ct1: float
    ct2: float
    # propeller power coefficient CP(J) = cp0 + cp1 J + cp2 J²
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
    reserve: float = 0.2         # fraction of capacity that should remain at the end
    airDensity: float = 1.225    # kg/m³
    escEfficiency: float = 0.95
    avionicsPower: float = 15.0  # W, always drawn
    maxAcceleration: float = 2.0 # m/s²
    timeStep: float = 0.2        # s


def _num(step: dict[str, Any], key: str, default: float | None = None, signed: bool = False) -> float:
    """Read a numeric field from a step, raising SimulationError if it is bad.

    A missing field takes ``default``; with no default it is required.
    Negative values are rejected unless ``signed`` is true.
    """
    v = step.get(key, default)
    # booleans are ints in Python, so reject them explicitly rather than read True as 1
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
    """Read a boolean field from a step; accepts ``True`` or the string ``"true"``."""
    v = step.get(key, default)
    return v in (True, "true") if v is not None else default


# ------------------------------------------------------------------ component models
class Propeller:
    """Fixed-pitch propeller described by quadratic CT(J) and CP(J) curves.

    ``D`` is the diameter in metres. ``j_max`` is the advance ratio at which
    thrust falls to zero; the curves are only used on 0 <= J <= j_max.
    """

    def __init__(self, i: Inputs):
        self.D = i.propDiameter * IN
        self.rho = i.airDensity
        self.ct = (i.ct0, i.ct1, i.ct2)
        self.cp = (i.cp0, i.cp1, i.cp2)
        # static (J = 0) thrust and power must be positive or hover is impossible
        if i.ct0 <= 0 or i.cp0 <= 0:
            raise SimulationError("ct0 and cp0 (static thrust and power coefficients) must be positive")
        # advance ratio where the thrust coefficient reaches zero: the end of the curves.
        # Solve ct2 J² + ct1 J + ct0 = 0 for the smallest positive root; if the
        # thrust curve never crosses zero, fall back to J = 2.
        a, b, c = i.ct2, i.ct1, i.ct0
        if abs(a) < 1e-12:  # linear CT: single root, only positive if the slope is negative
            self.j_max = -c / b if b < 0 else 2.0
        else:
            disc = b * b - 4 * a * c
            roots = [(-b + s * math.sqrt(disc)) / (2 * a) for s in (1, -1)] if disc >= 0 else []
            pos = [r for r in roots if r > 0]
            self.j_max = min(pos) if pos else 2.0
        # CP must stay positive over the used range, otherwise efficiency and
        # thrust/power blow up (a quadratic is checked at both ends of the range)
        if any(self.CP(j) <= 0 for j in (0, self.j_max * 0.999)):
            raise SimulationError("the power coefficient must stay positive up to the zero-thrust advance ratio")

    def CT(self, j: float) -> float:
        """Thrust coefficient at advance ratio ``j``."""
        return self.ct[0] + self.ct[1] * j + self.ct[2] * j * j

    def CP(self, j: float) -> float:
        """Power coefficient at advance ratio ``j``."""
        return self.cp[0] + self.cp[1] * j + self.cp[2] * j * j

    def speed_for(self, thrust: float, va: float) -> float:
        """Rotor speed (rev/s) giving ``thrust`` (N) at axial velocity ``va`` (m/s):
        ρ D⁴ (ct0 n² + ct1 (va/D) n + ct2 (va/D)²) = thrust."""
        # Substituting J = va / (n D) into T = CT(J) ρ n² D⁴ gives a quadratic
        # in n: ct0 n² + (ct1 u) n + (ct2 u² - T / (ρ D⁴)) = 0, with u = va / D.
        # Negative va (descent) is treated as hover.
        u = max(va, 0.0) / self.D
        a, b, c = self.ct[0], self.ct[1] * u, self.ct[2] * u * u - thrust / (self.rho * self.D ** 4)
        disc = max(b * b - 4 * a * c, 0.0)
        # ct0 > 0 and c < 0 for any positive thrust, so the "+" root is the positive one
        n = (-b + math.sqrt(disc)) / (2 * a)
        # at least fast enough to stay inside the curves (J <= j_max)
        return max(n, u / self.j_max if self.j_max > 0 else 0.0, 1e-6)

    def curves(self) -> list[dict[str, float]]:
        """Coefficient curves sampled from J = 0 to j_max, for plotting.

        CQ is the torque coefficient (CP / 2π), CTCP the thrust-to-power
        coefficient ratio, and efficiency the propulsive efficiency J CT / CP.
        """
        out = []
        for k in range(CURVE_POINTS):
            j = self.j_max * k / (CURVE_POINTS - 1)
            ct, cp = self.CT(j), self.CP(j)
            out.append({"J": j, "CT": ct, "CP": cp, "CQ": cp / (2 * math.pi), "CTCP": ct / cp, "efficiency": j * ct / cp})
        return out


class Motor:
    """Brushless DC motor, first-order model: back-EMF, winding resistance and
    a constant no-load (friction / iron loss) current. The torque constant is
    1 / Kv when Kv is expressed in rad/s per volt."""

    def __init__(self, i: Inputs):
        if i.motorKv <= 0 or i.motorResistance < 0 or i.motorNoLoadCurrent < 0 or i.motorMaxCurrent <= 0:
            raise SimulationError("motorKv and motorMaxCurrent must be positive, motorResistance and motorNoLoadCurrent not negative")
        self.kv = i.motorKv * 2 * math.pi / 60  # rad/s per V
        self.R = i.motorResistance
        self.I0 = i.motorNoLoadCurrent
        self.i_max = i.motorMaxCurrent

    def operate(self, torque: float, omega: float) -> tuple[float, float]:
        """Current (A) and terminal voltage (V) for a shaft torque and speed."""
        # torque-producing current Q·Kv' on top of the no-load current
        current = torque * self.kv + self.I0
        # back-EMF ω / Kv' plus the resistive drop in the windings
        return current, omega / self.kv + current * self.R

    def curves(self, voltage: float) -> list[dict[str, float]]:
        """Full-throttle characteristics at ``voltage``, against current."""
        out = []
        # sweep current from no-load up to stall or the rated maximum, whichever is lower
        i_stall = voltage / self.R if self.R > 0 else self.i_max
        top = min(self.i_max, i_stall)
        for k in range(CURVE_POINTS):
            cur = self.I0 + (top - self.I0) * k / (CURVE_POINTS - 1)
            # at fixed terminal voltage, speed drops linearly with current
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
    """Time-weighted sums of a step's (or behavior's) operating quantities.

    One instance is kept per flown step, per behavior and for the whole
    mission. Operating-point quantities are only accumulated for airborne
    slices, so their averages exclude time on the ground.
    """

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
        """Add a slice of ``h`` s at bus ``power`` (W) that drew ``energy`` (J).
        ``op`` is the propulsion operating point, or None on the ground."""
        self.t += h
        self.energy += energy
        self.peak = max(self.peak, power)
        self.sum["power"] += power * h
        if op:
            self.air_t += h
            self.max_throttle = max(self.max_throttle, op["throttle"])
            for k in self.KEYS[1:]:  # everything except power, which is summed above
                self.sum[k] += op[k] * h

    def summary(self) -> dict[str, Any]:
        """Totals and time averages, in the shape returned to the client."""
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
    """Fly the mission in ``i`` and return energy, performance and feasibility results.

    The simulation is a fixed-step explicit integration. Problems that do not
    stop the run (throttle saturation, overcurrent, battery overload, dipping
    into the reserve) are recorded as issues and make the result infeasible;
    running the battery empty ends the run early.
    """
    # ---- validate inputs and build the component models
    n_rot = int(round(i.rotorCount))
    if n_rot < 1 or i.takeoffMass <= 0 or i.batteryEnergy <= 0 or i.batteryMaxPower <= 0 or i.batteryVoltage <= 0:
        raise SimulationError("rotorCount, takeoffMass, batteryVoltage, batteryEnergy and batteryMaxPower must be positive")
    if not 0 <= i.reserve < 1:
        raise SimulationError("reserve is a fraction between 0 and 1")
    if i.airDensity <= 0 or i.dragArea < 0 or i.propDiameter <= 0 or not 0 < i.escEfficiency <= 1:
        raise SimulationError("airDensity and propDiameter must be positive, dragArea not negative, escEfficiency in (0, 1]")
    prop = Propeller(i)
    motor = Motor(i)
    dt = min(max(0.02, i.timeStep), 2.0)         # clamp the step to keep runs accurate but bounded
    a_max = max(i.maxAcceleration, 0.1)          # floor so a target speed is always reachable
    capacity = i.batteryEnergy * 3600            # J
    # internal resistance chosen so that drawing batteryMaxPower sags the
    # nominal voltage by 10 %: I R = 0.1 V and P = V I  =>  R = 0.1 V² / P
    r_batt = 0.1 * i.batteryVoltage ** 2 / i.batteryMaxPower
    rho, D = i.airDensity, prop.D

    # ---- simulation state
    used = 0.0                                   # J drawn from the pack so far
    t = 0.0                                      # s, mission clock
    mass = i.takeoffMass                         # kg, drops when the payload is released
    vh = vz = 0.0                                # m/s, horizontal and vertical velocity
    alt = dist = 0.0                             # m, altitude and horizontal distance flown
    depleted_at: float | None = None
    reserve_at: tuple[float, str] | None = None
    saturated: dict[str, float] = {}             # step -> s above full throttle
    overcurrent: dict[str, float] = {}           # step -> s above the motor's max current
    overload: dict[str, float] = {}              # step -> s the pack cannot deliver
    total = _Acc()                               # whole mission
    by_behavior: dict[str, _Acc] = {}            # behavior name -> accumulator
    steps_out: list[dict[str, Any]] = []         # per-step summaries, in flight order
    # timeline, one entry per slice (downsampled at the end)
    samples: list[dict[str, Any]] = [{"t": 0.0, "soc": 100.0, "power": i.avionicsPower, "speed": 0.0, "altitude": 0.0, "rpm": 0.0, "throttle": 0.0, "step": None}]

    def operate(ax: float, az: float) -> dict[str, float]:
        """The propulsion chain's operating point for accelerations ax, az.

        Closes over the current velocity (vh, vz) and mass, and works down the
        chain: vehicle forces -> rotor thrust -> propeller speed and shaft
        power -> motor current and voltage -> electrical power on the bus.
        """
        # aerodynamic drag on each axis, signed so it always opposes the motion
        drag_h = 0.5 * rho * i.dragArea * vh * abs(vh)
        drag_z = 0.5 * rho * i.dragArea * vz * abs(vz)
        # total force the rotors must supply: m·a + drag, plus weight vertically
        fx = mass * ax + drag_h
        fz = mass * (G + az) + drag_z
        thrust = math.hypot(fx, fz)
        tilt = math.atan2(fx, fz)                        # from vertical, toward the direction of travel
        va = vh * math.sin(tilt) + vz * math.cos(tilt)   # along the rotor axis
        # ---- propeller: thrust is shared equally between the rotors
        t_rot = thrust / n_rot
        n = prop.speed_for(t_rot, va)                    # rev/s
        j = min(max(va, 0.0) / (n * D), prop.j_max)
        ct, cp = prop.CT(j), prop.CP(j)
        shaft = cp * rho * n ** 3 * D ** 5               # W per rotor
        omega = 2 * math.pi * n                          # rad/s
        torque = shaft / omega
        # ---- motor: electrical input needed to produce that torque and speed
        current, volts = motor.operate(torque, omega)
        elec = volts * current                           # W per motor
        # busPower includes all rotors and the ESC losses; throttle is added by
        # the caller once the bus voltage is known
        return {
            "J": j, "CT": ct, "CP": cp, "CQ": cp / (2 * math.pi), "CTCP": ct / cp, "propEfficiency": j * ct / cp,
            "thrustPerPower": t_rot / shaft, "rpm": n * 60, "torque": torque, "thrust": t_rot, "shaftPower": shaft,
            "current": current, "motorVoltage": volts, "motorEfficiency": shaft / elec, "tilt": math.degrees(tilt),
            "speed": math.hypot(vh, vz), "busPower": n_rot * elec / i.escEfficiency,
        }

    # ---- fly each mission step in order
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
        # time slices; the last one is shortened to end exactly on the step's duration
        while remaining > 1e-9 and depleted_at is None:
            h = min(dt, remaining)
            op = None
            if airborne:
                # move the velocity toward the step's targets, rate-limited by a_max
                dvh = max(-a_max * h, min(a_max * h, target_h - vh))
                dvz = max(-a_max * h, min(a_max * h, target_z - vz))
                ax, az = dvh / h, dvz / h
                vh += dvh
                vz += dvz
                # operating point evaluated at the end-of-slice velocity
                op = operate(ax, az)
                power = op["busPower"] + i.avionicsPower
            else:
                # on the ground: stationary, avionics only
                vh = vz = 0.0
                power = i.avionicsPower
            # ---- battery: open-circuit voltage falls linearly with state of charge
            soc = 1 - used / capacity
            v_oc = i.batteryVoltage * (0.89 + 0.245 * soc)
            # current that delivers `power` behind the internal resistance:
            # P = (V_oc - I R) I  =>  R I² - V_oc I + P = 0
            disc = v_oc * v_oc - 4 * r_batt * power
            if disc < 0:  # beyond what the pack can deliver: count it, draw at the limit
                overload[name] = overload.get(name, 0.0) + h
                disc = 0.0
            # the smaller root is the physical operating point (the larger one is
            # past the maximum-power point, with most power lost in the pack)
            i_batt = (v_oc - math.sqrt(disc)) / (2 * r_batt)
            v_bus = v_oc - i_batt * r_batt               # terminal voltage seen by the ESCs
            # ---- record motor limits being exceeded (the run continues regardless)
            if op:
                op["throttle"] = op["motorVoltage"] / v_bus if v_bus > 0 else float("inf")
                if op["throttle"] > 1:
                    saturated[name] = saturated.get(name, 0.0) + h
                if op["current"] > motor.i_max:
                    overcurrent[name] = overcurrent.get(name, 0.0) + h
            # energy drawn: open-circuit voltage × current, so the pack's own
            # resistive loss is charged against capacity
            draw = v_oc * i_batt * h
            if used + draw >= capacity:
                # battery runs out partway through this slice: shorten the slice
                # to the moment it empties and stop the simulation there
                h *= (capacity - used) / draw if draw else 1
                draw = capacity - used
                depleted_at = t + h
            # ---- advance the state
            used += draw
            t += h
            remaining -= h
            alt = max(alt + vz * h, 0.0)                 # cannot descend below the ground
            dist += vh * h
            # ---- bookkeeping: accumulators, reserve crossing and timeline sample
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
        # payload drops after the step; mass is floored at 10 % of takeoff mass
        # so a payload heavier than the airframe cannot make it zero or negative
        if _flag(s, "releasesPayload", False):
            mass = max(mass - i.payloadMass, 0.1 * i.takeoffMass)

    # ---- results
    end_soc = 100 * (1 - used / capacity)
    tot = total.summary()
    # human-readable list of everything that makes the mission infeasible
    issues = []
    if depleted_at is not None:
        issues.append(f"battery empty at {depleted_at:.0f} s, during {steps_out[-1]['name']}")
    for label, d in (("above full throttle (motors cannot follow)", saturated), ("above the motors' maximum current", overcurrent),
                     ("more power than the battery delivers", overload)):
        if d:
            issues.append(f"{label} in " + ", ".join(f"{k} ({v:.0f} s)" for k, v in d.items()))
    if reserve_at:
        issues.append(f"below the {100 * i.reserve:.0f} % reserve at {reserve_at[0]:.0f} s, during {reserve_at[1]}")
    # the step most to blame, in order of severity: where the battery emptied,
    # else the first step that hit a throttle / current / power limit, else
    # where the reserve was crossed; "" if the mission is feasible
    limiting = (steps_out[-1]["name"] if depleted_at is not None else next(iter(saturated or overcurrent or overload), None)
                or (reserve_at[1] if reserve_at else ""))
    # downsample the timeline to about MAX_SAMPLES points, always keeping the last
    stride = max(1, math.ceil(len(samples) / MAX_SAMPLES))
    timeline = samples[::stride] + ([samples[-1]] if (len(samples) - 1) % stride else [])
    # hover rpm at takeoff mass, a reference line for the propeller plots
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


# ------------------------------------------------------------------ one component's curves
PROPELLER_PARAMS = ("ct0", "ct1", "ct2", "cp0", "cp1", "cp2")
MOTOR_PARAMS = ("kv", "resistance", "noLoadCurrent", "maxCurrent")


def component_curves(component: str, values: dict[str, Any], voltage: float | None = None) -> dict[str, Any]:
    """Performance curves of one propeller or motor, exactly as the simulation
    uses them, from its SysML attribute values (``PROPELLER_PARAMS`` or
    ``MOTOR_PARAMS``). A motor's full-throttle curves need the supply
    ``voltage``. Returns the curves and a few headline figures."""

    def get(name: str) -> float:
        v = values.get(name)
        if isinstance(v, bool) or v is None:
            raise SimulationError(f"{component} has no {name}")
        try:
            x = float(v)
        except (TypeError, ValueError) as exc:
            raise SimulationError(f"{name} is not a number") from exc
        if not math.isfinite(x):
            raise SimulationError(f"{name} must be finite")
        return x

    if component == "propeller":
        # the coefficients are dimensionless, so diameter and air density do not change the curves
        prop = Propeller(SimpleNamespace(propDiameter=1.0, airDensity=1.225, **{k: get(k) for k in PROPELLER_PARAMS}))
        curves = prop.curves()
        best = max(curves, key=lambda c: c["efficiency"])
        return {
            "curves": curves,
            "summary": {
                "zeroThrustJ": prop.j_max,
                "staticCT": prop.CT(0.0),
                "staticCP": prop.CP(0.0),
                "staticCTCP": prop.CT(0.0) / prop.CP(0.0),
                "maxEfficiency": best["efficiency"],
                "maxEfficiencyJ": best["J"],
            },
        }
    if component == "motor":
        if voltage is None or not voltage > 0:
            raise SimulationError("a motor's curves need a positive supply voltage")
        p = {k: get(k) for k in MOTOR_PARAMS}
        motor = Motor(SimpleNamespace(motorKv=p["kv"], motorResistance=p["resistance"], motorNoLoadCurrent=p["noLoadCurrent"], motorMaxCurrent=p["maxCurrent"]))
        curves = motor.curves(voltage)
        best = max(curves, key=lambda c: c["efficiency"])
        return {
            "curves": curves,
            "summary": {
                "voltage": voltage,
                "noLoadSpeed": p["kv"] * voltage,             # rpm, ideal (no load, no losses)
                "stallCurrent": voltage / p["resistance"] if p["resistance"] > 0 else None,
                "maxShaftPower": max(c["power"] for c in curves),
                "maxEfficiency": best["efficiency"],
                "maxEfficiencyCurrent": best["current"],
            },
        }
    raise SimulationError(f"unknown component '{component}': propeller or motor")
