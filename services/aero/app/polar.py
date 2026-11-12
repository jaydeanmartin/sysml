"""A section polar over the whole range of angles of attack a propeller sees.

From XFOIL's converged points, angles where it did not converge are
interpolated, and past the last converged angle on either side (stall) the
Viterna-Corrigan model takes over: lift and drag of a stalled section tend to
those of a flat plate, with a maximum drag set by the blade's aspect ratio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .xfoil import PolarPoint


@dataclass
class Polar:
    reynolds: float
    mach: float
    alpha: np.ndarray  # deg, converged and interpolated, ascending
    cl: np.ndarray
    cd: np.ndarray
    converged: int
    requested: int
    alpha_stall: float  # deg: angle of maximum lift
    cl_max: float
    alpha_min: float  # lowest angle XFOIL covered
    cd_max: float  # flat-plate drag at 90°
    source: str = "xfoil"
    stall_low: tuple[float, float, float] = field(default=(0.0, 0.0, 0.0))  # (alpha, cl, cd) at the negative end
    stall_high: tuple[float, float, float] = field(default=(0.0, 0.0, 0.0))

    def coefficients(self, alpha_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """cl, cd at any angle of attack (deg), clipped to ±90°."""
        a = np.clip(np.asarray(alpha_deg, dtype=float), -90.0, 90.0)
        cl = np.interp(a, self.alpha, self.cl)
        cd = np.interp(a, self.alpha, self.cd)
        hi = a > self.alpha[-1]
        lo = a < self.alpha[0]
        if hi.any():
            cl[hi], cd[hi] = _viterna(a[hi], *self.stall_high, self.cd_max)
        if lo.any():
            cl[lo], cd[lo] = _viterna(a[lo], *self.stall_low, self.cd_max)
        return cl, cd

    def summary(self) -> dict:
        return {
            "reynolds": round(self.reynolds),
            "mach": round(self.mach, 3),
            "source": self.source,
            "converged": self.converged,
            "requested": self.requested,
            "alphaStall": round(self.alpha_stall, 2),
            "clMax": round(self.cl_max, 4),
            "cdMin": round(float(self.cd.min()), 5),
            "alpha": [round(float(x), 2) for x in self.alpha],
            "cl": [round(float(x), 4) for x in self.cl],
            "cd": [round(float(x), 5) for x in self.cd],
        }


def flat_plate_cd_max(aspect_ratio: float) -> float:
    """Viterna-Corrigan maximum drag of a blade of this aspect ratio (span / mean chord)."""
    return 1.11 + 0.018 * min(max(aspect_ratio, 1.0), 50.0)


def _viterna(a_deg: np.ndarray, a_s_deg: float, cl_s: float, cd_s: float, cd_max: float) -> tuple[np.ndarray, np.ndarray]:
    """Viterna-Corrigan post-stall lift and drag, matching (cl_s, cd_s) at the stall angle a_s."""
    a = np.radians(a_deg)
    a_s = math.radians(a_s_deg)
    s, c = math.sin(a_s), math.cos(a_s)
    a1 = cd_max / 2
    b1 = cd_max
    a2 = (cl_s - cd_max * s * c) * s / (c * c)
    b2 = (cd_s - cd_max * s * s) / c
    sa = np.sin(a)
    sa = np.where(np.abs(sa) < 1e-6, 1e-6, sa)
    cl = a1 * np.sin(2 * a) + a2 * np.cos(a) ** 2 / sa
    cd = b1 * np.sin(a) ** 2 + b2 * np.cos(a)
    return cl, np.maximum(cd, cd_s)


def from_points(points: list[PolarPoint], reynolds: float, mach: float, requested: int, aspect_ratio: float) -> Polar:
    """A polar from XFOIL's converged points. Stall is the angle of maximum lift
    on the positive side; beyond it (and below the lowest converged angle) the
    Viterna-Corrigan model applies."""
    if len(points) < 4:
        raise ValueError(f"only {len(points)} converged points: too few for a polar")
    al = np.array([p.alpha for p in points])
    cl = np.array([p.cl for p in points])
    cd = np.array([p.cd for p in points])
    # keep the polar up to maximum lift: past it XFOIL's post-stall values are unreliable
    i_max = int(np.argmax(np.where(al >= 0, cl, -np.inf)))
    al, cl, cd = al[: i_max + 1], cl[: i_max + 1], cd[: i_max + 1]
    # fill the angles XFOIL skipped, on a 1° grid
    grid = np.arange(math.ceil(al[0]), math.floor(al[-1]) + 1e-9, 1.0)
    grid = np.union1d(grid, al)
    cl_g = np.interp(grid, al, cl)
    cd_g = np.interp(grid, al, cd)
    return Polar(
        reynolds=reynolds, mach=mach, alpha=grid, cl=cl_g, cd=cd_g, converged=len(points), requested=requested,
        alpha_stall=float(grid[-1]), cl_max=float(cl_g[-1]), alpha_min=float(grid[0]),
        cd_max=flat_plate_cd_max(aspect_ratio),
        stall_low=(float(grid[0]), float(cl_g[0]), float(cd_g[0])),
        stall_high=(float(grid[-1]), float(cl_g[-1]), float(cd_g[-1])),
    )


def thin_airfoil(reynolds: float, mach: float, aspect_ratio: float, camber: float = 0.04) -> Polar:
    """A fallback when XFOIL converges nowhere: thin-airfoil lift with a
    zero-lift angle from the camber, a constant profile drag, stall at 12°."""
    a0 = -math.degrees(2 * camber)
    grid = np.arange(-8.0, 12.0 + 1e-9, 1.0)
    cl = 2 * math.pi * np.radians(grid - a0) / math.sqrt(max(1 - mach * mach, 0.3))
    cd = 0.02 + 0.01 * (cl - 0.4) ** 2
    return Polar(
        reynolds=reynolds, mach=mach, alpha=grid, cl=cl, cd=cd, converged=0, requested=0,
        alpha_stall=12.0, cl_max=float(cl[-1]), alpha_min=-8.0, cd_max=flat_plate_cd_max(aspect_ratio), source="thin airfoil",
        stall_low=(-8.0, float(cl[0]), float(cd[0])), stall_high=(12.0, float(cl[-1]), float(cd[-1])),
    )
