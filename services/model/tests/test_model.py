"""Model service tests against a SysML v2 API server (SYSML_API_URL) and PostgreSQL
(DATABASE_URL, for runs). The interpreter is faked."""

import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL") or not os.environ.get("SYSML_API_URL"):
    pytest.skip("DATABASE_URL / SYSML_API_URL not set", allow_module_level=True)

os.environ["SEED_DEMO"] = "false"
os.environ["MIGRATE_LEGACY"] = "false"
from app import main, store  # noqa: E402

API = os.environ["SYSML_API_URL"]

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


def _elements(extra_part: bool) -> list[dict]:
    parts = ["a", "b"] if extra_part else ["a"]
    els = [
        {"@type": "Namespace", "@id": "P_ns", "owningRelationship": None, "ownedRelationship": [{"@id": "P_om"}], "ownedMember": [{"@id": "P"}]},
        {"@type": "OwningMembership", "@id": "P_om", "owningRelatedElement": {"@id": "P_ns"}, "ownedRelatedElement": [{"@id": "P"}]},
        {"@type": "Package", "@id": "P", "declaredName": "P", "qualifiedName": "P", "owningRelationship": {"@id": "P_om"},
         "ownedRelationship": [{"@id": f"P__{p}_om"} for p in parts]},
        {"@type": "Package", "@id": "Q", "declaredName": "Q", "qualifiedName": "Q"},  # another file
    ]
    for p in parts:
        els += [
            {"@type": "OwningMembership", "@id": f"P__{p}_om", "owningRelatedElement": {"@id": "P"}, "ownedRelatedElement": [{"@id": f"P__{p}"}]},
            {"@type": "PartUsage", "@id": f"P__{p}", "declaredName": p, "qualifiedName": f"P::{p}", "owningRelationship": {"@id": f"P__{p}_om"}},
        ]
    return els


