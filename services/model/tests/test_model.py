"""Model service tests (need PostgreSQL at DATABASE_URL). The interpreter is faked."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ["SEED_DEMO"] = "false"
from app import main  # noqa: E402

GRAPH = {
    "nodes": [
        {"id": "P", "kind": "package", "name": "P", "qualifiedName": "P", "owner": None, "loc": {"file": "M.sysml", "line": 1}},
        {"id": "P__R", "kind": "requirement def", "name": "R", "qualifiedName": "P::R", "owner": "P", "doc": "Shall work.", "loc": {"file": "M.sysml", "line": 2}},
        {"id": "P__r1", "kind": "requirement", "name": "r1", "qualifiedName": "P::r1", "owner": "P", "loc": {"file": "M.sysml", "line": 3}},
        {"id": "P__a", "kind": "part", "name": "a", "qualifiedName": "P::a", "owner": "P", "loc": {"file": "M.sysml", "line": 4}},
        {"id": "Q", "kind": "package", "name": "Q", "qualifiedName": "Q", "owner": None, "loc": {"file": "Other.sysml", "line": 1}},
    ],
    "edges": [{"id": "e1", "kind": "satisfy", "source": "P__a", "target": "P__r1"}],
}


def fake_interpreter(path, body, timeout=180):
    if path == "/parse":
        bad = "ERROR" in body["files"][0]["text"]
        return {
            "ok": not bad,
            "diagnostics": [{"severity": "error", "message": "boom", "file": body["files"][0]["name"], "line": 1, "column": 1}] if bad else [],
            "graph": {"nodes": [], "edges": []} if bad else GRAPH,
            "summary": {},
            "elements": [] if bad else [{"@id": "P", "@type": "Package", "qualifiedName": "P"}, {"@id": "P__a", "@type": "PartUsage", "qualifiedName": "P::a", "declaredName": "a", "owner": {"@id": "P"}}, {"@id": "Q", "@type": "Package"}],
            "convertError": None,
            "elapsedMs": 3,
        }
    if path == "/execute":
        return {"runId": body["runId"], "status": "holds", "exit": 0, "elapsedMs": 5, "checks": [{"subject": "satisfy r1 by a", "status": "holds", "values": []}], "trace": [], "errors": [], "output": [], "diagnostics": [], "command": [], "tools": []}
    raise AssertionError(path)


@pytest.fixture(scope="module")
def client():
    main._interpreter = fake_interpreter
    main._invocations = lambda run_id: []
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def model(client):
    p = client.post("/projects", json={"name": f"p-{uuid.uuid4().hex[:6]}"}).json()
    m = client.post(f"/projects/{p['id']}/models", json={"name": "M", "source": "package P {}"})
    assert m.status_code == 201, m.text
    yield m.json()
    client.delete(f"/projects/{p['id']}")


def test_create_model_stores_version_and_elements(client, model):
    assert model["headVersion"] == 1 and model["valid"]
    # only this model's own file / root packages are kept
    assert {n["id"] for n in model["graph"]["nodes"]} == {"P", "P__R", "P__r1", "P__a"}
    els = client.get(f"/models/{model['id']}/elements").json()
    assert {e["@id"] for e in els} == {"P", "P__a"}
    parts = client.get(f"/models/{model['id']}/elements", params={"metatype": "PartUsage"}).json()
    assert [e["declaredName"] for e in parts] == ["a"]


def test_save_creates_versions_and_skips_identical(client, model):
    mid = model["id"]
    assert client.put(f"/models/{mid}/source", json={"source": "package P {}"}).json()["headVersion"] == 1
    v2 = client.put(f"/models/{mid}/source", json={"source": "package P { ERROR }", "message": "broken"}).json()
    assert v2["headVersion"] == 2 and not v2["valid"] and v2["diagnostics"][0]["message"] == "boom"
    versions = client.get(f"/models/{mid}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert client.get(f"/models/{mid}", params={"version": 1}).json()["source"] == "package P {}"


def test_preview_does_not_store(client, model):
    r = client.post(f"/models/{model['id']}/preview", json={"source": "package P { part x; }"}).json()
    assert r["valid"] and "elements" not in r
    assert client.get(f"/models/{model['id']}").json()["headVersion"] == 1


def test_runs_and_requirement_verdicts(client, model):
    mid = model["id"]
    run = client.post(f"/models/{mid}/runs", json={"kind": "satisfy"})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "holds"
    assert client.get(f"/runs/{run.json()['id']}").json()["kind"] == "satisfy"
    reqs = {r["name"]: r for r in client.get(f"/models/{mid}/requirements").json()}
    assert reqs["R"]["doc"] == "Shall work."
    assert reqs["r1"]["satisfiedBy"] == ["P::a"]
    assert reqs["r1"]["verdict"]["status"] == "holds"


def test_unknown_model_is_404(client):
    assert client.get(f"/models/{uuid.uuid4()}").status_code == 404
    assert client.get("/models/not-a-uuid").status_code == 404
