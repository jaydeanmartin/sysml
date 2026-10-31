"""Analysis service tests (need PostgreSQL at DATABASE_URL)."""

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import formula
from app.builtins import to_si

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

from app.main import app  # noqa: E402


class External(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        x = req["inputs"]["x"]
        x = x["value"] if isinstance(x, dict) else x
        body = json.dumps({"outputs": {"y": {"value": x * 2}}} if x >= 0 else {"error": "negative"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def external():
    srv = HTTPServer(("127.0.0.1", 0), External)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/double"
    srv.shutdown()


def test_builtins_registered(client):
    names = {a["name"] for a in client.get("/analyses").json()}
    assert {"massRollup", "rangeEstimate", "dragForce", "rocketDeltaV"} <= names


def test_builtin_with_tool_request_and_units(client):
    r = client.post(
        "/analyses/massRollup/invoke",
        json={
            "toolName": "massRollup",
            "uri": "analysis://massRollup",
            "runId": "t-1",
            "inputs": {"engineMass": {"value": 180000, "unit": "g"}, "tankMass": {"value": 45, "unit": "kg"}, "transmissionMass": 90},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"outputs": {"total": {"value": 315.0, "unit": "kg"}}}
    inv = client.get("/invocations", params={"runId": "t-1"}).json()
    assert inv[0]["analysis"] == "massRollup" and inv[0]["status"] == "ok"


def test_missing_input_is_422(client):
    r = client.post("/analyses/massRollup/invoke", json={"inputs": {"engineMass": 1}})
    assert r.status_code == 422


def test_formula_analysis(client):
    name = f"f_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/analyses",
        json={"name": name, "kind": "formula", "inputs": [{"name": "a"}, {"name": "b"}], "outputs": [{"name": "c", "unit": "m"}], "formulas": {"c": "sqrt(a**2 + b**2)"}},
    )
    assert r.status_code == 201, r.text
    out = client.post(f"/analyses/{name}/invoke", json={"inputs": {"a": 3, "b": 4}}).json()
    assert out == {"outputs": {"c": {"value": 5.0, "unit": "m"}}}
    assert client.delete(f"/analyses/{name}").status_code == 204


def test_formula_rejects_unsafe_code(client):
    r = client.post(
        "/analyses",
        json={"name": "evil", "kind": "formula", "inputs": [{"name": "a"}], "outputs": [{"name": "c"}], "formulas": {"c": "__import__('os').system('id')"}},
    )
    assert r.status_code == 422


def test_external_analysis(client, external):
    name = f"ext_{uuid.uuid4().hex[:8]}"
    r = client.post("/analyses", json={"name": name, "kind": "external", "endpointUrl": external, "inputs": [{"name": "x"}], "outputs": [{"name": "y"}]})
    assert r.status_code == 201, r.text
    assert client.post(f"/analyses/{name}/invoke", json={"inputs": {"x": {"value": 21}}}).json() == {"outputs": {"y": {"value": 42}}}
    bad = client.post(f"/analyses/{name}/invoke", json={"inputs": {"x": -1}})
    assert bad.status_code == 502 and "negative" in bad.json()["detail"]
    # disabled analyses refuse
    client.patch(f"/analyses/{name}", params={"enabled": False})
    assert client.post(f"/analyses/{name}/invoke", json={"inputs": {"x": 1}}).status_code == 409
    client.delete(f"/analyses/{name}")


def test_builtin_cannot_be_deleted(client):
    assert client.delete("/analyses/massRollup").status_code == 409


def test_formula_and_units_helpers():
    assert formula.evaluate("max(a, 2) * g0", {"a": 1}) == pytest.approx(19.6133)
    with pytest.raises(formula.FormulaError):
        formula.evaluate("a.__class__", {"a": 1})
    assert to_si(36, "km/h", "m/s") == pytest.approx(10)
    assert to_si(2, "'m²'", "m**2") == 2
