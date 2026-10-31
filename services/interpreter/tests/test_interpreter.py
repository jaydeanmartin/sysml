"""Interpreter service tests. They run the real OpenSysML binary (``sysml`` on PATH
or OPENSYSML_BIN) and a fake analysis service for the REST tool calls."""

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import opensysml, tools
from app.main import app, parse_trace

pytestmark = pytest.mark.skipif(opensysml.binary_path() is None, reason="OpenSysML binary not installed")

EXAMPLE = next(
    (p / "examples" / "vehicle.sysml").read_text()
    for p in Path(__file__).resolve().parents
    if (p / "examples" / "vehicle.sysml").exists()
)
client = TestClient(app)


class FakeAnalysis(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # registry
        body = json.dumps(
            [
                {"name": "massRollup", "inputs": [{"name": "engineMass"}, {"name": "tankMass"}, {"name": "transmissionMass"}], "outputs": [{"name": "total"}]},
                {"name": "rangeEstimate", "inputs": [{"name": "capacity"}, {"name": "efficiency"}, {"name": "mass"}], "outputs": [{"name": "range"}]},
            ]
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAnalysis.calls.append((self.path, req))
        i = req["inputs"]
        if self.path.endswith("/massRollup/invoke"):
            out = {"total": {"value": sum(v["value"] for v in i.values()), "unit": "kg"}}
        else:
            out = {"range": {"value": 750.0}}
        body = json.dumps({"outputs": out}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module", autouse=True)
def fake_analysis(tmp_path_factory):
    srv = HTTPServer(("127.0.0.1", 0), FakeAnalysis)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old_url, old_base = tools.ANALYSIS_SERVICE_URL, tools.TOOLS_BASE
    tools.ANALYSIS_SERVICE_URL = f"http://127.0.0.1:{srv.server_port}"
    tools.TOOLS_BASE = tmp_path_factory.mktemp("tools")
    yield
    tools.ANALYSIS_SERVICE_URL, tools.TOOLS_BASE = old_url, old_base
    srv.shutdown()


def files(text=EXAMPLE):
    return [{"name": "vehicle.sysml", "text": text}]


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_parse_builds_graph():
    r = client.post("/parse", json={"files": files()}).json()
    assert r["ok"], r["diagnostics"]
    kinds = {n["name"]: n["kind"] for n in r["graph"]["nodes"] if n["owner"] and n["owner"] == "VehicleModel"}
    assert kinds["Vehicle"] == "part def"
    assert kinds["sedan"] == "part"
    assert kinds["MassRequirement"] == "requirement def"
    edges = {(e["kind"], e["source"].split("__")[-1], e["target"].split("__")[-1]) for e in r["graph"]["edges"]}
    assert ("connection", "fuelOut", "fuelIn") in edges
    assert ("satisfy", "sedan", "sedanMass") in edges
    assert ("transition", "parked", "idling") in edges
    rollup = next(n for n in r["graph"]["nodes"] if n["name"] == "MassRollup")
    assert rollup["tool"]["toolName"] == "massRollup"


def test_parse_reports_syntax_errors():
    r = client.post("/parse", json={"files": files("package P { part def A }")}).json()
    assert not r["ok"]
    assert r["diagnostics"][0]["severity"] == "error"
    assert r["diagnostics"][0]["file"] == "vehicle.sysml"


def test_calc_runs_in_interpreter():
    r = client.post("/execute", json={"files": files(), "kind": "calc", "target": "VehicleModel::FuelEnergy", "args": ["10"]}).json()
    assert r["status"] == "holds"
    assert r["checks"][0]["values"][0]["value"] == "[342.0]"


def test_requirement_check_calls_rest_analyses():
    FakeAnalysis.calls.clear()
    r = client.post("/execute", json={"files": files(), "kind": "satisfy", "runId": "run-123"}).json()
    assert r["status"] == "holds", r
    assert {c["subject"] for c in r["checks"]} == {"satisfy sedanMass by sedan", "satisfy sedanRange by sedan"}
    paths = [p for p, _ in FakeAnalysis.calls]
    assert "/analyses/massRollup/invoke" in paths
    assert all(req["runId"] == "run-123" for _, req in FakeAnalysis.calls)
    mass_req = next(req for p, req in FakeAnalysis.calls if p.endswith("massRollup/invoke"))
    assert mass_req["inputs"]["engineMass"] == {"value": 180, "unit": "kg"}


def test_evaluate_expression_uses_tool_result():
    r = client.post("/execute", json={"files": files(), "kind": "evaluate", "expression": "VehicleModel::sedan.mass"}).json()
    assert r["checks"][0]["values"][0]["value"] in ("1415 [SI::kg]", "1415.0 [SI::kg]")


def test_action_trace():
    r = client.post("/execute", json={"files": files(), "kind": "action", "target": "VehicleModel::Drive"}).json()
    assert r["checks"][0]["values"] == [{"name": "distance", "value": "122"}]
    steps = [e["node"] for e in r["trace"] if e["type"] == "step"]
    assert steps[:4] == ["ignite", "accelerate", "cruise", "brake"]


def test_state_machine_advances():
    r = client.post("/execute", json={"files": files(), "kind": "state", "target": "VehicleModel::VehicleStates", "advance": 10}).json()
    assert {"name": "state", "value": "moving"} in r["checks"][0]["values"]


def test_parse_trace():
    events, rest = parse_trace(["[trace] step 1: token 1@a", "[trace] transition: x -> y (event: time)", "✓ done"])
    assert events == [
        {"type": "step", "step": 1, "token": 1, "node": "a"},
        {"type": "transition", "from": "x", "to": "y", "event": "time"},
    ]
    assert rest == ["✓ done"]


def test_missing_target_is_422():
    assert client.post("/execute", json={"files": files(), "kind": "calc"}).status_code == 422


def test_manifest_dir_is_private():
    with tools.tools_dir("r1", registry=[{"name": "x", "inputs": [{"name": "a"}], "outputs": [{"name": "b"}]}]) as (d, names):
        assert names == ["x"]
        assert (d.stat().st_mode & 0o077) == 0
        m = json.loads((d / "x.json").read_text())
        assert m["variables"] == ["a", "b"] and m["invocation"]["env"]["SYSML_RUN_ID"] == "r1"
    assert not d.exists()


def test_render_native_view():
    if shutil.which("sysml") is None and opensysml.binary_path() is None:
        pytest.skip("no binary")
    r = client.post("/render", json={"files": files(), "view": "#interconnection:VehicleModel::Vehicle", "form": "mermaid"})
    assert r.status_code == 200
    assert "flowchart" in r.json()["text"]
