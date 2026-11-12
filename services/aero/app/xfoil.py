"""Section polars from XFOIL (Drela, MIT), run in batch mode.

One XFOIL process per polar: it sweeps the angle of attack up from 0° and,
re-initialised, down from 0°, each point starting from the previous one's
boundary layer. Points that do not converge are left out; ``polar`` fills the
gaps and extrapolates past stall.

Debian builds XFOIL with floating-point traps on, so a NaN in a boundary-layer
iteration that XFOIL would recover from kills it. ``nofpe.so`` (preloaded into
the XFOIL process only) makes gfortran's trap setup a no-op.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

XFOIL = os.environ.get("XFOIL_BIN", "xfoil")
NOFPE = os.environ.get("XFOIL_NOFPE", "/opt/xfoil/nofpe.so")
TIMEOUT = float(os.environ.get("XFOIL_TIMEOUT", "60"))
NACA_RE = re.compile(r"^\s*NACA\s*-?\s*(\d{4,5})\s*$", re.I)


class XfoilError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolarPoint:
    alpha: float  # deg
    cl: float
    cd: float
    cm: float


def available() -> bool:
    return shutil.which(XFOIL) is not None


def naca_digits(airfoil: str) -> str | None:
    m = NACA_RE.match(airfoil)
    return m.group(1) if m else None


def _commands(load: str, re_: float, mach: float, ncrit: float, amin: float, amax: float, step: float) -> str:
    return "\n".join(
        [
            "PLOP", "G F", "",  # no graphics
            load,
            "PANE",
            "OPER",
            f"VISC {re_:.0f}",
            f"MACH {mach:.3f}",
            "VPAR", f"N {ncrit:g}", "",
            "ITER 150",
            "PACC", "polar.txt", "",
            f"ASEQ 0 {amax:g} {step:g}",
            "INIT",
            f"ASEQ {-step:g} {amin:g} {-step:g}",
            "",
            "QUIT",
            "",
        ]
    )


def parse_polar(text: str) -> list[PolarPoint]:
    """The points of an XFOIL polar file (PACC), sorted by angle of attack."""
    pts: dict[float, PolarPoint] = {}
    started = False
    for line in text.splitlines():
        if line.strip().startswith("------"):
            started = True
            continue
        if not started:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            a, cl, cd, _cdp, cm = (float(x) for x in parts[:5])
        except ValueError:
            continue
        pts[a] = PolarPoint(a, cl, cd, cm)
    return [pts[a] for a in sorted(pts)]


_cache: dict[tuple, list[PolarPoint]] = {}
_lock = threading.Lock()


def run_polar(
    airfoil: str,
    reynolds: float,
    mach: float = 0.0,
    ncrit: float = 9.0,
    amin: float = -10.0,
    amax: float = 20.0,
    step: float = 1.0,
    coordinates: list[tuple[float, float]] | None = None,
) -> list[PolarPoint]:
    """The converged points of one polar. ``airfoil`` is a NACA 4- or 5-digit
    designation ("NACA 4412"), or a name for ``coordinates`` (x, y from the
    trailing edge over the upper surface to the leading edge and back)."""
    # Reynolds numbers to 2 significant digits share a polar: stations of
    # nearly equal Reynolds number reuse it
    re_key = float(f"{reynolds:.2g}")
    key = (airfoil.upper().replace(" ", ""), re_key, round(mach, 2), ncrit, amin, amax, step, tuple(coordinates or ()))
    with _lock:
        if key in _cache:
            return _cache[key]
    digits = naca_digits(airfoil)
    if not coordinates and not digits:
        raise XfoilError(f"airfoil '{airfoil}': give a NACA 4- or 5-digit designation, e.g. 'NACA 4412', or its coordinates")
    if not available():
        raise XfoilError(f"XFOIL not found ({XFOIL})")
    with tempfile.TemporaryDirectory(prefix="xfoil-") as d:
        if coordinates:
            Path(d, "airfoil.dat").write_text(f"{airfoil}\n" + "".join(f"{x:.6f} {y:.6f}\n" for x, y in coordinates))
            load = "LOAD airfoil.dat"
        else:
            load = f"NACA {digits}"
        env = {**os.environ}
        if Path(NOFPE).exists():
            env["LD_PRELOAD"] = NOFPE
        try:
            proc = subprocess.run(
                [XFOIL], input=_commands(load, re_key, mach, ncrit, amin, amax, step), cwd=d, env=env,
                capture_output=True, text=True, timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise XfoilError(f"XFOIL did not finish within {TIMEOUT:g} s ({airfoil}, Re {re_key:.3g})") from exc
        polar = Path(d, "polar.txt")
        points = parse_polar(polar.read_text()) if polar.exists() else []
        if not points:
            tail = "\n".join(proc.stdout.splitlines()[-5:])
            raise XfoilError(f"XFOIL converged at no angle of attack ({airfoil}, Re {re_key:.3g}): {tail}")
    with _lock:
        _cache[key] = points
    return points
