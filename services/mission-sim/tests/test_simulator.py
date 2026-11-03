"""Mission simulation (no other service needed)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.simulator import Inputs, SimulationError, simulate

STEPS = [
    {"name": "takeoff", "duration": 20, "powerFactor": 1.4},
    {"name": "cruise", "duration": 400, "powerFactor": 1.1},
    {"name": "release", "duration": 30, "powerFactor": 0.05, "releasesPayload": True},
    {"name": "cruise_2", "duration": 400, "powerFactor": 1.1},
]


def inputs(**kw):
    base = dict(steps=STEPS, hoverPower=600, takeoffMass=6, payloadMass=2, batteryEnergy=200, batteryMaxPower=3000, internalLoss=0)
    return Inputs(**{**base, **kw})


def test_energy_without_losses_or_payload_drop_is_power_times_time():
    r = simulate(inputs(payloadMass=0))
    expected = 600 * (20 * 1.4 + 400 * 1.1 + 30 * 0.05 + 400 * 1.1) / 3600
    assert r["energyUsed"] == pytest.approx(expected)
    assert r["energyPercent"] == pytest.approx(100 * expected / 200)
    assert r["missionTime"] == pytest.approx(850)
    assert r["endStateOfCharge"] == pytest.approx(100 - r["energyPercent"])
    assert r["peakPower"] == pytest.approx(600 * 1.4)


def test_releasing_the_payload_lightens_the_return_leg():
    r = simulate(inputs())
    back = r["flownSteps"][3]
    assert back["mass"] == pytest.approx(4)
    assert back["power"] == pytest.approx(600 * 1.1 * 4 / 6)
    assert r["energyUsed"] < simulate(inputs(payloadMass=0))["energyUsed"]


def test_losses_draw_more_than_is_delivered():
    r = simulate(inputs(internalLoss=0.1))
    assert r["energyUsed"] > r["energyDelivered"]


def test_reserve_and_depletion():
    r = simulate(inputs(batteryEnergy=300))
    assert r["feasible"] and r["completed"] and r["limitingStep"] == ""
    low = simulate(inputs(batteryEnergy=150))  # needs 127 Wh: completes, but ends at 15 %, below the 20 % reserve
    assert low["completed"] and not low["feasible"] and low["limitingStep"] in ("cruise", "cruise_2")
    empty = simulate(inputs(batteryEnergy=60))
    assert not empty["completed"] and empty["energyPercent"] == pytest.approx(100)
    assert empty["missionTime"] < 850 and empty["limitingStep"] == "cruise"


def test_power_beyond_the_battery_is_an_issue():
    r = simulate(inputs(batteryMaxPower=700))
    assert not r["feasible"] and r["limitingStep"] == "takeoff"
    assert any("more power" in x for x in r["issues"])


def test_bad_inputs():
    with pytest.raises(SimulationError):
        simulate(inputs(steps=[{"name": "x", "powerFactor": 1}]))
    with pytest.raises(SimulationError):
        simulate(inputs(batteryEnergy=0))


def test_rest_contract_matches_the_analysis_service():
    body = {
        "toolName": "missionSimulator",
        "inputs": {
            "steps": STEPS,
            "hoverPower": {"value": 600, "unit": "W"},
            "takeoffMass": 6, "payloadMass": 2, "batteryEnergy": 200, "batteryMaxPower": 3000,
        },
    }
    r = TestClient(app).post("/simulate", json=body)
    assert r.status_code == 200
    out = r.json()["outputs"]
    assert out["energyUsed"]["unit"] == "Wh" and out["missionTime"]["unit"] == "s"
    assert isinstance(out["timeline"], list) and out["timeline"][0]["soc"] == 100
    assert TestClient(app).post("/simulate", json={"inputs": {"steps": "nope"}}).status_code == 422