def fake_interpreter(path, body, timeout=180):
    if path == "/parse":
        text = body["files"][0]["text"]
        bad = "ERROR" in text
        return {
            "ok": not bad,
            "diagnostics": [{"severity": "error", "message": "boom", "file": body["files"][0]["name"], "line": 1, "column": 1}] if bad else [],
            "graph": {"nodes": [], "edges": []} if bad else GRAPH,
            "summary": {},
            "elements": [] if bad else _elements("part b" in text),
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
    m = client.post(f"/projects/{p['id']}/models", json={"name": "M", "description": "about M", "source": "package P {}"})
    assert m.status_code == 201, m.text
    yield m.json()
    client.delete(f"/projects/{p['id']}")


def _api(path, **kw):
    return httpx.get(f"{API}{path}", params={"page[size]": 5000}, timeout=60, **kw).json()


def test_create_model_commits_text_and_elements_to_the_sysml_v2_api(client, model):
    assert model["headVersion"] == 1 and model["valid"]
    # only this model's own file / root packages are kept
    assert {n["id"] for n in model["graph"]["nodes"]} == {"P", "P__R", "P__r1", "P__a"}
    pid, mid = model["projectId"], model["id"]
    commits = _api(f"/projects/{pid}/commits")
    assert [c["@id"] for c in commits] == [model["commitId"]]
    assert f"Model-Id: {mid}" in commits[0]["description"]
    stored = {e["@id"]: e for e in _api(f"/projects/{pid}/commits/{model['commitId']}/elements")}
    assert stored[mid]["@type"] == "Namespace" and stored[mid]["declaredName"] == "M"
    assert stored[store.text_id(mid)]["body"] == "package P {}"
    assert stored[store.description_id(mid)]["body"] == "about M"
    assert {e.get("declaredName") for e in stored.values()} >= {"P", "a"}
    assert "Q" not in {e.get("declaredName") for e in stored.values()}  # another file of the parse
    parts = client.get(f"/models/{mid}/elements", params={"metatype": "PartUsage"}).json()
    assert [e["declaredName"] for e in parts] == ["a"]
    assert len(client.get(f"/models/{mid}/elements").json()) == len(stored)


def test_save_creates_versions_and_skips_identical(client, model):
    mid = model["id"]
    assert client.put(f"/models/{mid}/source", json={"source": "package P {}"}).json()["headVersion"] == 1
    v2 = client.put(f"/models/{mid}/source", json={"source": "package P { ERROR }", "message": "broken"}).json()
    assert v2["headVersion"] == 2 and not v2["valid"] and v2["diagnostics"][0]["message"] == "boom"
    versions = client.get(f"/models/{mid}/versions").json()
    assert [(v["version"], v["message"], v["valid"]) for v in versions] == [(2, "broken", False), (1, "Created", True)]
    assert client.get(f"/models/{mid}", params={"version": 1}).json()["source"] == "package P {}"
    assert client.get(f"/models/{mid}", params={"version": 3}).status_code == 404


def test_commits_carry_only_changes(client, model):
    mid, pid = model["id"], model["projectId"]
    v2 = client.put(f"/models/{mid}/source", json={"source": "package P { part b; }"}).json()
    added = _api(f"/projects/{pid}/commits/{v2['commitId']}/changes")
    names = {(c["payload"] or {}).get("declaredName") for c in added}
    assert "b" in names and "a" not in names
    # forget what this process committed: the previous version is re-derived from its text
    main._payloads.clear()
    v3 = client.put(f"/models/{mid}/source", json={"source": "package P {}  "}).json()
    removed = _api(f"/projects/{pid}/commits/{v3['commitId']}/changes")
    assert sorted(c["payload"] is None for c in removed) == [False, False, True, True]  # text and P changed; part b and its membership deleted
    assert [e["declaredName"] for e in client.get(f"/models/{mid}/elements", params={"metatype": "PartUsage"}).json()] == ["a"]


def test_models_are_read_back_from_the_api_server(client, model):
    mid, pid = model["id"], model["projectId"]
    # a cold cache (e.g. after a restart) reads everything from the API server
    main._model_project.clear()
    store._snapshots.data.clear()
    store._histories.data.clear()
    m = client.get(f"/models/{mid}").json()
    assert m["source"] == "package P {}" and m["description"] == "about M" and m["headVersion"] == 1
    assert [x["name"] for x in client.get(f"/projects/{pid}/models").json()] == ["M"]
    assert next(p for p in client.get("/projects").json() if p["id"] == pid)["modelCount"] == 1


def test_names_are_unique(client, model):
    pid = model["projectId"]
    assert client.post(f"/projects/{pid}/models", json={"name": "M"}).status_code == 409
    name = next(p for p in client.get("/projects").json() if p["id"] == pid)["name"]
    assert client.post("/projects", json={"name": name}).status_code == 409


def test_delete_model_removes_its_elements(client, model):
    mid, pid = model["id"], model["projectId"]
    assert client.delete(f"/models/{mid}").status_code == 204
    assert client.get(f"/models/{mid}").status_code == 404
    assert client.get(f"/projects/{pid}/models").json() == []
    head = _api(f"/projects/{pid}/commits")
    last = max(head, key=lambda c: c["created"])
    assert _api(f"/projects/{pid}/commits/{last['@id']}/elements") == []


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


def test_unknown_model_or_project_is_404(client):
    assert client.get(f"/models/{uuid.uuid4()}").status_code == 404
    assert client.get("/models/not-a-uuid").status_code == 404
    assert client.get(f"/projects/{uuid.uuid4()}/models").status_code == 404


def test_delete_project_hides_it_and_frees_its_name(client):
    p = client.post("/projects", json={"name": f"gone-{uuid.uuid4().hex[:6]}"}).json()
    assert client.post(f"/projects/{p['id']}/models", json={"name": "M", "source": "package P {}"}).status_code == 201
    assert client.delete(f"/projects/{p['id']}").status_code == 204
    assert p["id"] not in {x["id"] for x in client.get("/projects").json()}
    assert client.get(f"/projects/{p['id']}/models").status_code == 404
    again = client.post("/projects", json={"name": p["name"]})
    assert again.status_code == 201
    client.delete(f"/projects/{again.json()['id']}")
