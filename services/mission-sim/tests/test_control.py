"""Controllability simulation: mixer, identification, tuning and manoeuvres."""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import control
from app.dynamics import SimulationError
from app.main import app

# a 700 mm quadcopter with 15 in propellers on 400 KV motors, 6S (the demo's parcel drone)
QUAD = {
    "rotorCount": 4, "takeoffMass": 6.25, "armLength": 0.35, "inertiaXX": 0.0889, "inertiaYY": 0.0889, "inertiaZZ": 0.1251,
    "rotorInertia": 4.54e-4, "dragArea": 0.05, "propDiameter": 15, "ct0": 0.105, "cp0": 0.044,
    "motorKv": 400, "motorResistance": 0.1, "motorNoLoadCurrent": 0.5, "motorMaxCurrent": 32, "batteryVoltage": 22.2,
}
# the control tests, as the model's #ControlTest requirements specify them
TESTS = [
    {"requirement": "T::Climb", "command": "climbRate", "step": 3, "hold": 4},
    {"requirement": "T::Speed", "command": "speed", "step": 12, "hold": 9},
    {"requirement": "T::Turn", "command": "heading", "step": 90, "rate": 45, "hold": 4},
    {"requirement": "T::Gust", "command": "gust", "step": 5, "hold": 7},
]


@pytest.fixture(scope="module")
def tuned():
    return control.simulate({**QUAD, "mode": "tune", "tests": TESTS})


def test_mixer_inverts_the_allocation_and_spins_alternate():
    for n in (4, 6, 8):
        pl = control.Plant(control._vehicle({**QUAD, "rotorCount": n}))
        assert np.allclose(pl.A @ pl.M, np.eye(4))  # thrust and the three torques reach the rotors and back
        assert list(pl.spin) == [1.0, -1.0] * (n // 2)
        # equal thrusts: no torque; a pure yaw command speeds up the rotors that turn against it
        assert np.allclose((pl.A @ np.full(n, 10.0))[1:], 0)
        dT = pl.M @ np.array([0, 0, 0, 1.0])
        assert np.all(dT[pl.spin < 0] > 0) and np.all(dT[pl.spin > 0] < 0)


def test_rejects_what_it_cannot_control():
    with pytest.raises(SimulationError):
        control.simulate({**QUAD, "rotorCount": 3, "mode": "tune"})          # no yaw control by alternate spin
    with pytest.raises(SimulationError):
        control.simulate({**QUAD, "takeoffMass": 60, "mode": "tune"})        # more than the rotors lift
    with pytest.raises(SimulationError, match="rpmKp"):
        control.simulate({**QUAD, "mode": "verify"})                         # verify needs the gains


def test_identification_recovers_the_dynamics(tuned):
    ident = tuned["identification"]
    assert 0.02 < ident["rotor"]["timeConstant"] < 0.2
    # the torque tests recover 1 / inertia (roll within 15 %, yaw within 5 %)
    assert ident["roll"]["b"] == pytest.approx(1 / QUAD["inertiaXX"], rel=0.15)
    assert ident["yaw"]["b"] == pytest.approx(1 / QUAD["inertiaZZ"], rel=0.05)
    assert ident["roll"]["lag"] >= 0.5 * ident["rotor"]["timeConstant"]  # body torques follow the rotor loop


def test_tuned_loops_have_margin_and_fly_the_manoeuvres(tuned):
    assert all(m["phaseMargin"] >= 45 for m in tuned["margins"].values())
    assert tuned["gainMargin"] >= 6
    assert set(control.GAIN_NAMES) <= set(tuned)  # the proposals, as top-level outputs
    # exactly the tests given, in order, each under its requirement
    assert [s["requirement"] for s in tuned["scenarios"]] == [t["requirement"] for t in TESTS]
    res = tuned["testResults"]
    assert res["T::Climb"]["settlingTime"] < 4 and res["T::Climb"]["overshoot"] < 20
    assert res["T::Speed"]["settlingTime"] < 5 and res["T::Speed"]["overshoot"] < 15
    assert res["T::Speed"]["maxTilt"] <= 32  # the 30° tilt limit holds
    assert res["T::Turn"]["settlingTime"] < 3 and res["T::Turn"]["maxTrackingError"] < 10
    assert res["T::Gust"]["maxDeviation"] < 1 and res["T::Gust"]["finalError"] < res["T::Gust"]["maxDeviation"]
    assert tuned["speedSettlingTime"] == res["T::Speed"]["settlingTime"]  # aggregates follow the tests
    assert tuned["saturation"] < 5 and tuned["completed"] and not tuned["issues"]


def test_verify_flies_the_given_gains(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    same = control.simulate({**QUAD, **gains, "mode": '"verify"', "tests": TESTS[1:2]})  # a SysML string keeps its quotes
    assert same["mode"] == "verify" and same["speedSettlingTime"] == pytest.approx(tuned["speedSettlingTime"], abs=0.05)
    # a sluggish velocity loop settles later
    slow = control.simulate({**QUAD, **gains, "velocityKp": gains["velocityKp"] / 4, "velocityKi": gains["velocityKi"] / 16, "mode": "verify", "tests": TESTS[1:2]})
    assert slow["speedSettlingTime"] is None or slow["speedSettlingTime"] > same["speedSettlingTime"]


def test_a_bad_test_fails_alone(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    r = control.simulate({**QUAD, **gains, "mode": "verify", "tests": [{"requirement": "T::Odd", "command": "loop", "step": 1}, TESTS[0]]})
    assert not r["completed"] and "T::Odd" not in r["testResults"] and "T::Climb" in r["testResults"]
    assert any("unknown command" in i for i in r["issues"])
    # a turn without a rate is a heading step; a descent is a negative climb rate
    r = control.simulate({**QUAD, **gains, "mode": "verify", "tests": [
        {"requirement": "T::Snap", "command": "heading", "step": 30, "hold": 4},
        {"requirement": "T::Down", "command": "climbRate", "step": -2, "hold": 4},
    ]})
    assert r["testResults"]["T::Snap"]["settlingTime"] is not None and r["testResults"]["T::Down"]["overshoot"] < 25


def test_a_half_turn_is_measured_through_180_degrees(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    r = control.simulate({**QUAD, **gains, "mode": "verify", "tests": [{"requirement": "T::Half", "command": "heading", "step": 180, "rate": 45, "hold": 4}]})
    half = r["testResults"]["T::Half"]
    assert half["maxTrackingError"] < 10 and half["finalError"] < 2  # not 360° when the heading wraps to -180°


def test_rest_contract(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    body = {"inputs": {**QUAD, **gains, "mode": "verify", "tests": [{"requirement": "T::Turn", "command": "heading", "step": {"value": 90}, "rate": 45}]}}
    r = TestClient(app).post("/control", json=body)
    assert r.status_code == 200
    out = r.json()["outputs"]
    assert out["phaseMargin"]["unit"] == "deg" and out["saturation"]["unit"] == "%"
    assert out["gustDeviation"] is None  # no gust test was asked for
    assert len(out["mixer"]["rotors"]) == 4 and out["mixer"]["rotors"][0]["spin"] == "ccw"
    assert math.isclose(out["rpmKp"], gains["rpmKp"])
    assert out["testResults"]["T::Turn"]["settlingTime"] is not None
    assert TestClient(app).post("/control", json={"inputs": {**QUAD, "rotorCount": 5, "mode": "tune"}}).status_code == 422
