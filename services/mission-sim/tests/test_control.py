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
BEHAVIORS = [
    {"slot": "takeoff", "behavior": "Takeoff", "climbRate": 3},
    {"slot": "cruise", "behavior": "Cruise", "speed": 12},
    {"slot": "turn", "behavior": "Turn", "yawRate": 45},
]


@pytest.fixture(scope="module")
def tuned():
    return control.simulate({**QUAD, "mode": "tune", "behaviors": BEHAVIORS})


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
    names = [s["name"] for s in tuned["scenarios"]]
    assert names == ["takeoff: climb rate step", "cruise: ground speed step", "turn: 90° turn", "hover: 5 m/s gust"]
    assert tuned["speedSettlingTime"] < 5 and tuned["speedOvershoot"] < 15
    assert tuned["headingSettlingTime"] < 6
    assert tuned["gustDeviation"] < 1
    assert tuned["saturation"] < 5 and tuned["completed"] and not tuned["issues"]
    cruise = next(s for s in tuned["scenarios"] if s["parameter"] == "speed")
    assert cruise["maxTilt"] <= 32  # the 30° tilt limit holds


def test_verify_flies_the_given_gains(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    same = control.simulate({**QUAD, **gains, "mode": '"verify"', "behaviors": BEHAVIORS[1:2]})  # a SysML string keeps its quotes
    assert same["mode"] == "verify" and same["speedSettlingTime"] == pytest.approx(tuned["speedSettlingTime"], abs=0.05)
    # a sluggish velocity loop settles later
    slow = control.simulate({**QUAD, **gains, "velocityKp": gains["velocityKp"] / 4, "velocityKi": gains["velocityKi"] / 16, "mode": "verify", "behaviors": BEHAVIORS[1:2]})
    assert slow["speedSettlingTime"] is None or slow["speedSettlingTime"] > same["speedSettlingTime"]


def test_rest_contract(tuned):
    gains = {k: tuned[k] for k in control.GAIN_NAMES}
    body = {"inputs": {**QUAD, **gains, "mode": "verify", "behaviors": [{"slot": "turn", "yawRate": {"value": 45}}]}}
    r = TestClient(app).post("/control", json=body)
    assert r.status_code == 200
    out = r.json()["outputs"]
    assert out["phaseMargin"]["unit"] == "deg" and out["gustDeviation"]["unit"] == "m"
    assert len(out["mixer"]["rotors"]) == 4 and out["mixer"]["rotors"][0]["spin"] == "ccw"
    assert math.isclose(out["rpmKp"], gains["rpmKp"])
    assert TestClient(app).post("/control", json={"inputs": {**QUAD, "rotorCount": 5, "mode": "tune"}}).status_code == 422
