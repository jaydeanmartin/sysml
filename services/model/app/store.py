"""Model storage in a SysML v2 API server (OMG Systems Modeling API & Services).

How the studio's data maps onto the API:

* project        -> an API Project; its default branch ("main") is the history
* model          -> a root ``Namespace`` element whose ``@id`` is the model id and whose
                    ``declaredName`` is the model name. It owns the model's packages, a
                    ``Documentation`` (the model description) and a ``TextualRepresentation``
                    (``language = "sysml"``, ``body`` = the model's text, the source of truth)
* model version  -> a Commit on main that changed the model. Its description is the save
                    message followed by trailers: ``Model-Id``, ``Model-Name``, ``Valid``,
                    ``Elements`` (and ``Authored`` for history migrated from PostgreSQL)
* elements       -> the interpreted model (OpenSysML ``api-json``). Their ids are derived
                    from qualified names, so they map to stable UUIDs: an element keeps its
                    identity across commits and each commit only carries what changed.

Commits are immutable, so everything read at a commit (the models' texts, the history up to
it) is cached by commit id.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

API_URL = os.environ.get("SYSML_API_URL", "http://sysml-api:9000")
TEXT_LANGUAGE = "sysml"
# namespace of the UUIDs given to element ids that are not UUIDs already (one sub-namespace per project)
ELEMENT_NS = uuid.UUID("0b6c1d3e-5f0a-4a56-9b0e-7c35a2f4d9e1")
_TRAILER = re.compile(r"^([A-Z][A-Za-z-]*): ?(.*)$")
PAGE = 5000


class StoreError(Exception):
    """The SysML v2 API server failed or is unreachable (HTTP 502/503)."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


@dataclass
class Commit:
    id: str
    created: str
    message: str
    trailers: dict[str, str]
    previous: str | None

    @property
    def model_id(self) -> str | None:
        return self.trailers.get("Model-Id")

    @property
    def authored(self) -> str:
        return self.trailers.get("Authored") or self.created


@dataclass
class StoredModel:
    id: str
    name: str
    description: str
    source: str


@dataclass
class Snapshot:
    """The models of a project at one commit."""

    commit: str | None
    models: dict[str, StoredModel] = field(default_factory=dict)


class LRU:
    def __init__(self, size: int):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key: Any) -> Any:
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key: Any, value: Any) -> Any:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value


# ------------------------------------------------------------------ HTTP
_http = httpx.Client(base_url=API_URL, timeout=httpx.Timeout(300, connect=5))


def _request(method: str, path: str, **kw: Any) -> httpx.Response:
    try:
        return _http.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise StoreError(f"SysML v2 API server unavailable: {exc}") from exc


def _json(r: httpx.Response, what: str) -> Any:
    if r.status_code >= 400:
        raise StoreError(f"SysML v2 API server: {what} failed ({r.status_code}) {r.text[:300]}", 502)
    return r.json()


