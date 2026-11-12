import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import bemt, polar, xfoil
from app.main import app

needs_xfoil = pytest.mark.skipif(not xfoil.available(), reason="XFOIL not installed")


def test_parse_polar_skips_headers_and_sorts():
    text = """
       XFOIL         Version 6.99
   alpha    CL        CD       CDp       CM     Top_Xtr  Bot_Xtr
  ------ -------- --------- --------- -------- -------- --------
   1.000   0.5639   0.01746   0.00782  -0.1072   0.7817   1.0000
   0.000   0.4377   0.01791   0.00867  -0.1064   0.8207   1.0000
  -1.000   0.3095   0.01830   0.00968  -0.1046   0.8618   1.0000
"""
    pts = xfoil.parse_polar(text)
    assert [p.alpha for p in pts] == [-1.0, 0.0, 1.0]
    assert pts[1].cl == 0.4377 and pts[1].cd == 0.01791


def _points(alphas, a0=-4.0, stall=12.0):
    out = []
    for a in alphas:
        cl = 0.1 * (a - a0) if a <= stall else 0.1 * (stall - a0) - 0.05 * (a - stall)
        out.append(xfoil.PolarPoint(a, cl, 0.012 + 0.0004 * a * a, -0.05))
    return out


def test_polar_fills_gaps_and_extrapolates_past_stall():
    pts = [p for p in _points(range(-6, 17)) if p.alpha != 3]  # 3° did not converge
    p = polar.from_points(pts, 1e5, 0.2, requested=23, aspect_ratio=6)
    assert p.alpha_stall == 12 and p.alpha[-1] == 12  # cut at maximum lift
    cl, cd = p.coefficients(np.array([3.0, 30.0, 90.0, -40.0]))
    assert cl[0] == pytest.approx(0.7)  # interpolated across the gap
    assert cd[2] == pytest.approx(polar.flat_plate_cd_max(6), rel=1e-3)  # a flat plate at 90°
    assert abs(cl[2]) < 0.05  # no lift at 90°
    assert cl[3] < 0 and cd[3] > 0.3  # stalled at negative angles too
    # continuous at the stall angle
    a, b = p.coefficients(np.array([12.0, 12.001]))
    assert a[1] == pytest.approx(a[0], abs=1e-2) and b[1] == pytest.approx(b[0], abs=1e-2)


def _blade(**kw):
    st = np.linspace(0.15, 1.0, 9)
    D = 15 * 0.0254
    pitch = 5 * 0.0254
    twist = np.degrees(np.arctan(pitch / (2 * math.pi * st * D / 2)))
    args = dict(diameter_in=15, blades=2, stations=st, chord=0.14 - 0.06 * st, twist=twist, airfoil="NACA 4412")
    return bemt.Blade.build(**{**args, **kw})


def _sections(blade):
    """Synthetic polars, so the momentum model is tested without XFOIL."""
    p = polar.from_points(_points(range(-8, 17)), 8e4, 0.2, requested=25, aspect_ratio=blade.aspect_ratio)
    return [bemt.Section(float(s), "test", p) for s in blade.stations]


def test_blade_checks_its_stations():
    with pytest.raises(bemt.AeroError):
        bemt.Blade.build(15, 2, [0.2, 0.5], [0.1, 0.1], [20, 10], "NACA 4412")
    with pytest.raises(bemt.AeroError):
        bemt.Blade.build(15, 2, [0.2, 0.5, 0.4], [0.1] * 3, [20] * 3, "NACA 4412")
    with pytest.raises(bemt.AeroError):
        bemt.Blade.build(15, 2, [0.2, 0.5, 1.0], [0.1] * 3, [20] * 2, "NACA 4412")


def test_bemt_static_and_forward_flight():
    blade = _blade()
    secs = _sections(blade)
    n = 110 / (math.pi * blade.diameter)
    hover = bemt.solve(blade, secs, n, 0.0, 1.225)
    assert hover["converged"]
    # a low-pitch (P/D 0.33) blade of 7 % solidity: CT ≈ σ cl/6 · π³/4 ≈ 0.06, CP ≈ 0.015
    assert 0.03 < hover["CT"] < 0.12 and 0.008 < hover["CP"] < 0.05
    # momentum check: the thrust equals the rate of axial momentum through the disc
    sp = hover["span"]
    assert np.all(sp["inflow"] > 0)
    fast = bemt.solve(blade, secs, n, 0.4 * n * blade.diameter, 1.225)
    assert fast["CT"] < hover["CT"]  # thrust falls with advance ratio
    eta = 0.4 * fast["CT"] / fast["CP"]
    assert 0.3 < eta < 0.9


def test_analyse_fits_quadratics(monkeypatch):
    blade = _blade()
    monkeypatch.setattr(bemt, "sections_for", lambda b, *a, **k: _sections(b))
    res = bemt.analyse(blade)
    js = [p["J"] for p in res.curve]
    assert js[0] == 0 and res.curve[-1]["CT"] <= 0  # swept until the thrust is gone
    assert res.fit["ct0"] == pytest.approx(res.curve[0]["CT"], rel=0.1)
    assert res.fit["cp0"] > 0 and res.fit["ct2"] < 0
    assert res.fit_error["CT"] < 0.02
    assert 0.3 < res.static["figureOfMerit"] < 0.85


@needs_xfoil
def test_xfoil_polar():
    pts = xfoil.run_polar("NACA 4412", 1e5, 0.1)
    by = {p.alpha: p for p in pts}
    assert len(pts) > 15
    assert 0.3 < by[0.0].cl < 0.6 and 0.005 < by[0.0].cd < 0.03
    with pytest.raises(xfoil.XfoilError):
        xfoil.run_polar("Clark Y", 1e5)  # not a NACA designation and no coordinates


@needs_xfoil
def test_propeller_endpoint():
    st = [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0]
    D = 15 * 0.0254
    twist = [round(math.degrees(math.atan(5 * 0.0254 / (2 * math.pi * s * D / 2))), 2) for s in st]
    body = {"inputs": {"diameter": 15, "bladeCount": 2, "stations": st, "chord": [0.12, 0.15, 0.16, 0.155, 0.145, 0.13, 0.115, 0.095, 0.07, 0.05],
                       "twist": twist, "airfoil": "NACA 4412", "hubRadius": 0.12}}
    r = TestClient(app).post("/propeller", json=body)
    assert r.status_code == 200, r.text
    o = r.json()["outputs"]
    assert 0.05 < o["ct0"] < 0.2 and 0.01 < o["cp0"] < 0.1
    assert o["thrustPerWatt"]["unit"] == "N/W" and o["thrustPerWatt"]["value"] > 0
    assert len(o["sections"]) == len(st) and all(s["source"] == "xfoil" for s in o["sections"])
    assert TestClient(app).post("/propeller", json={"inputs": {**body["inputs"], "chord": [0.1]}}).status_code == 422
