"""Dynamic mission simulation (no other service needed)."""

import math

import pytest
from fastapi.testclient import TestClient

from app.dynamics import Inputs, SimulationError, simulate
from app.main import app

RHO = 1.225
D = 15 * 0.0254


def inputs(steps, **kw):
    base = dict(
        steps=steps, rotorCount=4, takeoffMass=6.25, payloadMass=2, dragArea=0.05, propDiameter=15,
        ct0=0.105, ct1=-0.05, ct2=-0.48, cp0=0.036, cp1=0, cp2=-0.07,
        motorKv=400, motorResistance=0.1, motorNoLoadCurrent=0.5, motorMaxCurrent=32,
        batteryVoltage=22.2, batteryEnergy=222, batteryMaxPower=5000, timeStep=0.1,
    )
    return Inputs(**{**base, **kw})


def hover(duration=60, name="hover"):
    return {"name": name, "behavior": "Hover", "duration": duration}


def test_steady_hover_matches_the_closed_form():
    r = simulate(inputs([hover()]))
    thrust = 6.25 * 9.81 / 4
    n = math.sqrt(thrust / (0.105 * RHO * D ** 4))           # rev/s, J = 0
    shaft = 0.036 * RHO * n ** 3 * D ** 5
    torque = shaft / (2 * math.pi * n)
    kv = 400 * 2 * math.pi / 60
    current = torque * kv + 0.5
    volts = 2 * math.pi * n / kv + current * 0.1
    bus = 4 * volts * current / 0.95 + 15
    [b] = r["behaviors"]
    assert b["averagePower"] == pytest.approx(bus, rel=1e-6)
    op = b["operatingPoint"]
    assert op["rpm"] == pytest.approx(60 * n, rel=1e-6)
    assert op["J"] == 0 and op["propEfficiency"] == 0
    assert op["thrustPerPower"] == pytest.approx(thrust / shaft, rel=1e-6)
    assert op["motorEfficiency"] == pytest.approx(shaft / (volts * current), rel=1e-6)
    assert r["reference"]["hoverRpm"] == pytest.approx(60 * n, rel=1e-6)
    # energy drawn includes the battery's resistive loss
    assert r["energyUsed"] > bus * 60 / 3600


def test_climbing_moves_the_propeller_along_its_curves_and_costs_more_than_hover():
    r = simulate(inputs([{"name": "takeoff", "behavior": "Takeoff", "duration": 20, "verticalSpeed": 3}, hover(20)]))
    climb, hov = r["behaviors"]
    assert climb["operatingPoint"]["J"] > 0
    assert climb["operatingPoint"]["propEfficiency"] > 0
    assert climb["averagePower"] > hov["averagePower"]


def test_accelerating_is_a_power_transient():
    r = simulate(inputs([{"name": "cruise", "behavior": "Cruise", "duration": 120, "groundSpeed": 12}]))
    [b] = r["behaviors"]
    assert b["peakPower"] > 1.15 * b["averagePower"]
    assert r["distanceFlown"] == pytest.approx(12 * 120 - 12 ** 2 / (2 * 2), rel=0.01)   # 2 m/s² to 12 m/s
    assert b["operatingPoint"]["tilt"] > 0


def test_on_the_ground_only_the_avionics_draw_power_and_a_release_lightens_the_drone():
    steps = [hover(30), {"name": "drop", "behavior": "GroundRelease", "duration": 30, "airborne": False, "releasesPayload": True}, hover(30, "hover_2")]
    r = simulate(inputs(steps))
    first, ground, second = r["flownSteps"]
    assert ground["averagePower"] == pytest.approx(15)
    assert ground["operatingPoint"] is None
    assert second["mass"] == pytest.approx(4.25)
    assert second["averagePower"] < first["averagePower"]
    # per behavior: both hovers sum into one entry
    assert [b["behavior"] for b in r["behaviors"]] == ["Hover", "GroundRelease"]
    assert r["behaviors"][0]["energy"] == pytest.approx(first["energy"] + second["energy"])


def test_propeller_and_motor_curves():
    r = simulate(inputs([hover(5)]))
    prop = r["propellerCurves"]
    assert prop[0]["J"] == 0 and prop[-1]["CT"] == pytest.approx(0, abs=1e-9)
    for p in prop:
        assert p["efficiency"] == pytest.approx(p["J"] * p["CT"] / p["CP"])
        assert p["CQ"] == pytest.approx(p["CP"] / (2 * math.pi))
    motor = r["motorCurves"]
    kv = 400 * 2 * math.pi / 60
    for m in motor:
        assert m["torque"] == pytest.approx((m["current"] - 0.5) / kv)
        assert m["speed"] == pytest.approx((22.2 - m["current"] * 0.1) * 400)
    eff = [m["efficiency"] for m in motor]
    peak = eff.index(max(eff))
    assert 0 < peak < len(eff) - 1   # efficiency peaks between no load and max current


def test_too_low_a_kv_saturates_the_motors():
    r = simulate(inputs([hover(10)], motorKv=150))
    assert r["maxThrottle"] > 1
    assert not r["feasible"]
    assert any("full throttle" in x for x in r["issues"])


def test_an_empty_pack_ends_the_flight():
    r = simulate(inputs([hover(3600)], batteryEnergy=20))
    assert not r["completed"]
    assert r["energyPercent"] == pytest.approx(100)
    assert r["missionTime"] < 3600


def test_bad_inputs_are_refused():
    with pytest.raises(SimulationError):
        simulate(inputs([hover()], ct0=0))
    with pytest.raises(SimulationError):
        simulate(inputs([{"name": "x", "duration": -1}]))


def test_endpoint_takes_values_with_units():
    i = inputs([hover(10)])
    body = {k: getattr(i, k) for k in ("rotorCount", "takeoffMass", "payloadMass", "dragArea", "propDiameter", "ct0", "ct1", "ct2", "cp0", "cp1", "cp2",
                                       "motorKv", "motorResistance", "motorNoLoadCurrent", "motorMaxCurrent", "batteryVoltage", "batteryEnergy", "batteryMaxPower")}
    body["takeoffMass"] = {"value": 6.25, "unit": "kg"}
    res = TestClient(app).post("/dynamics", json={"toolName": "dynamicsSimulator", "inputs": {**body, "steps": i.steps}})
    assert res.status_code == 200, res.text
    out = res.json()["outputs"]
    assert out["energyUsed"]["unit"] == "Wh" and out["maxThrottle"] < 1
    assert len(out["propellerCurves"]) == 41
    bad = TestClient(app).post("/dynamics", json={"inputs": {"steps": i.steps}})
    assert bad.status_code == 422