def _get_all(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """GET a paged collection (the API pages with ``page[size]`` and ``Link: rel="next"``)."""
    out: list[dict[str, Any]] = []
    r = _request("GET", path, params={"page[size]": PAGE, **(params or {})})
    while True:
        if r.status_code == 404:
            return out
        out += _json(r, f"GET {path}")
        nxt = r.links.get("next", {}).get("url")
        if not nxt:
            return out
        r = _request("GET", nxt)


def ping() -> None:
    _json(_request("GET", "/projects", params={"page[size]": 1}), "GET /projects")


# ------------------------------------------------------------------ projects
_branches: dict[str, str] = {}  # project id -> default branch id (fixed at creation)


def projects() -> list[dict[str, Any]]:
    return _get_all("/projects")


def project(project_id: str) -> dict[str, Any] | None:
    r = _request("GET", f"/projects/{project_id}")
    if r.status_code in (400, 404):
        return None
    return _json(r, "GET project")


def create_project(name: str, description: str) -> dict[str, Any]:
    return _json(
        _request("POST", "/projects", json={"@type": "Project", "name": name, "description": description}),
        "create project",
    )


def delete_project(project_id: str) -> bool:
    """Delete a project; False if the server refused (the pilot implementation cannot
    delete a project that has commits)."""
    _request("DELETE", f"/projects/{project_id}")
    gone = project(project_id) is None
    if gone:
        _branches.pop(project_id, None)
    return gone


def head(project_id: str) -> str | None:
    """Head commit of the project's default branch (None: no commit yet)."""
    branch = _branches.get(project_id)
    if not branch:
        p = project(project_id)
        if not p:
            raise KeyError(project_id)
        branch = _branches[project_id] = p["defaultBranch"]["@id"]
    b = _json(_request("GET", f"/projects/{project_id}/branches/{branch}"), "GET branch")
    return (b.get("head") or {}).get("@id")


# ------------------------------------------------------------------ commits
def describe(message: str, trailers: dict[str, Any]) -> str:
    lines = [f"{k}: {v}" for k, v in trailers.items() if v is not None]
    return f"{message.strip() or '(no message)'}\n\n" + "\n".join(lines)


def _parse_commit(c: dict[str, Any]) -> Commit:
    text = c.get("description") or ""
    head_text, _, tail = text.rpartition("\n\n")
    trailers = {}
    for line in tail.splitlines():
        m = _TRAILER.match(line)
        if not m:
            trailers = {}
            break
        trailers[m.group(1)] = m.group(2)
    message = head_text if trailers else text
    if message == "(no message)":
        message = ""
    return Commit(c["@id"], c.get("created") or "", message, trailers, (c.get("previousCommit") or {}).get("@id"))


_histories = LRU(64)


def history(project_id: str, commit_id: str | None) -> list[Commit]:
    """Commits from the first to ``commit_id``, following ``previousCommit``."""
    if not commit_id:
        return []
    cached = _histories.get(commit_id)
    if cached is not None:
        return cached
    by_id = {c["@id"]: _parse_commit(c) for c in _get_all(f"/projects/{project_id}/commits")}
    chain, cur = [], by_id.get(commit_id)
    while cur:
        chain.append(cur)
        cur = by_id.get(cur.previous) if cur.previous else None
    return _histories.put(commit_id, chain[::-1])


def commit(project_id: str, previous: str | None, changes: list[dict[str, Any]], description: str) -> Commit:
    body: dict[str, Any] = {"@type": "Commit", "description": description, "change": changes}
    if previous:
        body["previousCommit"] = {"@id": previous}
    c = _parse_commit(_json(_request("POST", f"/projects/{project_id}/commits", json=body), "commit"))
    base = history(project_id, previous) if previous else []
    _histories.put(c.id, [*base, c])
    return c


# ------------------------------------------------------------------ reading a commit
def _query(project_id: str, commit_id: str, where: dict[str, Any], select: list[str] | None = None) -> list[dict[str, Any]]:
    """Elements of a commit matching ``where`` (only the ``select``ed properties, or all)."""
    q: dict[str, Any] = {"@type": "Query", "where": where}
    if select:
        q["select"] = select
    r = _request("POST", f"/projects/{project_id}/query-results", params={"commitId": commit_id}, json=q)
    return _json(r, "query")


def _is(prop: str, value: str) -> dict[str, Any]:
    return {"@type": "PrimitiveConstraint", "inverse": False, "operator": "=", "property": prop, "value": [value]}


_snapshots = LRU(256)


def snapshot(project_id: str, commit_id: str | None) -> Snapshot:
    """The studio models stored at a commit: root Namespaces that carry a SysML text."""
    if not commit_id:
        return Snapshot(None)
    cached = _snapshots.get(commit_id)
    if cached is not None:
        return cached
    where = {
        "@type": "CompositeConstraint", "operator": "or",
        "constraint": [_is("@type", "Namespace"), _is("@type", "TextualRepresentation"), _is("@type", "Documentation")],
    }
    rows = _query(project_id, commit_id, where, ["@id", "@type", "declaredName", "owningRelationship", "body", "language"])
    by_id = {r["@id"]: r for r in rows}
    models = {}
    for r in rows:
        if r.get("@type") != "Namespace" or r.get("owningRelationship"):
            continue
        text = by_id.get(text_id(r["@id"]))
        if not text or text.get("language") != TEXT_LANGUAGE:
            continue
        doc = by_id.get(description_id(r["@id"])) or {}
        models[r["@id"]] = StoredModel(r["@id"], r.get("declaredName") or "", doc.get("body") or "", text.get("body") or "")
    return _snapshots.put(commit_id, Snapshot(commit_id, dict(sorted(models.items(), key=lambda kv: kv[1].name))))


def remember(project_id: str, commit_id: str, snap: Snapshot) -> None:
    """Cache the snapshot of a commit this service just made (no need to read it back)."""
    _snapshots.put(commit_id, Snapshot(commit_id, dict(sorted(snap.models.items(), key=lambda kv: kv[1].name))))


def element_ids(project_id: str, commit_id: str | None) -> set[str]:
    """Ids of all elements at a commit."""
    if not commit_id:
        return set()
    every = {**_is("@id", str(uuid.UUID(int=0))), "inverse": True}
    return {r["@id"] for r in _query(project_id, commit_id, every, ["@id"])}


def elements(project_id: str, commit_id: str, metatype: str | None = None) -> list[dict[str, Any]]:
    """Elements of a commit as the API serves them (all, or of one metatype)."""
    if metatype:
        return _query(project_id, commit_id, _is("@type", metatype))
    return _get_all(f"/projects/{project_id}/commits/{commit_id}/elements")


# ------------------------------------------------------------------ interpreted elements -> API payloads
def text_id(model_id: str) -> str:
    return str(uuid.uuid5(uuid.UUID(model_id), "textual-representation"))


def description_id(model_id: str) -> str:
    return str(uuid.uuid5(uuid.UUID(model_id), "documentation"))


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def own_elements(elements: list[dict[str, Any]], root_ids: set[str]) -> list[dict[str, Any]]:
    """The elements of one file: everything owned, transitively, by the root Namespace
    that owns the given root packages (``elements`` holds every file of a parse)."""
    by_id = {e["@id"]: e for e in elements if "@id" in e}
    roots = [
        e["@id"] for e in by_id.values()
        if e.get("@type") == "Namespace" and not e.get("owningRelationship")
        and any(m.get("@id") in root_ids for m in e.get("ownedMember") or [])
    ]
    seen, todo = set(), roots or [i for i in root_ids if i in by_id]
    while todo:
        i = todo.pop()
        if i in seen or i not in by_id:
            continue
        seen.add(i)
        e = by_id[i]
        todo += [r["@id"] for k in ("ownedRelationship", "ownedRelatedElement") for r in e.get(k) or [] if isinstance(r, dict)]
    return [e for e in elements if e.get("@id") in seen]


def _membership(mid: str, owner: str, member: str) -> dict[str, Any]:
    ref = {"@id": member}
    return {
        "@type": "OwningMembership", "@id": mid, "elementId": mid, "owner": {"@id": owner},
        "owningRelatedElement": {"@id": owner}, "membershipOwningNamespace": {"@id": owner},
        "memberElement": ref, "ownedMemberElement": ref, "ownedRelatedElement": [ref], "relatedElement": [{"@id": owner}, ref],
        "source": [{"@id": owner}], "target": [ref], "isImplied": False, "ownedRelationship": [], "aliasIds": [],
    }


def to_api(
    elements: list[dict[str, Any]], project_id: str, model_id: str, name: str, description: str, source: str
) -> dict[str, dict[str, Any]]:
    """API payloads (by @id) of one model: its interpreted elements with UUID ids, under a
    root Namespace that also owns the description and the SysML text."""
    ids = {e["@id"] for e in elements if "@id" in e}
    root = next((e for e in elements if e.get("@type") == "Namespace" and not e.get("owningRelationship")), None)
    root_orig = root["@id"] if root else next(
        (r["@id"] for e in elements if isinstance(r := e.get("owningRelatedElement"), dict) and r.get("@id") not in ids), None
    )
    pns = uuid.uuid5(ELEMENT_NS, str(project_id))

    def mid(i: str) -> str:
        return model_id if i == root_orig else i if _is_uuid(i) else str(uuid.uuid5(pns, i))

    def fix(v: Any) -> Any:
        if isinstance(v, dict):
            if set(v) == {"@id"}:
                i = v["@id"]
                # standard library elements (OpenSysML gives them their UUIDs) are not in the
                # project, and the API server refuses references to elements it does not hold
                return None if _is_uuid(i) and i not in ids else {"@id": mid(i)}
            return {k: fix(x) for k, x in v.items() if not k.startswith("sysx:")}
        if isinstance(v, list):
            return [y for x in v if (y := fix(x)) is not None]
        return v

    out: dict[str, dict[str, Any]] = {}
    for e in elements:
        if "@id" not in e:
            continue
        p = fix(e)
        p["@id"] = p["elementId"] = mid(e["@id"])
        out[p["@id"]] = p
    ns = out.get(model_id) or {
        "@type": "Namespace", "@id": model_id, "elementId": model_id, "owningRelationship": None, "aliasIds": [],
        "isImpliedIncluded": False, "declaredShortName": None,
        "ownedRelationship": [{"@id": m} for m, p in out.items() if (p.get("owningRelatedElement") or {}).get("@id") == model_id],
    }
    if "ownedMember" not in ns:
        ns["ownedMember"] = [r["ownedMemberElement"] for m in ns["ownedRelationship"] if (r := out.get(m["@id"])) and r.get("ownedMemberElement")]
        ns["ownedMembership"] = list(ns["ownedRelationship"])
    ns["declaredName"] = ns["name"] = name
    out[model_id] = ns

    owner = {"@id": model_id}
    for eid, payload in (
        (description_id(model_id), {"@type": "Documentation", "body": description, "locale": None}),
        (text_id(model_id), {"@type": "TextualRepresentation", "language": TEXT_LANGUAGE, "body": source,
                             "representedElement": owner, "annotatedElement": [owner]}),
    ):
        m = str(uuid.uuid5(uuid.UUID(eid), "membership"))
        out[eid] = {
            **payload, "@id": eid, "elementId": eid, "owner": owner, "owningNamespace": owner,
            "owningRelationship": {"@id": m}, "owningMembership": {"@id": m}, "ownedRelationship": [], "aliasIds": [],
        }
        out[m] = _membership(m, model_id, eid)
        for key in ("ownedRelationship", "ownedMembership"):
            if {"@id": m} not in ns.setdefault(key, []):
                ns[key].append({"@id": m})
        if {"@id": eid} not in ns.setdefault("ownedMember", []):
            ns["ownedMember"].append({"@id": eid})
    ns["documentation"] = [{"@id": description_id(model_id)}]
    ns["textualRepresentation"] = [{"@id": text_id(model_id)}]
    return out


def prune(payloads: dict[str, dict[str, Any]], known: set[str]) -> dict[str, dict[str, Any]]:
    """Drop references to elements that are neither in ``known`` nor in ``payloads``."""
    keep = known | set(payloads)

    def fix(v: Any) -> Any:
        if isinstance(v, dict):
            if set(v) == {"@id"}:
                return v if v["@id"] in keep else None
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [y for x in v if (y := fix(x)) is not None]
        return v

    return {i: fix(p) for i, p in payloads.items()}


def changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """DataVersions turning ``before`` into ``after`` (a null payload deletes an element)."""
    out = [
        {"@type": "DataVersion", "identity": {"@id": i}, "payload": p}
        for i, p in after.items() if before.get(i) != p
    ]
    out += [{"@type": "DataVersion", "identity": {"@id": i}, "payload": None} for i in before if i not in after]
    return out


# ------------------------------------------------------------------ write lock
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock(project_id: str) -> threading.Lock:
    """Serializes the commits of a project (read head -> commit) within this service."""
    with _locks_guard:
        return _locks.setdefault(project_id, threading.Lock())


def created(c: Commit | None) -> datetime | None:
    if not c:
        return None
    try:
        return datetime.fromisoformat(c.authored.replace("Z", "+00:00"))
    except ValueError:
        return None
