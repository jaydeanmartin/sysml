"""Model service tests (need PostgreSQL at DATABASE_URL). The interpreter is faked."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ["SEED_DEMO"] = "false"
from app import db, main, repo  # noqa: E402

API = "/sysml-v2"

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
            {"@type": "OwningMembership", "@id": f"P__{p}_om", "owningRelatedElement": {"@id": "P"}, "ownedRelatedElement": [{"@id": f"P__{p}"}],
             "source": [{"@id": "P"}], "target": [{"@id": f"P__{p}"}]},
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


def _all(client, path, **params):
    r = client.get(f"{API}{path}", params={"page[size]": 1000, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _part(name):
    return {"@type": "DataVersion", "payload": {"@type": "PartDefinition", "declaredName": name}}


# ------------------------------------------------------------------ Studio
def test_create_model_commits_text_and_elements(client, model):
    assert model["headVersion"] == 1 and model["valid"]
    # only this model's own file / root packages are kept
    assert {n["id"] for n in model["graph"]["nodes"]} == {"P", "P__R", "P__r1", "P__a"}
    pid, mid = model["projectId"], model["id"]
    commits = _all(client, f"/projects/{pid}/commits")
    assert [c["@id"] for c in commits] == [model["commitId"]]
    assert commits[0]["name"] == "M v1" and commits[0]["description"] == "Created" and commits[0]["previousCommit"] is None
    stored = {e["@id"]: e for e in _all(client, f"/projects/{pid}/commits/{model['commitId']}/elements")}
    assert stored[mid]["@type"] == "Namespace" and stored[mid]["declaredName"] == "M"
    assert stored[repo.text_id(mid)]["body"] == "package P {}"
    assert stored[repo.description_id(mid)]["body"] == "about M"
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
    assert all(v["commitId"] for v in versions)
    assert client.get(f"/models/{mid}", params={"version": 1}).json()["source"] == "package P {}"
    # an invalid text keeps the last interpreted elements; only the text changes
    changes = _all(client, f"/projects/{model['projectId']}/commits/{v2['commitId']}/changes")
    assert [c["identity"]["@id"] for c in changes] == [repo.text_id(mid)]
    assert [e["declaredName"] for e in client.get(f"/models/{mid}/elements", params={"metatype": "PartUsage"}).json()] == ["a"]


def test_commits_carry_only_changes(client, model):
    mid, pid = model["id"], model["projectId"]
    v2 = client.put(f"/models/{mid}/source", json={"source": "package P { part b; }"}).json()
    added = _all(client, f"/projects/{pid}/commits/{v2['commitId']}/changes")
    names = {(c["payload"] or {}).get("declaredName") for c in added}
    assert "b" in names and "a" not in names
    v3 = client.put(f"/models/{mid}/source", json={"source": "package P {}  "}).json()
    removed = _all(client, f"/projects/{pid}/commits/{v3['commitId']}/changes")
    # the text and package P change; part b and its membership are deleted
    assert sorted(c["payload"] is None for c in removed) == [False, False, True, True]
    assert [e["declaredName"] for e in client.get(f"/models/{mid}/elements", params={"metatype": "PartUsage"}).json()] == ["a"]
    assert [e["declaredName"] for e in client.get(f"/models/{mid}/elements", params={"metatype": "PartUsage", "version": 2}).json()] == ["a", "b"]
    diff = _all(client, f"/projects/{pid}/commits/{v3['commitId']}/diff", baseCommitId=model["commitId"])
    assert [d["compareData"]["identity"]["@id"] for d in diff] == [repo.text_id(mid)]


def test_names_are_unique(client, model):
    pid = model["projectId"]
    assert client.post(f"/projects/{pid}/models", json={"name": "M"}).status_code == 409
    name = next(p for p in client.get("/projects").json() if p["id"] == pid)["name"]
    assert client.post("/projects", json={"name": name}).status_code == 409
    r = client.post(f"{API}/projects", json={"@type": "Project", "name": name})
    assert r.status_code == 409 and r.json()["@type"] == "Error"


def test_delete_model_commits_the_deletion(client, model):
    mid, pid = model["id"], model["projectId"]
    assert client.delete(f"/models/{mid}").status_code == 204
    assert client.get(f"/models/{mid}").status_code == 404
    assert client.get(f"/projects/{pid}/models").json() == []
    commits = _all(client, f"/projects/{pid}/commits")
    assert len(commits) == 2 and commits[-1]["name"] == "Delete M"
    assert _all(client, f"/projects/{pid}/commits/{commits[-1]['@id']}/elements") == []
    assert _all(client, f"/projects/{pid}/commits/{commits[0]['@id']}/elements")  # the history stays


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


# ------------------------------------------------------------------ SysML v2 API
@pytest.fixture()
def project(client):
    r = client.post(f"{API}/projects", json={"@type": "Project", "name": f"api-{uuid.uuid4().hex[:6]}", "description": "d"})
    assert r.status_code == 201, r.text
    yield r.json()
    client.delete(f"{API}/projects/{r.json()['@id']}")


def test_projects_and_default_branch(client, project):
    pid = project["@id"]
    assert project["@type"] == "Project" and project["defaultBranch"] and project["alias"] == []
    branch = client.get(f"{API}/projects/{pid}/branches/{project['defaultBranch']['@id']}").json()
    assert branch["name"] == "main" and branch["head"] is None and branch["owningProject"] == {"@id": pid}
    assert client.get(f"{API}/projects/{pid}").json() == project
    # the Studio sees API projects (with no models yet)
    assert next(p for p in client.get("/projects").json() if p["id"] == pid)["modelCount"] == 0
    r = client.put(f"{API}/projects/{pid}", json={"@type": "Project", "name": project["name"] + "x", "description": None})
    assert r.status_code == 200 and r.json()["name"] == project["name"] + "x" and r.json()["description"] == ""
    assert client.get(f"{API}/projects/{uuid.uuid4()}").status_code == 404
    assert client.get(f"{API}/projects/nope").json() == {"@type": "Error", "description": "project not found"}


def test_commit_elements_and_delete(client, project):
    pid = project["@id"]
    r = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "description": "two parts", "change": [_part("A"), _part("B")]})
    assert r.status_code == 201, r.text
    c1 = r.json()
    assert c1["@type"] == "Commit" and c1["previousCommit"] is None and c1["description"] == "two parts"
    els = _all(client, f"/projects/{pid}/commits/{c1['@id']}/elements")
    assert sorted(e["declaredName"] for e in els) == ["A", "B"]
    a = next(e for e in els if e["declaredName"] == "A")
    assert a["elementId"] == a["@id"]
    assert client.get(f"{API}/projects/{pid}/commits/{c1['@id']}/elements/{a['@id']}").json() == a
    assert len(_all(client, f"/projects/{pid}/commits/{c1['@id']}/roots")) == 2
    # update A, delete B
    b = next(e for e in els if e["declaredName"] == "B")
    c2 = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "change": [
        {"@type": "DataVersion", "identity": {"@id": a["@id"]}, "payload": {**a, "declaredName": "A2"}},
        {"@type": "DataVersion", "identity": {"@id": b["@id"]}, "payload": None},
    ]}).json()
    assert c2["previousCommit"] == {"@id": c1["@id"]}
    assert [e["declaredName"] for e in _all(client, f"/projects/{pid}/commits/{c2['@id']}/elements")] == ["A2"]
    assert sorted(e["declaredName"] for e in _all(client, f"/projects/{pid}/commits/{c1['@id']}/elements")) == ["A", "B"]
    changes = _all(client, f"/projects/{pid}/commits/{c2['@id']}/changes")
    assert {(ch["identity"]["@id"], ch["payload"] is None) for ch in changes} == {(a["@id"], False), (b["@id"], True)}
    one = client.get(f"{API}/projects/{pid}/commits/{c2['@id']}/changes/{changes[0]['@id']}").json()
    assert one == changes[0]
    diff = _all(client, f"/projects/{pid}/commits/{c2['@id']}/diff", baseCommitId=c1["@id"])
    assert len(diff) == 2 and all(d["@type"] == "DataDifference" for d in diff)
    assert client.get(f"{API}/projects/{pid}/commits/{c2['@id']}/elements/{b['@id']}").status_code == 404
    # optimistic concurrency for clients that name the commit they build on
    stale = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "previousCommit": {"@id": c1["@id"]}, "change": [_part("C")]})
    assert stale.status_code == 409
    bad = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "change": [{"payload": {"name": "untyped"}}]})
    assert bad.status_code == 400 and bad.json()["@type"] == "Error"


def test_paging_links(client, project):
    pid = project["@id"]
    c = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "change": [_part(f"P{i}") for i in range(5)]}).json()
    path = f"{API}/projects/{pid}/commits/{c['@id']}/elements"
    seen, url, pages = [], path + "?page[size]=2", 0
    while url:
        r = client.get(url)
        seen += [e["@id"] for e in r.json()]
        pages += 1
        url = r.links.get("next", {}).get("url")
    assert pages == 3 and len(seen) == 5 and seen == sorted(seen)
    back = client.get(path, params={"page[size]": 2, "page[before]": seen[4]})
    assert [e["@id"] for e in back.json()] == seen[2:4] and "prev" in back.links


def test_relationships(client, model):
    pid, cid, mid = model["projectId"], model["commitId"], model["id"]
    pkg = next(e for e in client.get(f"/models/{mid}/elements", params={"metatype": "Package"}).json())
    out = _all(client, f"/projects/{pid}/commits/{cid}/elements/{pkg['@id']}/relationships", direction="out")
    assert [r["@type"] for r in out] == ["OwningMembership"]
    assert _all(client, f"/projects/{pid}/commits/{cid}/elements/{pkg['@id']}/relationships", direction="in") == []


def test_queries(client, project):
    pid = project["@id"]
    client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "change": [
        _part("A"), _part("B"),
        {"@type": "DataVersion", "payload": {"@type": "PartUsage", "declaredName": "a", "isComposite": True}},
        {"@type": "DataVersion", "payload": {"@type": "AttributeUsage", "declaredName": "mass", "lower": 3}},
    ]})
    run = lambda where, **kw: client.post(f"{API}/projects/{pid}/query-results", json={"@type": "Query", "where": where, **kw})

    def names(r):
        assert r.status_code == 200, r.text
        return sorted(e.get("declaredName") for e in r.json())

    eq = lambda prop, *v, **kw: {"@type": "PrimitiveConstraint", "property": prop, "operator": "=", "value": list(v), **kw}
    assert names(run(eq("@type", "PartDefinition"))) == ["A", "B"]
    assert names(run(eq("declaredName", "A", "a"))) == ["A", "a"]
    assert names(run(eq("@type", "PartDefinition", inverse=True))) == ["a", "mass"]
    assert names(run({"@type": "PrimitiveConstraint", "property": "@type", "operator": "instanceOf", "value": ["Usage"]})) == ["a", "mass"]
    assert names(run({"@type": "PrimitiveConstraint", "property": "lower", "operator": ">=", "value": [3]})) == ["mass"]
    assert names(run({"@type": "CompositeConstraint", "operator": "or", "constraint": [eq("declaredName", "A"), eq("isComposite", True)]})) == ["A", "a"]
    selected = run(eq("declaredName", "A"), select=["declaredName"]).json()
    assert set(selected[0]) == {"@id", "@type", "declaredName"}
    assert run({"@type": "PrimitiveConstraint", "property": "x", "operator": "~", "value": [1]}).status_code == 400
    # stored queries
    q = client.post(f"{API}/projects/{pid}/queries", json={"@type": "Query", "name": "defs", "where": eq("@type", "PartDefinition")})
    assert q.status_code == 201, q.text
    qid = q.json()["@id"]
    assert names(client.get(f"{API}/projects/{pid}/queries/{qid}/results")) == ["A", "B"]
    assert [x["@id"] for x in _all(client, f"/projects/{pid}/queries")] == [qid]
    assert client.delete(f"{API}/projects/{pid}/queries/{qid}").status_code == 200
    assert client.get(f"{API}/projects/{pid}/queries/{qid}").status_code == 404


def test_branches_tags_and_merge(client, project):
    pid, main_id = project["@id"], project["defaultBranch"]["@id"]
    base = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "change": [_part("A"), _part("B")]}).json()
    a, b = sorted(_all(client, f"/projects/{pid}/commits/{base['@id']}/elements"), key=lambda e: e["declaredName"])
    tag = client.post(f"{API}/projects/{pid}/tags", json={"@type": "Tag", "name": "v1", "taggedCommit": {"@id": base["@id"]}})
    assert tag.status_code == 201 and tag.json()["taggedCommit"] == {"@id": base["@id"]}
    r = client.post(f"{API}/projects/{pid}/branches", json={"@type": "Branch", "name": "feature", "head": {"@id": base["@id"]}})
    assert r.status_code == 201, r.text
    feature = r.json()["@id"]
    assert client.post(f"{API}/projects/{pid}/branches", json={"@type": "Branch", "name": "feature", "head": None}).status_code == 409

    def change(branch, el, name):
        return client.post(f"{API}/projects/{pid}/commits", params={"branchId": branch}, json={"@type": "Commit", "change": [
            {"@type": "DataVersion", "identity": {"@id": el["@id"]}, "payload": {**el, "declaredName": name}}]}).json()

    f1 = change(feature, a, "A-feature")
    m1 = change(main_id, b, "B-main")
    merged = client.post(f"{API}/projects/{pid}/branches/{main_id}/merge", params={"sourceCommitId": f1["@id"]})
    assert merged.status_code == 201, merged.text
    mc = merged.json()
    assert mc["previousCommit"] == {"@id": m1["@id"]}  # the reference implementation's single previous commit
    repo.PREVIOUS_COMMIT = "array"  # as in the OpenAPI document: every parent of a merge commit
    try:
        both = client.get(f"{API}/projects/{pid}/commits/{mc['@id']}").json()["previousCommit"]
        assert {p["@id"] for p in both} == {m1["@id"], f1["@id"]}
    finally:
        repo.PREVIOUS_COMMIT = "object"
    assert sorted(e["declaredName"] for e in _all(client, f"/projects/{pid}/commits/{mc['@id']}/elements")) == ["A-feature", "B-main"]
    # both sides change A: a conflict, unless resolved
    f2 = change(feature, a, "A-f2")
    change(main_id, a, "A-m2")
    conflict = client.post(f"{API}/projects/{pid}/branches/{main_id}/merge", params={"sourceCommitId": f2["@id"]})
    assert conflict.status_code == 409 and conflict.json() == [{"@id": a["@id"], "@type": "DataIdentity"}]
    resolved = client.post(f"{API}/projects/{pid}/branches/{main_id}/merge", params={"sourceCommitId": f2["@id"]},
                           json=[{**a, "declaredName": "A-resolved"}])
    assert resolved.status_code == 201, resolved.text
    names = sorted(e["declaredName"] for e in _all(client, f"/projects/{pid}/commits/{resolved.json()['@id']}/elements"))
    assert names == ["A-resolved", "B-main"]
    assert client.delete(f"{API}/projects/{pid}/branches/{main_id}").status_code == 409
    deleted = client.delete(f"{API}/projects/{pid}/branches/{feature}")
    assert deleted.status_code == 200 and deleted.json()["deleted"]
    assert [t["name"] for t in _all(client, f"/projects/{pid}/tags")] == ["v1"]


def test_api_commit_changing_a_models_text_becomes_a_studio_version(client, model):
    pid, mid = model["projectId"], model["id"]
    text = client.get(f"{API}/projects/{pid}/commits/{model['commitId']}/elements/{repo.text_id(mid)}").json()
    r = client.post(f"{API}/projects/{pid}/commits", json={"@type": "Commit", "description": "edited elsewhere", "change": [
        {"@type": "DataVersion", "identity": {"@id": text["@id"]}, "payload": {**text, "body": "package P { part b; }"}}]})
    assert r.status_code == 201, r.text
    m = client.get(f"/models/{mid}").json()
    assert m["headVersion"] == 2 and m["source"] == "package P { part b; }" and m["versionMessage"] == "edited elsewhere"
    assert m["commitId"] == r.json()["@id"]
    # the next save in the Studio builds on it
    v3 = client.put(f"/models/{mid}/source", json={"source": "package P { part b; } "}).json()
    assert v3["headVersion"] == 3


def test_migrates_versions_stored_before_the_api(client):
    """Databases from before the SysML v2 API store: versions with their elements in models.element."""
    name = f"old-{uuid.uuid4().hex[:6]}"
    with db.pool.connection() as c, c.transaction():
        c.execute("""CREATE TABLE IF NOT EXISTS models.element (
            version_id BIGINT NOT NULL, element_id TEXT NOT NULL, metatype TEXT NOT NULL, name TEXT,
            qualified_name TEXT, owner_id TEXT, data JSONB NOT NULL, PRIMARY KEY (version_id, element_id))""")
        p = c.execute("INSERT INTO models.project (name) VALUES (%s) RETURNING *", (name,)).fetchone()
        m = c.execute("INSERT INTO models.model (project_id, name, head_version) VALUES (%s,'Old',2) RETURNING *", (p["id"],)).fetchone()
        for n, extra in ((1, False), (2, True)):
            v = c.execute(
                "INSERT INTO models.model_version (model_id, version, source, message, valid) VALUES (%s,%s,%s,%s,true) RETURNING id",
                (m["id"], n, f"package P {{ v{n} }}", f"save {n}"),
            ).fetchone()
            for e in repo.own_elements(_elements(extra), {"P"}):
                c.execute("INSERT INTO models.element (version_id, element_id, metatype, data) VALUES (%s,%s,%s,%s)",
                          (v["id"], e["@id"], e["@type"], __import__("json").dumps(e)))
        repo.migrate(c)
    try:
        commits = _all(client, f"/projects/{p['id']}/commits")
        assert [x["description"] for x in commits] == ["save 1", "save 2"]
        parts = client.get(f"/models/{m['id']}/elements", params={"metatype": "PartUsage"}).json()
        assert [e["declaredName"] for e in parts] == ["a", "b"]
        assert [v["commitId"] for v in client.get(f"/models/{m['id']}/versions").json()] == [commits[1]["@id"], commits[0]["@id"]]
    finally:
        client.delete(f"/projects/{p['id']}")
