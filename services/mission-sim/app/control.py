"""Closed-loop controllability simulation of a multirotor, and controller tuning.

A rigid body in 3-D (6 degrees of freedom) with one rotor per motor, flown by a
cascade of controllers as a flight controller runs them::

    speed, heading  -> velocity loop (PI)      -> acceleration, so a tilt and a thrust
    climb rate      -> vertical loop (PI)      -> vertical acceleration
    position        -> position loop (P)       -> speed (hover hold, gust rejection)
    tilt, heading   -> attitude loop (P), heading loop (P)  -> body-rate commands
    body rates      -> rate loops (PID roll / pitch, PI yaw)  -> body torques
    thrust, torques -> MIXER -> thrust per rotor -> rotor-speed command
    rotor speed     -> ESC speed loop (PI, per motor), or throttle only -> motor voltage

**Vehicle.** Body axes x forward, y left, z up. Rotor i sits at angle
``2πi/n + π/n`` on a circle of ``armLength`` (the X layout of the geometry
service) and spins in alternate directions, starting with ``firstRotorSpin``
(+1 counter-clockwise seen from above). Inertia is diagonal (``inertiaXX``,
``inertiaYY``, ``inertiaZZ``, from the architecture). Drag is
``½ ρ CdA |va| va`` on the airspeed ``va`` (ground speed minus wind).

**Rotors.** Thrust ``kT ω²`` and aerodynamic torque ``kQ ω²`` from the
propeller's static coefficients (``ct0``, ``cp0``; the dynamic mission
simulation covers advance ratio). The brushless motor of ``dynamics.Motor``
drives each rotor of inertia ``rotorInertia``: ``Jr dω/dt = Qm - kQ ω²``.
The ESC limits the current to ``motorMaxCurrent`` (braking allowed) and the
voltage to the battery's. The body feels each motor's torque, ``-s Qm`` about
z, which carries both the rotor's drag and its spin-up reaction, plus the
rotors' gyroscopic torque ``-ω × Σ s Jr Ω``.

**Mixer.** The allocation matrix ``A`` maps rotor thrusts to collective thrust
and body torques (``[1; y; -x; -s κ]`` per rotor, κ = kQ / kT); the mixer is its
pseudo-inverse. When a rotor would leave ``[0, Tmax]``, yaw gives way first,
then collective thrust, then roll and pitch.

**Tuning** (``mode = "tune"``), from the inside out, with open-loop tests as on a
test stand:

1. *Rotor*: the body held, a voltage step on one motor from hover. The speed
   response is fitted with a first-order model (gain, time constant); the ESC
   speed loop is a PI by internal model control (closed-loop time constant half
   the motor's).
2. *Body rates*: translation held, rotation free (a gimbal), the speed loops
   closed. An open-loop torque step about each axis; the rate response is
   fitted with ``b / (s (τ s + 1))`` (b = 1 / inertia). Roll and pitch get a
   PID, yaw a PI, by the SIMC rules for integrating processes, then the gain is
   lowered until the loop has at least 45° of phase margin.
3. *Attitude, heading, velocity, vertical, position* loops are set from the
   identified inner loops, each a quarter of the bandwidth of the loop inside it.

Stability margins (phase and gain) of every loop are computed from the
identified models and the gains in use.

**Verification** (``mode = "verify"`` with the model's gains, or after tuning
with the proposed ones): the control **tests** the model specifies, each a
requirement tagged ``#ControlTest`` (see ``fly_test``): a climb-rate, speed or
heading step, or a gust while holding position. Each reports rise and settling
time, overshoot, tracking error, deviation, tilt and the share of time a rotor
was at its limit, under ``testResults[requirement]`` for the requirement to be
checked with.

Not modelled: sensors and state estimation (the controller sees the true
state), blade flapping, ground effect, vortex ring state, battery sag. The
tuned gains are a sound starting point for flight tests, not flight-ready.
"""

from __future__ import annotations

import math
from dataclasses import MISSING, dataclass, fields
from types import SimpleNamespace
from typing import Any

import numpy as np

from .dynamics import G, IN, Motor, SimulationError

MAX_SERIES = 300  # points kept per recorded time series


# ------------------------------------------------------------------ inputs
@dataclass
class Vehicle:
    rotorCount: float
    takeoffMass: float           # kg, with the payload
    armLength: float             # m, centre to motor axis
    inertiaXX: float             # kg·m²
    inertiaYY: float
    inertiaZZ: float
    rotorInertia: float          # kg·m², propeller + motor rotor
    dragArea: float              # m²
    propDiameter: float          # in
    ct0: float
    cp0: float
    motorKv: float               # rpm/V
    motorResistance: float       # Ω
    motorNoLoadCurrent: float    # A
    motorMaxCurrent: float       # A
    batteryVoltage: float        # V
    firstRotorSpin: float = 1.0  # +1: rotor 1 turns counter-clockwise seen from above
    airDensity: float = 1.225
    maxTilt: float = 30.0        # deg
    maxYawRate: float = 90.0     # deg/s
    timeStep: float = 0.002      # s


@dataclass
class Gains:
    """Flight-controller gains (the attributes of a FlightController)."""

    rpmKp: float                 # V per rad/s
    rpmKi: float                 # V per rad
    rateKp: float                # N·m per rad/s (roll and pitch)
    rateKi: float                # N·m per rad
    rateKd: float                # N·m per rad/s²
    yawRateKp: float             # N·m per rad/s
    yawRateKi: float             # N·m per rad
    attitudeKp: float            # 1/s
    headingKp: float             # 1/s
    velocityKp: float            # 1/s
    velocityKi: float            # 1/s²
    verticalKp: float            # 1/s
    verticalKi: float            # 1/s²
    positionKp: float            # 1/s
    rpmLoop: bool = True         # the ESCs hold the commanded speed; false: throttle only


