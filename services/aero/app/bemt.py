"""Blade-element momentum theory (BEMT) for a fixed-pitch propeller in axial flow.

Each blade element (radius r, chord c, pitch angle θ) sees the axial speed V
plus the induced velocity vi, and the rotational speed Ωr less the swirl ut:

    φ = atan2(V + vi, Ωr − ut)        α = θ − φ        W² = (V + vi)² + (Ωr − ut)²
    dT = ½ ρ W² B c (cl cos φ − cd sin φ) dr
    dQ = ½ ρ W² B c (cl sin φ + cd cos φ) r dr

Momentum through the annulus, with Prandtl's tip and hub losses F, gives

    dT = 4π r ρ F (V + vi) vi dr       dQ = 4π r² ρ F (V + vi) ut dr

and the two are solved for vi and ut by relaxed fixed-point iteration. Written in
induced velocities (not induction factors), it holds at V = 0, the static case
a multirotor hovers near.

The section polars come from XFOIL at each station's Reynolds and Mach numbers
at a reference rotor speed (``tip_speed``), as the propeller turns in hover;
between stations the coefficients are interpolated by radius. Advance ratio
J = V / (n D); CT = T / (ρ n² D⁴); CP = P / (ρ n³ D⁵); η = J CT / CP.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from . import polar as polars
from . import xfoil

INCH = 0.0254
SPEED_OF_SOUND = 340.3  # m/s, sea level


class AeroError(ValueError):
    pass


@dataclass
class Blade:
    diameter: float  # m
    blades: int
    stations: np.ndarray  # r/R, ascending
    chord: np.ndarray  # c/R
    twist: np.ndarray  # deg: chord line to the rotation plane
    airfoils: list[str]
    hub: float  # r/R where the blade starts

    @property
    def radius(self) -> float:
        return self.diameter / 2

    @property
    def aspect_ratio(self) -> float:
        span = 1.0 - self.hub
        mean_chord = float(np.trapezoid(self.chord, self.stations) / max(self.stations[-1] - self.stations[0], 1e-9))
        return span / max(mean_chord, 1e-6)

    @classmethod
    def build(cls, diameter_in: float, blades: float, stations, chord, twist, airfoil, hub: float | None = None) -> "Blade":
        st = np.asarray(stations, dtype=float)
        ch = np.asarray(chord, dtype=float)
        tw = np.asarray(twist, dtype=float)
        n = len(st)
        if n < 3:
            raise AeroError("give at least 3 blade stations")
        if len(ch) != n or len(tw) != n:
            raise AeroError(f"stations, chord and twist need one value per station ({n}): got {len(ch)} chords, {len(tw)} twists")
        if np.any(np.diff(st) <= 0) or st[0] <= 0 or st[-1] > 1.0 + 1e-9:
            raise AeroError("stations are r/R values, ascending, in (0, 1]")
        if np.any(ch <= 0):
            raise AeroError("every chord must be positive")
        if diameter_in <= 0 or blades < 1:
            raise AeroError("diameter and blade count must be positive")
        foils = [airfoil] * n if isinstance(airfoil, str) else list(airfoil)
        if len(foils) != n:
            raise AeroError(f"give one airfoil, or one per station ({n})")
        return cls(diameter_in * INCH, int(round(blades)), st, ch, tw, foils, float(hub if hub is not None else st[0]))


@dataclass
class Section:
    """A station's polar, at the Reynolds and Mach numbers of the reference speed."""

    station: float
    airfoil: str
    polar: polars.Polar
    warning: str | None = None


@dataclass
class Result:
    curve: list[dict]
    fit: dict
    fit_range: tuple[float, float]
    fit_error: dict
    static: dict
    span: list[dict]
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)


def sections_for(blade: Blade, tip_speed: float, rho: float, mu: float, ncrit: float) -> list[Section]:
    """XFOIL polars at every station, run in parallel."""

    def one(i: int) -> Section:
        r = blade.stations[i]
        w = tip_speed * r  # hover inflow is small next to the blade speed
        c = blade.chord[i] * blade.radius
        re_ = rho * w * c / mu
        mach = w / SPEED_OF_SOUND
        warn = None
        if re_ < 3e4:
            warn = f"r/R {r:g}: Reynolds number {re_:.3g} is below XFOIL's useful range; computed at 30 000"
            re_ = 3e4
        if mach > 0.5:
            warn = f"r/R {r:g}: Mach {mach:.2f}: XFOIL's compressibility correction is computed at 0.5"
            mach = 0.5
        try:
            pts = xfoil.run_polar(blade.airfoils[i], re_, mach, ncrit)
            p = polars.from_points(pts, re_, mach, requested=31, aspect_ratio=blade.aspect_ratio)
        except (xfoil.XfoilError, ValueError) as exc:
            p = polars.thin_airfoil(re_, mach, blade.aspect_ratio)
            warn = f"r/R {r:g}: {exc}; a thin-airfoil polar is used instead"
        return Section(float(r), blade.airfoils[i], p, warn)

    with ThreadPoolExecutor(max_workers=min(8, len(blade.stations))) as pool:
        return list(pool.map(one, range(len(blade.stations))))


