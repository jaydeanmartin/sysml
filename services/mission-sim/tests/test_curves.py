"""Performance curves of one component, as the dynamic simulation uses them."""

import math

import pytest
from fastapi.testclient import TestClient

from app import dynamics
from app.main import app

PROP = {"ct0": 0.11, "ct1": -0.05, "ct2": -0.12, "cp0": 0.045, "cp1": -0.01, "cp2": -0.03}
MOTOR = {"kv": 400, "resistance": 0.1, "noLoadCurrent": 0.5, "maxCurrent": 32}


def test_propeller_curves_end_at_zero_thrust():
    r = dynamics.component_curves("propeller", PROP)
    c, s = r["curves"], r["summary"]
    assert c[0]["J"] == 0 and c[0]["efficiency"] == 0
    assert c[-1]["J"] == pytest.approx(s["zeroThrustJ"]) and c[-1]["CT"] == pytest.approx(0, abs=1e-12)
    assert s["staticCTCP"] == pytest.approx(0.11 / 0.045)
    assert 0 < s["maxEfficiencyJ"] < s["zeroThrustJ"] and 0 < s["maxEfficiency"] < 1
    assert all(p["CQ"] == pytest.approx(p["CP"] / (2 * math.pi)) for p in c)


def test_propeller_curves_match_the_simulation():
    # the same Propeller the dynamic simulation builds, whatever the diameter
    sim = dynamics.Propeller(dynamics.Inputs(steps=[], rotorCount=4, takeoffMass=5, payloadMass=0, dragArea=0.05, propDiameter=15,
                                             **PROP, motorKv=400, motorResistance=0.1, motorNoLoadCurrent=0.5, motorMaxCurrent=32,
                                             batteryVoltage=22.2, batteryEnergy=200, batteryMaxPower=3000))
    assert dynamics.component_curves("propeller", PROP)["curves"] == sim.curves()


def test_motor_curves_at_a_voltage():
    r = dynamics.component_curves("motor", MOTOR, voltage=22.2)
    c, s = r["curves"], r["summary"]
    assert c[0]["current"] == pytest.approx(0.5) and c[0]["torque"] == pytest.approx(0)
    assert c[-1]["current"] == pytest.approx(32)  # rated maximum, below the 222 A stall current
    assert c[0]["speed"] == pytest.approx((22.2 - 0.5 * 0.1) * 400)
    assert s["noLoadSpeed"] == pytest.approx(400 * 22.2) and s["stallCurrent"] == pytest.approx(222)
    assert 0 < s["maxEfficiency"] < 1
    # a lower voltage slows the motor
    assert dynamics.component_curves("motor", MOTOR, voltage=14.8)["curves"][0]["speed"] < c[0]["speed"]


def test_bad_component_inputs():
    with pytest.raises(dynamics.SimulationError):
        dynamics.component_curves("motor", MOTOR)  # no voltage
    with pytest.raises(dynamics.SimulationError):
        dynamics.component_curves("propeller", {**PROP, "ct0": None})
    with pytest.raises(dynamics.SimulationError):
        dynamics.component_curves("gearbox", {})


def test_rest_contract():
    client = TestClient(app)
    r = client.post("/curves", json={"toolName": "componentCurves", "inputs": {"component": "motor", **MOTOR, "voltage": {"value": 22.2, "unit": "V"}}})
    assert r.status_code == 200
    out = r.json()["outputs"]
    assert len(out["curves"]) == dynamics.CURVE_POINTS and out["summary"]["voltage"] == 22.2
    assert client.post("/curves", json={"inputs": {"component": "propeller"}}).status_code == 422