GAIN_NAMES = [f.name for f in fields(Gains) if f.name != "rpmLoop"]


def _vehicle(raw: dict[str, Any]) -> Vehicle:
    vals = {}
    for f in fields(Vehicle):
        v = raw.get(f.name)
        if v is None:
            if f.default is not MISSING:
                continue
            raise SimulationError(f"missing input: {f.name}")
        try:
            vals[f.name] = float(v)
        except (TypeError, ValueError) as exc:
            raise SimulationError(f"{f.name} is not a number") from exc
        if not math.isfinite(vals[f.name]):
            raise SimulationError(f"{f.name} must be finite")
    v = Vehicle(**vals)
    n = int(round(v.rotorCount))
    if n < 4 or n % 2:
        raise SimulationError("the controller needs an even number of rotors (4 or more) for yaw control by alternate spin")
    for name in ("takeoffMass", "armLength", "inertiaXX", "inertiaYY", "inertiaZZ", "rotorInertia", "propDiameter", "ct0", "cp0",
                 "motorKv", "motorMaxCurrent", "batteryVoltage", "timeStep"):
        if getattr(v, name) <= 0:
            raise SimulationError(f"{name} must be positive")
    return v


def _gains(raw: dict[str, Any]) -> Gains:
    vals: dict[str, Any] = {}
    for name in GAIN_NAMES:
        v = raw.get(name)
        if v is None or isinstance(v, bool):
            raise SimulationError(f"verify needs the controller's {name}: assign a FlightController (tune one first)")
        vals[name] = float(v)
    vals["rpmLoop"] = raw.get("rpmLoop") not in (False, "false")
    return Gains(**vals)