def _coefficients(sections: list[Section], r_rel: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """cl, cd of each element: its neighbouring stations' polars, interpolated by radius."""
    st = np.array([s.station for s in sections])
    cl = np.empty_like(alpha)
    cd = np.empty_like(alpha)
    j = np.clip(np.searchsorted(st, r_rel) - 1, 0, len(st) - 2)
    w = np.clip((r_rel - st[j]) / (st[j + 1] - st[j]), 0.0, 1.0)
    for k in range(len(st) - 1):
        m = j == k
        if not m.any():
            continue
        cl_a, cd_a = sections[k].polar.coefficients(alpha[m])
        cl_b, cd_b = sections[k + 1].polar.coefficients(alpha[m])
        cl[m] = (1 - w[m]) * cl_a + w[m] * cl_b
        cd[m] = (1 - w[m]) * cd_a + w[m] * cd_b
    return cl, cd


def solve(blade: Blade, sections: list[Section], n_rps: float, v: float, rho: float, elements: int = 40) -> dict:
    """Thrust, torque and the spanwise loads at rotor speed n (rev/s) and axial speed v (m/s)."""
    R = blade.radius
    # cosine spacing: finer towards the hub and the tip, where loads change fastest
    t = (1 - np.cos(np.linspace(0, math.pi, elements + 1))) / 2
    edges = blade.hub + (1.0 - blade.hub) * t
    r_rel = (edges[:-1] + edges[1:]) / 2
    dr = np.diff(edges) * R
    r = r_rel * R
    c = np.interp(r_rel, blade.stations, blade.chord) * R
    theta = np.radians(np.interp(r_rel, blade.stations, blade.twist))
    omega = 2 * math.pi * n_rps
    B = blade.blades

    vi = np.full_like(r, 0.05 * omega * R + 0.0)
    ut = np.zeros_like(r)
    # each element's own relaxation, halved whenever its correction changes sign
    # (an element oscillating between two states, typically at the hub)
    relax = np.full_like(r, 0.3)
    last = np.zeros_like(r)
    converged = False
    for _ in range(400):
        vax = v + vi
        vt = np.maximum(omega * r - ut, 1e-6)
        phi = np.arctan2(vax, vt)
        alpha = np.degrees(theta - phi)
        cl, cd = _coefficients(sections, r_rel, alpha)
        w2 = vax**2 + vt**2
        cx = cl * np.cos(phi) - cd * np.sin(phi)
        cy = cl * np.sin(phi) + cd * np.cos(phi)
        s = np.maximum(np.abs(np.sin(phi)), 1e-4)
        f_tip = 2 / math.pi * np.arccos(np.exp(-B / 2 * (1 - r_rel) / (r_rel * s)))
        f_hub = 2 / math.pi * np.arccos(np.exp(-B / 2 * (r_rel - blade.hub) / (r_rel * s)))
        # floored: near the hub and tip, a vanishing F makes the iteration stiff
        F = np.maximum(f_tip * f_hub, 0.02)
        k = 0.5 * w2 * B * c * cx / (4 * math.pi * r * F)
        disc = v * v + 4 * k
        vi_new = np.where(disc >= 0, (-v + np.sqrt(np.maximum(disc, 0))) / 2, -v / 2)
        ut_new = w2 * B * c * cy / (8 * math.pi * r * F * np.maximum(v + vi_new, 1e-3))
        # physical bounds: the flow through the disc does not reverse, the swirl stays below the blade speed
        vi_new = np.clip(vi_new, -0.5 * v, omega * R)
        ut_new = np.clip(ut_new, -0.5 * omega * r, 0.9 * omega * r)
        step = vi_new - vi
        delta = max(float(np.max(np.abs(step))), float(np.max(np.abs(ut_new - ut))))
        relax = np.where(step * last < 0, np.maximum(relax / 2, 0.005), relax)
        last = step
        vi += relax * step
        ut += relax * (ut_new - ut)
        if delta < 1e-5 * omega * R:
            converged = True
            break
    dT = 0.5 * rho * w2 * B * c * cx
    dQ = 0.5 * rho * w2 * B * c * cy * r
    thrust = float(np.sum(dT * dr))
    torque = float(np.sum(dQ * dr))
    power = torque * omega
    D = blade.diameter
    return {
        "thrust": thrust, "torque": torque, "power": power,
        "CT": thrust / (rho * n_rps**2 * D**4), "CP": power / (rho * n_rps**3 * D**5),
        "converged": converged,
        "span": {
            "rR": r_rel, "chord": c, "twist": np.degrees(theta), "alpha": alpha, "cl": cl, "cd": cd,
            "inflow": vi, "swirl": ut, "dT": dT, "dQ": dQ, "loss": F,
        },
    }


def analyse(
    blade: Blade, tip_speed: float = 110.0, rho: float = 1.225, mu: float = 1.81e-5, ncrit: float = 9.0,
    j_max: float | None = None, j_step: float = 0.025, fit_j_max: float | None = None,
) -> Result:
    """Thrust and power coefficients against advance ratio at the reference rotor
    speed, the quadratic fits CT = ct0 + ct1 J + ct2 J², CP = cp0 + cp1 J + cp2 J²
    over the thrust-producing range (or up to ``fit_j_max``), and the static
    operating point with its spanwise loads."""
    if not 20 <= tip_speed <= 300:
        raise AeroError("tip speed: 20 to 300 m/s")
    n = tip_speed / (math.pi * blade.diameter)
    sections = sections_for(blade, tip_speed, rho, mu, ncrit)
    warnings = [s.warning for s in sections if s.warning]

    # sweep J until the thrust is gone (or j_max)
    j_end = j_max if j_max is not None else 1.5
    curve = []
    static = None
    for J in np.arange(0.0, j_end + 1e-9, j_step):
        res = solve(blade, sections, n, J * n * blade.diameter, rho)
        if J == 0:
            static = res
        point = {"J": round(float(J), 4), "CT": res["CT"], "CP": res["CP"], "eta": J * res["CT"] / res["CP"] if res["CP"] > 0 else 0.0, "converged": res["converged"]}
        curve.append(point)
        if j_max is None and res["CT"] <= 0:
            break
    if not any(p["converged"] for p in curve):
        warnings.append("the blade-element iteration did not converge")
    assert static is not None

    # fit over the thrust-producing range
    hi = fit_j_max if fit_j_max is not None else max([p["J"] for p in curve if p["CT"] > 0] or [0.0])
    pts = [p for p in curve if p["J"] <= hi + 1e-9]
    if len(pts) < 3:
        raise AeroError("fewer than 3 thrust-producing advance ratios: check the twist (pitch) and the chord")
    js = np.array([p["J"] for p in pts])
    ct_fit = np.polynomial.polynomial.polyfit(js, [p["CT"] for p in pts], 2)
    cp_fit = np.polynomial.polynomial.polyfit(js, [p["CP"] for p in pts], 2)
    err = {
        "CT": float(np.max(np.abs(np.polynomial.polynomial.polyval(js, ct_fit) - [p["CT"] for p in pts]))),
        "CP": float(np.max(np.abs(np.polynomial.polynomial.polyval(js, cp_fit) - [p["CP"] for p in pts]))),
    }
    fit = {"ct0": ct_fit[0], "ct1": ct_fit[1], "ct2": ct_fit[2], "cp0": cp_fit[0], "cp1": cp_fit[1], "cp2": cp_fit[2]}

    sp = static["span"]
    stall = [float(x) for x, a, rr in zip(sp["rR"], sp["alpha"], sp["rR"]) if a > _stall_at(sections, rr)]
    if stall:
        warnings.append(f"stalled in hover from r/R {min(stall):.2f} to {max(stall):.2f}")
    span = [
        {k: round(float(sp[k][i]), 6) for k in ("rR", "chord", "twist", "alpha", "cl", "cd", "inflow", "swirl", "dT", "dQ", "loss")}
        for i in range(len(sp["rR"]))
    ]
    static_point = {
        "rpm": n * 60, "thrust": static["thrust"], "torque": static["torque"], "power": static["power"],
        "CT": static["CT"], "CP": static["CP"], "thrustPerWatt": static["thrust"] / static["power"] if static["power"] > 0 else None,
        # figure of merit: ideal (momentum) power over actual, at this thrust
        "figureOfMerit": (static["thrust"] ** 1.5 / math.sqrt(2 * rho * math.pi * blade.radius**2)) / static["power"] if static["power"] > 0 and static["thrust"] > 0 else None,
    }
    return Result(curve, fit, (0.0, float(hi)), err, static_point, span, sections, warnings)


def _stall_at(sections: list[Section], r_rel: float) -> float:
    st = [s.station for s in sections]
    return float(np.interp(r_rel, st, [s.polar.alpha_stall for s in sections]))