# ------------------------------------------------------------------ plant
class Plant:
    """Rotors, motors and rigid body, with the mixer for its geometry."""

    def __init__(self, v: Vehicle):
        self.v = v
        self.n = n = int(round(v.rotorCount))
        self.m = v.takeoffMass
        self.I = np.array([v.inertiaXX, v.inertiaYY, v.inertiaZZ])
        self.Jr = v.rotorInertia
        rho, D = v.airDensity, v.propDiameter * IN
        self.kT = v.ct0 * rho * D ** 4 / (2 * math.pi) ** 2      # N per (rad/s)²
        self.kQ = v.cp0 / (2 * math.pi) * rho * D ** 5 / (2 * math.pi) ** 2
        self.kappa = self.kQ / self.kT                              # m: yaw torque per newton of thrust
        self.motor = Motor(SimpleNamespace(motorKv=v.motorKv, motorResistance=v.motorResistance,
                                           motorNoLoadCurrent=v.motorNoLoadCurrent, motorMaxCurrent=v.motorMaxCurrent))
        self.kv, self.R, self.I0, self.imax = self.motor.kv, self.motor.R, self.motor.I0, self.motor.i_max
        self.vbat = v.batteryVoltage
        ang = 2 * math.pi * np.arange(n) / n + math.pi / n
        self.angles = ang
        self.x = v.armLength * np.cos(ang)
        self.y = v.armLength * np.sin(ang)
        self.spin = v.firstRotorSpin * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        # allocation: [T, τx, τy, τz] = A @ thrusts; mixer = pseudo-inverse
        self.A = np.vstack([np.ones(n), self.y, -self.x, -self.spin * self.kappa])
        self.M = np.linalg.pinv(self.A)
        # rotor speed limits: full battery voltage, or the current limit
        a, b, c = self.kQ, 1 / (self.R * self.kv ** 2) if self.R > 0 else 0.0, -(self.vbat / self.R - self.I0) / self.kv if self.R > 0 else 0.0
        w_volt = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a) if self.R > 0 else self.vbat * self.kv
        w_curr = math.sqrt(max(self.imax - self.I0, 0) / self.kv / self.kQ)
        self.w_max = min(w_volt, w_curr)
        self.T_max = self.kT * self.w_max ** 2
        self.T_hover = self.m * G / n
        if self.T_hover >= self.T_max:
            raise SimulationError(f"the rotors cannot lift {self.m:.1f} kg: {n} x {self.T_max:.1f} N at most")
        self.w_hover = math.sqrt(self.T_hover / self.kT)

    def u_ff(self, w: np.ndarray) -> np.ndarray:
        """Steady-state motor voltage for rotor speed ``w`` (the throttle map)."""
        cur = self.kQ * w * w * self.kv + self.I0
        return w / self.kv + cur * self.R

    def max_yaw_torque(self, thrust_each: float) -> float:
        """Largest yaw torque with every rotor kept in [0, Tmax] around ``thrust_each``."""
        return self.n * self.kappa * min(self.T_max - thrust_each, thrust_each)

    def max_roll_torque(self, thrust_each: float) -> float:
        d = min(self.T_max - thrust_each, thrust_each)
        return float(np.sum(np.abs(self.y)) * d)


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _rot(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cross product of 3-vectors (np.cross is slow on tiny arrays)."""
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class State:
    def __init__(self, plant: Plant):
        self.p = np.zeros(3)
        self.v = np.zeros(3)
        self.q = np.array([1.0, 0, 0, 0])
        self.w = np.zeros(3)
        self.rotor = np.full(plant.n, plant.w_hover)


def step_plant(pl: Plant, s: State, u: np.ndarray, dt: float, wind: np.ndarray, hold: str = "free") -> dict[str, Any]:
    """Advance the plant by ``dt`` with motor voltages ``u``. ``hold``: "free";
    "gimbal" (translation held); "stand" (the body held)."""
    w = s.rotor
    cur = np.minimum(np.maximum((u - w / pl.kv) / pl.R if pl.R > 0 else np.full(pl.n, pl.imax), -pl.imax), pl.imax)
    Qm = (cur - pl.I0 * np.sign(w)) / pl.kv
    Qa = pl.kQ * w * np.abs(w)
    s.rotor = w + (Qm - Qa) / pl.Jr * dt
    T = pl.kT * s.rotor * np.abs(s.rotor)
    if hold != "stand":
        tau = np.array([np.dot(pl.y, T), -np.dot(pl.x, T), -np.dot(pl.spin, Qm)])
        h_r = np.array([0.0, 0.0, pl.Jr * np.dot(pl.spin, s.rotor)])
        tau = tau - _cross(s.w, h_r)
        wdot = (tau - _cross(s.w, pl.I * s.w)) / pl.I
        s.w = s.w + wdot * dt
        dq = np.concatenate([[1.0], 0.5 * s.w * dt])
        s.q = _quat_mul(s.q, dq)
        s.q /= math.sqrt(float(s.q @ s.q))
    if hold == "free":
        R = _rot(s.q)
        va = s.v - wind
        drag = -0.5 * pl.v.airDensity * pl.v.dragArea * math.sqrt(float(va @ va)) * va
        acc = (R[:, 2] * T.sum() + drag) / pl.m - np.array([0, 0, G])
        s.v = s.v + acc * dt
        s.p = s.p + s.v * dt
    return {"current": cur, "voltage": u, "thrust": T}


# ------------------------------------------------------------------ controller
class Controller:
    """The cascade of the module docstring, with integrator anti-windup."""

    def __init__(self, pl: Plant, g: Gains, dt: float):
        self.pl, self.g, self.dt = pl, g, dt
        self.i_vel = np.zeros(2)
        self.i_vz = 0.0
        self.i_rate = np.zeros(3)
        self.i_rpm = np.zeros(pl.n)
        self.w_prev = np.zeros(3)
        self.d_filt = np.zeros(3)
        self.saturated = False

    def velocity_ref(self, s: State, ref: dict[str, Any]) -> tuple[np.ndarray, float]:
        g = self.g
        if ref.get("position") is not None:
            e = np.asarray(ref["position"][:2]) - s.p[:2]
            vh = g.positionKp * e
            speed = math.hypot(vh[0], vh[1])
            vmax = 5.0
            if speed > vmax:
                vh *= vmax / speed
            vz = g.positionKp * (ref["position"][2] - s.p[2])
            return vh, vz
        return np.asarray(ref.get("velocity", (0.0, 0.0)), dtype=float), float(ref.get("vz", 0.0))

    def __call__(self, s: State, ref: dict[str, Any], hold: str = "free") -> np.ndarray:
        pl, g, dt = self.pl, self.g, self.dt
        R = _rot(s.q)
        max_tilt = math.radians(pl.v.maxTilt)
        if hold == "free":
            vh_ref, vz_ref = self.velocity_ref(s, ref)
            # velocity and vertical loops: accelerations
            e = vh_ref - s.v[:2]
            a_h = g.velocityKp * e + g.velocityKi * self.i_vel
            a_lim = G * math.tan(max_tilt)
            a_norm = math.hypot(a_h[0], a_h[1])
            if a_norm < a_lim:
                self.i_vel += e * dt
            else:
                a_h *= a_lim / a_norm
            ez = vz_ref - s.v[2]
            a_z = g.verticalKp * ez + g.verticalKi * self.i_vz
            a_z = min(max(float(a_z), -0.6 * G), 0.6 * G)
            if abs(a_z) < 0.6 * G:
                self.i_vz += ez * dt
            f = pl.m * np.array([a_h[0], a_h[1], a_z + G])
            b3 = f / math.sqrt(float(f @ f))
            if b3[2] < math.cos(max_tilt):  # tilt limit on the thrust direction itself
                h = math.hypot(b3[0], b3[1])
                b3 = np.array([b3[0] / h * math.sin(max_tilt), b3[1] / h * math.sin(max_tilt), math.cos(max_tilt)])
            thrust = float(np.dot(f, R[:, 2]))
            psi = ref.get("heading", 0.0)
            b1c = np.array([math.cos(psi), math.sin(psi), 0.0])
            b2 = _cross(b3, b1c)
            b2 /= math.sqrt(float(b2 @ b2))
            Rd = np.array([_cross(b2, b3), b2, b3]).T
            E = Rd.T @ R - R.T @ Rd
            eR = 0.5 * np.array([E[2, 1], E[0, 2], E[1, 0]])  # attitude error: vee of the skew part
            rate_ref_xy = -g.attitudeKp * eR[:2]
        else:
            thrust = pl.m * G
            rate_ref_xy = np.asarray(ref.get("rates", (0.0, 0.0, 0.0))[:2], dtype=float)
        # heading loop
        if hold == "free" or ref.get("heading") is not None:
            yaw = math.atan2(R[1, 0], R[0, 0])
            yaw_rate = g.headingKp * _wrap(ref.get("heading", 0.0) - yaw) + ref.get("yawRate", 0.0)
        else:
            yaw_rate = ref.get("rates", (0.0, 0.0, 0.0))[2]
        ymax = math.radians(pl.v.maxYawRate)
        yaw_rate = min(max(float(yaw_rate), -ymax), ymax)
        rate_ref = np.array([rate_ref_xy[0], rate_ref_xy[1], yaw_rate])
        # rate loops: PID roll / pitch (derivative on the measurement, filtered), PI yaw
        e = rate_ref - s.w
        wdot = (s.w - self.w_prev) / dt
        self.w_prev = s.w.copy()
        alpha = dt / (dt + 0.01)
        self.d_filt += alpha * (wdot - self.d_filt)
        kp = np.array([g.rateKp, g.rateKp, g.yawRateKp])
        ki = np.array([g.rateKi, g.rateKi, g.yawRateKi])
        kd = np.array([g.rateKd, g.rateKd, 0.0])
        tau = kp * e + ki * self.i_rate - kd * self.d_filt
        if ref.get("torque") is not None:  # open-loop torque test
            tau = np.asarray(ref["torque"], dtype=float)
        # mixer with desaturation: roll / pitch first, then collective, then yaw
        T0 = pl.M @ np.array([thrust, 0, 0, 0])
        Trp = pl.M @ np.array([0, tau[0], tau[1], 0])
        Ty = pl.M @ np.array([0, 0, 0, tau[2]])
        lo, hi = 0.0, pl.T_max
        sat = False
        span = Trp.max() - Trp.min()
        if span > hi - lo:
            Trp *= (hi - lo) / span
            sat = True
        base = T0 + Trp
        if base.max() > hi:
            base -= base.max() - hi
            sat = True
        if base.min() < lo:
            base += lo - base.min()
            sat = True
        k = 1.0
        for i in range(pl.n):
            if Ty[i] > 1e-12:
                k = min(k, (hi - base[i]) / Ty[i])
            elif Ty[i] < -1e-12:
                k = min(k, (lo - base[i]) / Ty[i])
        k = max(k, 0.0)
        if k < 1.0:
            sat = True
        T = np.minimum(np.maximum(base + k * Ty, lo), hi)
        # integrators wind only while the mixer is not saturated
        if not sat:
            self.i_rate += e * dt
        self.saturated = sat
        w_cmd = np.sqrt(T / pl.kT)
        # ESC: speed loop (PI on the throttle map) or throttle only
        u = pl.u_ff(w_cmd)
        if g.rpmLoop:
            er = w_cmd - s.rotor
            u = u + g.rpmKp * er + g.rpmKi * self.i_rpm
            inside = (u > 0) & (u < pl.vbat)
            self.i_rpm += np.where(inside, er * dt, 0.0)
        u = np.minimum(np.maximum(u, 0.0), pl.vbat)
        if np.any(u >= pl.vbat - 1e-9):
            self.saturated = True
        return u


# ------------------------------------------------------------------ identification
def _fit_first_order(t: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(final change, time constant) of a step response starting at t = 0."""
    dy = y[-1] - y[0]
    target = y[0] + 0.632 * dy
    i = int(np.argmax((y - target) * np.sign(dy) >= 0))
    if i == 0:
        return dy, t[1]
    tau = t[i - 1] + (target - y[i - 1]) / (y[i] - y[i - 1]) * (t[i] - t[i - 1])
    return dy, max(tau, t[1])


def _fit_integrating(t: np.ndarray, y: np.ndarray, step: float, min_lag: float) -> tuple[float, float]:
    """(b, τ) of an integrator with a lag, ``y ≈ b·step·(t - τ)`` once the lag has
    passed: b from the slope of the second half of the response, τ from its
    intercept, at least ``min_lag``."""
    half = t >= t[-1] / 2
    slope, intercept = np.polyfit(t[half], y[half], 1)
    b = float(slope / step)
    tau = float(-intercept / slope) if slope else min_lag
    return b, max(tau, min_lag)


def _series(t: np.ndarray, **cols: np.ndarray) -> list[dict[str, float]]:
    stride = max(1, math.ceil(len(t) / MAX_SERIES))
    idx = list(range(0, len(t), stride))
    if idx[-1] != len(t) - 1:
        idx.append(len(t) - 1)
    return [{"t": float(t[i]), **{k: float(v[i]) for k, v in cols.items()}} for i in idx]


def identify(pl: Plant, dt: float) -> dict[str, Any]:
    """Open-loop tests: a voltage step on a held rotor, then torque steps on a gimbal."""
    out: dict[str, Any] = {}
    # 1. rotor: the body held, motor 1 stepped by 10 % of the battery voltage from hover
    s = State(pl)
    u = pl.u_ff(s.rotor)
    du = 0.1 * pl.vbat
    if u[0] + du > pl.vbat:
        du = 0.5 * (pl.vbat - u[0])
    tt, ww = [], []
    t = 0.0
    horizon = 0.6
    while t < horizon:
        uu = u.copy()
        uu[0] += du
        step_plant(pl, s, uu, dt, np.zeros(3), hold="stand")
        t += dt
        tt.append(t)
        ww.append(s.rotor[0])
    tt_a, ww_a = np.array([0.0, *tt]), np.array([pl.w_hover, *ww])
    dw, tau_m = _fit_first_order(tt_a, ww_a)
    model = pl.w_hover + dw * (1 - np.exp(-tt_a / tau_m))
    out["rotor"] = {
        "gain": dw / du, "timeConstant": tau_m, "step": du, "hoverRpm": pl.w_hover * 60 / (2 * math.pi),
        "test": _series(tt_a, measured=ww_a * 60 / (2 * math.pi), model=model * 60 / (2 * math.pi)),
    }
    return out


def identify_body(pl: Plant, g_rpm: tuple[float, float, bool], dt: float, rotor_lag: float) -> dict[str, Any]:
    """Torque steps about each axis on a gimbal, the rotor speed loops closed (or
    throttle only). Every body torque but the motors' brief reaction torque comes
    from a change of rotor speed (thrust differences for roll and pitch, drag
    torque for yaw), so the lag is at least ``rotor_lag``, the closed rotor
    loop's time constant."""
    out: dict[str, Any] = {}
    rpmKp, rpmKi, loop = g_rpm
    base = Gains(rpmKp, rpmKi, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, loop)
    for axis, name in ((0, "roll"), (1, "pitch"), (2, "yaw")):
        s = State(pl)
        ctl = Controller(pl, base, dt)
        if axis < 2:
            torque = 0.1 * pl.max_roll_torque(pl.T_hover)
        else:
            torque = 0.25 * pl.max_yaw_torque(pl.T_hover)
        # long enough to see the ramp, short enough to keep the tilt small
        horizon = min(0.6, math.sqrt(2 * math.radians(15) * pl.I[axis] / torque)) if axis < 2 else 0.6
        horizon = max(horizon, 0.15)
        tt, ww = [0.0], [0.0]
        t = 0.0
        tq = np.zeros(3)
        tq[axis] = torque
        while t < horizon:
            u = ctl(s, {"torque": tq}, hold="gimbal")
            step_plant(pl, s, u, dt, np.zeros(3), hold="gimbal")
            t += dt
            tt.append(t)
            ww.append(s.w[axis])
        ta, wa = np.array(tt), np.array(ww)
        b, tau = _fit_integrating(ta, wa, torque, rotor_lag)
        model = b * torque * (ta - tau * (1 - np.exp(-ta / tau)))
        out[name] = {
            "b": b, "inertiaEstimate": 1 / b if b > 0 else None, "lag": tau, "torque": torque,
            "test": _series(ta, measured=wa, model=model),
        }
    return out


# ------------------------------------------------------------------ loop analysis
OMEGA = np.geomspace(1e-2, 2e3, 4000)


def _margins(L: np.ndarray) -> dict[str, float | None]:
    """Phase margin (deg), gain margin (dB) and crossover (rad/s) of a loop's frequency response."""
    mag = np.abs(L)
    ph = np.unwrap(np.angle(L))
    pm = gm = wc = None
    i = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    if len(i):
        k = i[0]
        wc = float(OMEGA[k])
        pm = float(180 + math.degrees(ph[k]))
    j = np.where((ph[:-1] > -math.pi) & (ph[1:] <= -math.pi))[0]
    if len(j):
        gm = float(-20 * math.log10(max(mag[j[0]], 1e-12)))
    return {"phaseMargin": pm, "gainMargin": gm, "crossover": wc}


def analyse(ident: dict[str, Any], g: Gains, dt: float) -> dict[str, Any]:
    """Each loop's margins, from the identified models and the gains, with a sample of delay."""
    s = 1j * OMEGA
    delay = np.exp(-s * dt)
    r = ident["rotor"]
    out: dict[str, Any] = {}
    # rotor speed loop
    P_rot = r["gain"] / (r["timeConstant"] * s + 1)
    if g.rpmLoop:
        L = (g.rpmKp + g.rpmKi / s) * P_rot * delay
        out["rpm"] = _margins(L)
        T_rot = L / (1 + L)
    else:
        T_rot = 1 / (r["timeConstant"] * s + 1)
    # rate loops: the identified torque-to-rate model (its lag includes the rotor loop)
    T_rate = {}
    for name, kp, ki, kd in (("roll", g.rateKp, g.rateKi, g.rateKd), ("pitch", g.rateKp, g.rateKi, g.rateKd), ("yaw", g.yawRateKp, g.yawRateKi, 0.0)):
        m = ident[name]
        P = m["b"] / (s * (m["lag"] * s + 1))
        C = kp + ki / s + kd * s / (0.01 * s + 1)
        L = C * P * delay
        out[name] = _margins(L)
        T_rate[name] = L / (1 + L)
    # attitude and heading: P on an integrator of the closed rate loop
    L_att = g.attitudeKp * T_rate["roll"] / s
    out["attitude"] = _margins(L_att)
    T_att = L_att / (1 + L_att)
    out["heading"] = _margins(g.headingKp * T_rate["yaw"] / s)
    # velocity: acceleration command -> speed through the attitude loop
    out["velocity"] = _margins((g.velocityKp + g.velocityKi / s) * T_att / s)
    # vertical: acceleration command -> climb rate through the rotor loop
    out["vertical"] = _margins((g.verticalKp + g.verticalKi / s) * T_rot / s)
    return out


def tune(pl: Plant, dt: float, rpm_loop: bool = True) -> tuple[Gains, dict[str, Any]]:
    """Identify inside-out and choose the gains (see the module docstring)."""
    ident = identify(pl, dt)
    r = ident["rotor"]
    tau_m, k_m = r["timeConstant"], r["gain"]
    lam = 0.5 * tau_m
    rpmKp = tau_m / (k_m * lam)
    rpmKi = rpmKp / tau_m
    ident |= identify_body(pl, (rpmKp, rpmKi, rpm_loop), dt, lam if rpm_loop else tau_m)
    gains: dict[str, float] = {"rpmKp": rpmKp, "rpmKi": rpmKi}

    def pid_for(m: dict[str, Any], derivative: bool) -> tuple[float, float, float]:
        theta = m["lag"] + dt
        kc = 1 / (m["b"] * 2 * theta)          # SIMC, integrating process, λ = θ
        ti = 8 * theta
        kd = 0.25 * kc * m["lag"] if derivative else 0.0
        return kc, kc / ti, kd

    def pm_of(name: str, kp: float, ki: float, kd: float) -> float:
        m = ident[name]
        s = 1j * OMEGA
        L = (kp + ki / s + kd * s / (0.01 * s + 1)) * m["b"] / (s * (m["lag"] * s + 1)) * np.exp(-s * dt)
        return _margins(L)["phaseMargin"] or 0.0

    for name, keys, deriv in (("roll", ("rateKp", "rateKi", "rateKd"), True), ("yaw", ("yawRateKp", "yawRateKi", None), False)):
        kp, ki, kd = pid_for(ident[name], deriv)
        for _ in range(30):  # lower the gain until the loop has 45° of phase margin
            if pm_of(name, kp, ki, kd) >= 45:
                break
            kp, ki, kd = 0.85 * kp, 0.85 * ki, 0.85 * kd
        gains[keys[0]], gains[keys[1]] = kp, ki
        if keys[2]:
            gains[keys[2]] = kd
    g0 = Gains(**gains, attitudeKp=0, headingKp=0, velocityKp=0, velocityKi=0, verticalKp=0, verticalKi=0, positionKp=0, rpmLoop=rpm_loop)
    first = analyse(ident, g0, dt)
    wc_rate = first["roll"]["crossover"] or 10.0
    wc_yaw = first["yaw"]["crossover"] or 5.0
    gains["attitudeKp"] = wc_rate / 4
    gains["headingKp"] = wc_yaw / 4
    # velocity: integrator through the attitude loop (time constant 1 / attitudeKp), SIMC with λ = 2θ
    theta = 1 / gains["attitudeKp"]
    gains["velocityKp"] = 1 / (3 * theta)
    gains["velocityKi"] = gains["velocityKp"] / (4 * 3 * theta)
    # vertical: integrator through the rotor loop, gentler (λ = 3θ) to keep thrust headroom
    theta_v = (lam if rpm_loop else tau_m) + dt
    theta_v = max(theta_v, 0.1)
    gains["verticalKp"] = 1 / (4 * theta_v)
    gains["verticalKi"] = gains["verticalKp"] / (16 * theta_v)
    gains["positionKp"] = 1 / (4 * 3 * theta)
    return Gains(**gains, rpmLoop=rpm_loop), ident


# ------------------------------------------------------------------ manoeuvres
def _step_metrics(t: np.ndarray, y: np.ndarray, t0: float, target: float, start: float = 0.0, band: float = 0.05, t1: float = math.inf) -> dict[str, float | None]:
    """Rise (10-90 %), settling (within ``band`` of the step), overshoot and final
    error of a step at ``t0``, held until ``t1``."""
    sel = (t >= t0) & (t < t1)
    tt, yy = t[sel] - t0, y[sel]
    d = target - start
    if abs(d) < 1e-9:
        return {"riseTime": None, "settlingTime": None, "overshoot": None, "finalError": float(abs(yy[-1] - target))}
    frac = (yy - start) / d
    i10 = np.argmax(frac >= 0.1)
    i90 = np.argmax(frac >= 0.9)
    rise = float(tt[i90] - tt[i10]) if frac.max() >= 0.9 else None
    outside = np.where(np.abs(frac - 1) > band)[0]
    settle = float(tt[outside[-1] + 1]) if len(outside) and outside[-1] + 1 < len(tt) else (0.0 if not len(outside) else None)
    over = float(max(0.0, (frac.max() - 1) * 100))
    return {"riseTime": rise, "settlingTime": settle, "overshoot": over, "finalError": float(abs(yy[-1] - target))}


def _fly(pl: Plant, g: Gains, dt: float, duration: float, ref_at, wind_at=None) -> dict[str, np.ndarray]:
    s = State(pl)
    ctl = Controller(pl, g, dt)
    n = int(round(duration / dt))
    rec = {k: np.zeros(n) for k in ("t", "vx", "vz", "speed", "heading", "x", "y", "z", "tilt", "yawRate", "rpmMin", "rpmMax", "throttle", "sat")}
    zero = np.zeros(3)
    for k in range(n):
        t = k * dt
        ref = ref_at(t)
        u = ctl(s, ref)
        step_plant(pl, s, u, dt, wind_at(t) if wind_at else zero)
        R = _rot(s.q)
        rec["t"][k] = t + dt
        rec["vx"][k], rec["vz"][k] = s.v[0], s.v[2]
        rec["speed"][k] = math.hypot(s.v[0], s.v[1])
        rec["heading"][k] = math.degrees(math.atan2(R[1, 0], R[0, 0]))
        rec["x"][k], rec["y"][k], rec["z"][k] = s.p
        rec["tilt"][k] = math.degrees(math.acos(max(-1.0, min(1.0, R[2, 2]))))
        rec["yawRate"][k] = math.degrees(s.w[2])
        rpm = s.rotor * 60 / (2 * math.pi)
        rec["rpmMin"][k], rec["rpmMax"][k] = rpm.min(), rpm.max()
        rec["throttle"][k] = float(u.max() / pl.vbat)
        rec["sat"][k] = 1.0 if ctl.saturated else 0.0
        if not np.all(np.isfinite(s.v)) or np.linalg.norm(s.p) > 1e4:
            raise SimulationError("the simulation diverged: the controller does not stabilise the vehicle")
    return rec


COMMANDS = {
    "climbRate": ("climb rate", "m/s"),
    "speed": ("ground speed", "m/s"),
    "heading": ("heading", "deg"),
    "gust": ("position error", "m"),
}


def _num_or(v: Any, default: float | None) -> float | None:
    if isinstance(v, bool) or v is None:
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def fly_test(pl: Plant, g: Gains, test: dict[str, Any], dt: float) -> dict[str, Any]:
    """Fly one control test from a settled hover (1 s), as its requirement specifies:

    * ``climbRate``: a step of vertical speed to ``step`` m/s, held ``hold`` s, then 0;
    * ``speed``: a step of ground speed to ``step`` m/s along the heading, held ``hold`` s;
    * ``heading``: a turn of ``step`` degrees at ``rate`` deg/s (a step without a rate),
      then held ``hold`` s; settling and overshoot count from the end of the turn;
    * ``gust``: holding position, a step of ``step`` m/s of wind, held ``hold`` s.
    """
    v = pl.v
    t0 = 1.0
    cmd = str(test.get("command", "")).strip('"')
    step = _num_or(test.get("step"), None)
    hold = _num_or(test.get("hold"), 6.0) or 6.0
    if cmd not in COMMANDS:
        raise SimulationError(f"unknown command '{cmd}': {', '.join(COMMANDS)}")
    if step is None:
        raise SimulationError("the test has no step")
    quantity, unit = COMMANDS[cmd]
    out: dict[str, Any] = {}
    if cmd == "climbRate":
        t1 = t0 + hold
        rec = _fly(pl, g, dt, t1 + 2.0, lambda t: {"vz": step if t0 <= t < t1 else 0.0})
        out = _step_metrics(rec["t"], rec["vz"], t0, step, t1=t1)
        series = _series(rec["t"], reference=np.where((rec["t"] >= t0) & (rec["t"] < t1), step, 0.0), actual=rec["vz"], throttle=rec["throttle"])
        name = f"climb rate step to {step:g} m/s"
    elif cmd == "speed":
        rec = _fly(pl, g, dt, t0 + hold, lambda t: {"velocity": (step if t >= t0 else 0.0, 0.0)})
        out = _step_metrics(rec["t"], rec["speed"], t0, step)
        series = _series(rec["t"], reference=np.where(rec["t"] >= t0, step, 0.0), actual=rec["speed"], tilt=rec["tilt"], throttle=rec["throttle"])
        name = f"speed step to {step:g} m/s"
    elif cmd == "heading":
        rate_deg = _num_or(test.get("rate"), None)
        rate = math.radians(min(rate_deg, v.maxYawRate)) if rate_deg and rate_deg > 0 else None
        angle = math.radians(step)
        t_end = t0 + (abs(angle) / rate if rate else 0.0)
        sign = 1.0 if angle >= 0 else -1.0

        def ref_turn(t: float) -> dict[str, Any]:
            if t < t0:
                return {"heading": 0.0}
            if t < t_end:
                return {"heading": sign * rate * (t - t0), "yawRate": sign * rate}
            return {"heading": angle}

        rec = _fly(pl, g, dt, t_end + hold, ref_turn)
        rec["heading"] = np.degrees(np.unwrap(np.radians(rec["heading"])))  # continuous through ±180°
        ref = np.where(rec["t"] < t0, 0.0, np.where(rec["t"] < t_end, sign * math.degrees(rate or 0) * (rec["t"] - t0), step))
        after = rec["t"] >= t_end
        err = rec["heading"][after] - step
        outside = np.where(np.abs(err) > 2.0)[0]
        tt = rec["t"][after] - t_end
        settle = 0.0 if not len(outside) else (float(tt[outside[-1] + 1]) if outside[-1] + 1 < len(tt) else None)
        beyond = (rec["heading"][after] - step) * sign
        out = {
            "riseTime": None, "settlingTime": settle,
            "overshoot": float(max(0.0, beyond.max()) / abs(step) * 100) if step else None,
            "finalError": float(abs(err[-1])), "maxTrackingError": float(np.max(np.abs(ref - rec["heading"]))),
        }
        series = _series(rec["t"], reference=ref, actual=rec["heading"], yawRate=rec["yawRate"], throttle=rec["throttle"])
        name = f"{step:g}° turn" + (f" at {math.degrees(rate):g}°/s" if rate else "")
    else:  # gust
        wind = np.array([step, 0.0, 0.0])
        rec = _fly(pl, g, dt, t0 + hold, lambda t: {"position": (0.0, 0.0, 0.0)}, lambda t: wind if t >= t0 else np.zeros(3))
        dev = np.hypot(np.hypot(rec["x"], rec["y"]), rec["z"])
        out = {"riseTime": None, "settlingTime": None, "overshoot": None, "finalError": float(dev[-1]), "maxDeviation": float(dev.max())}
        series = _series(rec["t"], reference=np.zeros_like(rec["t"]), actual=dev, tilt=rec["tilt"], throttle=rec["throttle"])
        name = f"{step:g} m/s gust, holding position"
    return {
        "requirement": test.get("requirement"), "name": name, "command": cmd, "step": step, "quantity": quantity, "unit": unit,
        **out, "maxTilt": float(rec["tilt"].max()), "maxThrottle": float(rec["throttle"].max()),
        "saturation": float(100 * rec["sat"].mean()), "series": series,
    }


# ------------------------------------------------------------------ entry point
def simulate(raw: dict[str, Any]) -> dict[str, Any]:
    """``raw``: the vehicle inputs, ``tests`` (the control tests to fly, each with
    its ``requirement``, ``command``, ``step``, ``rate`` and ``hold``), ``mode``
    ("tune" or "verify") and, to verify, the gains."""
    mode = str(raw.get("mode", "verify")).strip('"')  # a SysML string evaluates with its quotes
    if mode not in ("tune", "verify"):
        raise SimulationError("mode is tune or verify")
    v = _vehicle(raw)
    pl = Plant(v)
    dt = v.timeStep
    rpm_loop = raw.get("rpmLoop") not in (False, "false")
    if mode == "tune":
        gains, ident = tune(pl, dt, rpm_loop)
    else:
        gains = _gains(raw)
        ident = identify(pl, dt)
        r = ident["rotor"]
        # the closed rotor loop's time constant: the rotor's own without the speed loop
        rotor_lag = r["timeConstant"] / (1 + gains.rpmKp * r["gain"]) if gains.rpmLoop else r["timeConstant"]
        ident |= identify_body(pl, (gains.rpmKp, gains.rpmKi, gains.rpmLoop), dt, rotor_lag)
    margins = analyse(ident, gains, dt)
    tests = raw.get("tests") or []
    if not isinstance(tests, list):
        raise SimulationError("tests must be a list")
    issues: list[str] = []
    scen: list[dict[str, Any]] = []
    completed = True
    for test in tests:
        label = str(test.get("name") or test.get("requirement") or "test")
        try:
            scen.append(fly_test(pl, gains, test, dt))
        except SimulationError as exc:  # this test fails; the others still fly
            completed = False
            issues.append(f"{label}: {exc}")
            scen.append({"requirement": test.get("requirement"), "name": label, "command": test.get("command"), "error": str(exc), "series": []})
    flown = [x for x in scen if "error" not in x]
    pms = [m["phaseMargin"] for m in margins.values() if m["phaseMargin"] is not None]
    gms = [m["gainMargin"] for m in margins.values() if m["gainMargin"] is not None]

    def worst(key: str, command: str) -> float | None:
        vals = [x[key] for x in flown if x["command"] == command and x.get(key) is not None]
        return max(vals) if vals else None

    # yaw authority: the yaw acceleration available at hover against reaching the
    # fastest commanded turn rate in half a second (45°/s without a turn test)
    rates = [_num_or(t.get("rate"), None) for t in tests if str(t.get("command", "")).strip('"') == "heading"]
    yaw_cmd = math.radians(min(max([r for r in rates if r] or [45.0]), v.maxYawRate))
    yaw_acc = pl.max_yaw_torque(pl.T_hover) / pl.I[2]
    saturation = max([x["saturation"] for x in flown] or [0.0])
    for x in flown:
        if x["saturation"] > 5:
            issues.append(f"{x['name']}: a rotor at its limit {x['saturation']:.0f} % of the time")
        if x.get("settlingTime") is None and x["command"] != "gust":
            issues.append(f"{x['name']}: does not settle")
    metrics = ("riseTime", "settlingTime", "overshoot", "finalError", "maxDeviation", "maxTrackingError", "maxTilt", "maxThrottle", "saturation")
    result = {
        "mode": mode,
        "gains": {k: getattr(gains, k) for k in GAIN_NAMES} | {"rpmLoop": gains.rpmLoop},
        **{k: getattr(gains, k) for k in GAIN_NAMES},  # tuned, or the ones verified
        "mixer": {
            "rotors": [{"index": i + 1, "angle": math.degrees(float(pl.angles[i])), "x": float(pl.x[i]), "y": float(pl.y[i]), "spin": "ccw" if pl.spin[i] > 0 else "cw"} for i in range(pl.n)],
            "allocation": pl.A.tolist(),   # [T, τx, τy, τz] = allocation @ rotor thrusts
            "mixer": pl.M.tolist(),        # rotor thrusts = mixer @ [T, τx, τy, τz]
            "yawPerThrust": pl.kappa,
        },
        "hover": {
            "thrustPerRotor": pl.T_hover, "maxThrustPerRotor": pl.T_max, "rpm": pl.w_hover * 60 / (2 * math.pi),
            "maxRpm": pl.w_max * 60 / (2 * math.pi), "throttle": float(pl.u_ff(np.array([pl.w_hover]))[0] / pl.vbat),
        },
        "identification": ident,
        "margins": margins,
        "phaseMargin": min(pms) if pms else None,
        "gainMargin": min(gms) if gms else None,
        "thrustMargin": pl.T_max / pl.T_hover,
        "yawAuthority": yaw_acc / (yaw_cmd / 0.5),
        "climbSettlingTime": worst("settlingTime", "climbRate"),
        "speedSettlingTime": worst("settlingTime", "speed"),
        "speedOvershoot": worst("overshoot", "speed"),
        "headingSettlingTime": worst("settlingTime", "heading"),
        "gustDeviation": worst("maxDeviation", "gust"),
        "saturation": saturation,
        "completed": completed,
        "issues": issues,
        # each test's results, by the requirement that specifies it: what the requirement is checked with
        "testResults": {x["requirement"]: {k: x.get(k) for k in metrics} for x in flown if x.get("requirement")},
        "scenarios": scen,
    }
    return result
