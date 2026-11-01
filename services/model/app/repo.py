"""SysML v2 API repository in PostgreSQL: projects, branches, tags, commits and
element versions as defined by the OMG Systems Modeling API and Services 1.0.

Each commit stores only its changes (``models.data_versions``, one row per
created, updated or deleted element) plus an index of every element it holds
(``models.commit_elements``: element id -> data version), so reading a commit
is a single join whatever the length of the history.

It also maps the Studio onto that structure: a model is a root ``Namespace``
whose id is the model id. The namespace owns the model's packages, a
``Documentation`` (the model description) and a ``TextualRepresentation``
(language "sysml", the source text).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from psycopg import Connection

# Commit.previousCommit: a single {"@id"} (or null), as the reference implementation
# (SysML-v2-API-Services) and its clients use it, or "array" as in the OpenAPI document
PREVIOUS_COMMIT = os.environ.get("SYSML_API_PREVIOUS_COMMIT", "object")

ELEMENT_NS = uuid.UUID("0b6c1d3e-5f0a-4a56-9b0e-7c35a2f4d9e1")
TEXT_LANGUAGE = "sysml"
DEFAULT_BRANCH = "main"
NOT_ELEMENTS = ("ProjectUsage", "ExternalData", "ExternalRelationship")

_SUBTYPES: dict[str, list[str]] = json.loads((Path(__file__).with_name("metaclasses.json")).read_text())["subtypes"]


class RepoError(Exception):
    def __init__(self, status: int, description: str, body: Any = None):
        super().__init__(description)
        self.status, self.description, self.body = status, description, body


def is_uuid(s: Any) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except ValueError:
        return False


def ref(i: Any) -> dict[str, str] | None:
    return {"@id": str(i)} if i else None


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


# ------------------------------------------------------------------ records as API JSON
def project_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "@id": str(r["id"]), "@type": "Project", "alias": r["alias"], "created": _iso(r["created_at"]),
        "defaultBranch": ref(r["default_branch_id"]), "description": r["description"], "name": r["name"],
    }


def branch_json(r: dict[str, Any], deleted: datetime | None = None) -> dict[str, Any]:
    return {
        "@id": str(r["id"]), "@type": "Branch", "alias": r["alias"], "created": _iso(r["created_at"]),
        "deleted": _iso(deleted), "description": r["description"], "head": ref(r["head_commit_id"]), "name": r["name"],
        "owningProject": ref(r["project_id"]), "referencedCommit": ref(r["head_commit_id"]),
    }


def tag_json(r: dict[str, Any], deleted: datetime | None = None) -> dict[str, Any]:
    return {
        "@id": str(r["id"]), "@type": "Tag", "alias": r["alias"], "created": _iso(r["created_at"]),
        "deleted": _iso(deleted), "description": r["description"], "name": r["name"], "owningProject": ref(r["project_id"]),
        "referencedCommit": ref(r["tagged_commit_id"]), "taggedCommit": ref(r["tagged_commit_id"]),
    }


def commit_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "@id": str(r["id"]), "@type": "Commit", "alias": r["alias"], "created": _iso(r["created_at"]),
        "description": r["description"], "name": r["name"], "owningProject": ref(r["project_id"]),
        "previousCommit": [ref(p) for p in r["previous_commit_ids"]] if PREVIOUS_COMMIT == "array"
        else ref(r["previous_commit_ids"][0] if r["previous_commit_ids"] else None),
    }


def version_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "@id": str(r["id"]), "@type": "DataVersion", "alias": [], "description": None, "name": None,
        "identity": {"@id": str(r["element_id"]), "@type": "DataIdentity", "alias": [], "description": None, "name": None},
        "payload": r["payload"],
    }


def query_json(r: dict[str, Any]) -> dict[str, Any]:
    q = r["query"]
    return {
        "@id": str(r["id"]), "@type": "Query", "alias": r["alias"], "description": r["description"], "name": r["name"],
        "owningProject": ref(r["project_id"]), "select": q.get("select") or [], "where": q.get("where"),
    }


# ------------------------------------------------------------------ projects and branches
def create_project(c: Connection, name: str, description: str | None = "", alias: Iterable[str] = (),
                   project_id: Any = None, created: datetime | None = None) -> dict[str, Any]:
    """A project with an empty default branch "main". Names are unique (409)."""
    p = c.execute(
        """INSERT INTO models.project (id, name, description, alias, created_at)
           VALUES (coalesce(%s, gen_random_uuid()), %s, coalesce(%s, ''), %s, coalesce(%s, now()))
           ON CONFLICT (name) DO NOTHING RETURNING *""",
        (project_id, name, description, list(alias), created),
    ).fetchone()
    if not p:
        raise RepoError(409, f"project '{name}' already exists")
    return ensure_default_branch(c, p)


def ensure_default_branch(c: Connection, p: dict[str, Any]) -> dict[str, Any]:
    if p["default_branch_id"]:
        return p
    b = c.execute(
        "INSERT INTO models.branches (project_id, name, created_at) VALUES (%s,%s,%s) RETURNING id",
        (p["id"], DEFAULT_BRANCH, p["created_at"]),
    ).fetchone()
    return c.execute("UPDATE models.project SET default_branch_id=%s WHERE id=%s RETURNING *", (b["id"], p["id"])).fetchone()


def project(c: Connection, project_id: Any) -> dict[str, Any]:
    p = c.execute("SELECT * FROM models.project WHERE id=%s", (project_id,)).fetchone() if is_uuid(project_id) else None
    if not p:
        raise RepoError(404, "project not found")
    return p


def branch(c: Connection, project_id: Any, branch_id: Any, lock: bool = False) -> dict[str, Any]:
    b = c.execute(
        "SELECT * FROM models.branches WHERE id=%s AND project_id=%s" + (" FOR UPDATE" if lock else ""), (branch_id, project_id)
    ).fetchone() if is_uuid(branch_id) else None
    if not b:
        raise RepoError(404, "branch not found")
    return b


def head(c: Connection, project_id: Any) -> Any:
    """Head commit id of the project's default branch (None while it has no commit)."""
    r = c.execute(
        "SELECT b.head_commit_id FROM models.project p JOIN models.branches b ON b.id=p.default_branch_id WHERE p.id=%s",
        (project_id,),
    ).fetchone()
    return r and r["head_commit_id"]


def commit_row(c: Connection, project_id: Any, commit_id: Any) -> dict[str, Any]:
    r = c.execute(
        "SELECT * FROM models.commits WHERE id=%s AND project_id=%s", (commit_id, project_id)
    ).fetchone() if is_uuid(commit_id) else None
    if not r:
        raise RepoError(404, "commit not found")
    return r


# ------------------------------------------------------------------ commits
def commit(
    c: Connection, project_id: Any, branch_id: Any, changes: list[tuple[str, dict[str, Any] | None]], *,
    name: str | None = None, description: str | None = None, alias: Iterable[str] = (),
    model_id: Any = None, created: datetime | None = None, expected_head: Any = "any", merged: Any = None,
) -> dict[str, Any]:
    """Commit (element id, payload or None to delete) changes on top of the branch head.

    Must run inside a transaction: the branch row is locked until it ends, so
    commits to one branch are serialised across workers. ``model_id`` marks the
    changed elements as belonging to a Studio model; otherwise an updated element
    keeps the model it had."""
    b = branch(c, project_id, branch_id, lock=True)
    parent = b["head_commit_id"]
    if expected_head != "any" and str(expected_head or "") != str(parent or ""):
        raise RepoError(409, f"the branch head is {parent}, not {expected_head}")
    by_id = dict(changes)  # the last change of an element wins
    previous = [p for p in (parent, merged) if p]
    cm = c.execute(
        """INSERT INTO models.commits (project_id, name, description, alias, previous_commit_ids, created_at)
           VALUES (%s,%s,%s,%s,%s,coalesce(%s, now())) RETURNING *""",
        (project_id, name, description, list(alias), previous, created),
    ).fetchone()
    ids = list(by_id)
    owners: dict[str, Any] = {}
    if model_id is None and parent and ids:
        owners = {
            str(r["element_id"]): r["model_id"] for r in c.execute(
                """SELECT ce.element_id, dv.model_id FROM models.commit_elements ce
                   JOIN models.data_versions dv ON dv.id=ce.version_id
                   WHERE ce.commit_id=%s AND ce.element_id = ANY(%s::uuid[])""",
                (parent, ids),
            ).fetchall()
        }
    with c.cursor().copy("COPY models.data_versions (commit_id, element_id, payload, model_id) FROM STDIN") as cp:
        for i, payload in by_id.items():
            cp.write_row((cm["id"], i, None if payload is None else json.dumps(payload), model_id or owners.get(i)))
    if parent:
        c.execute(
            """INSERT INTO models.commit_elements (commit_id, element_id, version_id)
               SELECT %s, element_id, version_id FROM models.commit_elements
               WHERE commit_id=%s AND element_id <> ALL(%s::uuid[])""",
            (cm["id"], parent, ids),
        )
    c.execute(
        """INSERT INTO models.commit_elements (commit_id, element_id, version_id)
           SELECT commit_id, element_id, id FROM models.data_versions WHERE commit_id=%s AND payload IS NOT NULL""",
        (cm["id"],),
    )
    c.execute("UPDATE models.branches SET head_commit_id=%s WHERE id=%s", (cm["id"], b["id"]))
    return cm


def parse_changes(changes: Any) -> list[tuple[str, dict[str, Any] | None]]:
    """CommitRequest.change (DataVersionRequests) as (element id, payload) pairs. A
    missing payload is a deletion, like a null one (some clients omit null fields)."""
    if not isinstance(changes, list):
        raise RepoError(400, "'change' must be a list of DataVersion")
    out = []
    for dv in changes:
        if not isinstance(dv, dict):
            raise RepoError(400, "each change must be a DataVersion")
        payload, identity = dv.get("payload"), (dv.get("identity") or {}).get("@id")
        if payload is None:
            if not identity:
                raise RepoError(400, "a deletion (null payload) needs 'identity'")
        elif not isinstance(payload, dict) or not isinstance(payload.get("@type"), str):
            raise RepoError(400, "a payload must be an object with '@type'")
        else:
            identity = identity or payload.get("@id") or str(uuid.uuid4())
            payload = {**payload, "@id": str(identity)}
            if payload["@type"] not in NOT_ELEMENTS:
                payload.setdefault("elementId", str(identity))
        if not is_uuid(identity):
            raise RepoError(400, f"'{identity}' is not a UUID")
        out.append((str(uuid.UUID(str(identity))), payload))
    return out


def snapshot(c: Connection, commit_id: Any) -> dict[str, Any]:
    """{element id: data version id} of everything a commit holds."""
    if not commit_id:
        return {}
    return {
        str(r["element_id"]): r["version_id"]
        for r in c.execute("SELECT element_id, version_id FROM models.commit_elements WHERE commit_id=%s", (commit_id,))
    }


def payloads(c: Connection, version_ids: Iterable[Any]) -> dict[Any, dict[str, Any]]:
    ids = list(version_ids)
    return {r["id"]: r for r in c.execute("SELECT * FROM models.data_versions WHERE id = ANY(%s)", (ids,))} if ids else {}


def model_elements(c: Connection, commit_id: Any, model_id: Any) -> dict[str, dict[str, Any]]:
    """{element id: payload} of one Studio model at a commit."""
    if not commit_id:
        return {}
    return {
        str(r["element_id"]): r["payload"] for r in c.execute(
            """SELECT ce.element_id, dv.payload FROM models.commit_elements ce
               JOIN models.data_versions dv ON dv.id=ce.version_id WHERE ce.commit_id=%s AND dv.model_id=%s""",
            (commit_id, model_id),
        )
    }


def ancestors(c: Connection, commit_id: Any) -> dict[str, int]:
    """{commit id: seq} of a commit and everything before it."""
    if not commit_id:
        return {}
    rows = c.execute(
        """WITH RECURSIVE anc(id) AS (
               SELECT %s::uuid
               UNION SELECT unnest(c.previous_commit_ids) FROM models.commits c JOIN anc ON c.id=anc.id)
           SELECT c.id, c.seq FROM anc JOIN models.commits c ON c.id=anc.id""",
        (commit_id,),
    ).fetchall()
    return {str(r["id"]): r["seq"] for r in rows}


def diff(c: Connection, base_id: Any, compare_id: Any) -> list[dict[str, Any]]:
    """DataDifferences between two commits, ordered by element id."""
    base, comp = snapshot(c, base_id), snapshot(c, compare_id)
    changed = sorted(i for i in base.keys() | comp.keys() if base.get(i) != comp.get(i))
    data = payloads(c, [v for i in changed for v in (base.get(i), comp.get(i)) if v])
    out = []
    for i in changed:
        b, k = data.get(base.get(i)), data.get(comp.get(i))
        if b and k and b["payload"] == k["payload"]:
            continue
        out.append({"@type": "DataDifference", "baseData": b and version_json(b), "compareData": k and version_json(k)})
    return out


def merge(c: Connection, project_id: Any, branch_id: Any, source_id: Any, description: str | None,
          resolutions: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a commit into a branch (three-way, against their latest common ancestor).
    Fast-forwards when the branch head is an ancestor of the source. Elements changed
    differently on both sides conflict (409 with their identities) unless
    ``resolutions`` holds the payload to keep for them."""
    b = branch(c, project_id, branch_id, lock=True)
    src = commit_row(c, project_id, source_id)
    target = b["head_commit_id"] and str(b["head_commit_id"])
    src_anc = ancestors(c, src["id"])
    if not target or target in src_anc:
        c.execute("UPDATE models.branches SET head_commit_id=%s WHERE id=%s", (src["id"], b["id"]))
        return src
    tgt_anc = ancestors(c, target)
    if str(src["id"]) in tgt_anc:
        return commit_row(c, project_id, target)  # already merged
    common = tgt_anc.keys() & src_anc.keys()
    base_id = max(common, key=lambda i: tgt_anc[i]) if common else None
    base, ours, theirs = snapshot(c, base_id), snapshot(c, target), snapshot(c, src["id"])
    ours_changed = {i for i in base.keys() | ours.keys() if base.get(i) != ours.get(i)}
    theirs_changed = {i for i in base.keys() | theirs.keys() if base.get(i) != theirs.get(i)}
    both = ours_changed & theirs_changed
    data = payloads(c, [v for i in both for v in (ours.get(i), theirs.get(i)) if v])
    resolved = {str(p.get("@id")): p for p in resolutions if isinstance(p, dict)}

    def body(i: str) -> Any:
        v = data.get(ours.get(i)) if ours.get(i) else None
        return v and v["payload"]

    conflicts = sorted(
        i for i in both
        if body(i) != (data[theirs[i]]["payload"] if theirs.get(i) else None) and i not in resolved
    )
    if conflicts:
        raise RepoError(409, "merge conflict", [{"@id": i, "@type": "DataIdentity"} for i in conflicts])
    take = payloads(c, [theirs[i] for i in theirs_changed - both if theirs.get(i)])
    changes: list[tuple[str, dict[str, Any] | None]] = [
        (i, take[theirs[i]]["payload"] if theirs.get(i) else None) for i in sorted(theirs_changed - both)
    ]
    changes += [(i, resolved[i]) for i in sorted(both & resolved.keys())]
    return commit(c, project_id, branch_id, changes, description=description or f"Merge {src['id']}", merged=src["id"])


# ------------------------------------------------------------------ queries
def subtypes(metaclass: str) -> set[str]:
    out, todo = set(), [metaclass]
    while todo:
        t = todo.pop()
        if t not in out:
            out.add(t)
            todo += _SUBTYPES.get(t, [])
    return out


def where_sql(con: Any) -> tuple[str, list[Any]]:
    """SQL (over ``dv.payload``) for a Query's PrimitiveConstraint / CompositeConstraint."""
    if not isinstance(con, dict):
        raise RepoError(400, "a constraint must be an object")
    kind = con.get("@type")
    if kind == "CompositeConstraint":
        op = con.get("operator")
        parts = con.get("constraint")
        if op not in ("and", "or") or not isinstance(parts, list) or not parts:
            raise RepoError(400, "a CompositeConstraint needs operator 'and'/'or' and a list of constraints")
        sqls = [where_sql(p) for p in parts]
        return "(" + f" {op.upper()} ".join(s for s, _ in sqls) + ")", [x for _, p in sqls for x in p]
    if kind != "PrimitiveConstraint":
        raise RepoError(400, "a constraint must be a PrimitiveConstraint or a CompositeConstraint")
    prop, op, values = con.get("property"), con.get("operator"), con.get("value")
    if not isinstance(prop, str):
        raise RepoError(400, "a PrimitiveConstraint needs a 'property'")
    values = values if isinstance(values, list) else [values]
    f = "dv.payload -> %s::text"
    if op in ("=", "in"):
        terms, params = [], []
        for v in values:
            if v is None:
                terms.append(f"({f} IS NULL OR {f} = 'null'::jsonb)")
                params += [prop, prop]
            else:
                terms.append(f"({f} = %s::jsonb OR (jsonb_typeof({f}) = 'array' AND {f} @> %s::jsonb))")
                params += [prop, json.dumps(v), prop, prop, json.dumps([v])]
        sql = "(" + " OR ".join(terms) + ")"
    elif op == "instanceOf":
        types = sorted({t for v in values if isinstance(v, str) for t in subtypes(v)})
        sql, params = f"({f} #>> '{{}}') = ANY(%s)", [prop, types]
    elif op in ("<", "<=", ">", ">="):
        v = values[0] if values else None
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise RepoError(400, f"operator '{op}' needs a number or a string value")
        if isinstance(v, str):
            sql, params = f"(jsonb_typeof({f}) = 'string' AND ({f} #>> '{{}}') {op} %s)", [prop, prop, v]
        else:
            sql, params = f"(jsonb_typeof({f}) = 'number' AND ({f} #>> '{{}}')::numeric {op} %s)", [prop, prop, v]
    else:
        raise RepoError(400, f"unknown operator '{op}'")
    return (f"NOT coalesce({sql}, false)" if con.get("inverse") else f"coalesce({sql}, false)"), params


def query(c: Connection, commit_id: Any, where: Any, select: list[str] | None = None) -> list[dict[str, Any]]:
    """Elements of a commit that satisfy ``where`` (all of them when it is null)."""
    if not commit_id:
        return []
    sql, params = where_sql(where) if where else ("true", [])
    rows = c.execute(
        f"""SELECT dv.payload FROM models.commit_elements ce JOIN models.data_versions dv ON dv.id=ce.version_id
            WHERE ce.commit_id=%s AND {sql} ORDER BY ce.element_id""",
        [commit_id, *params],
    ).fetchall()
    if not select:
        return [r["payload"] for r in rows]
    keep = ["@id", "@type", *select]
    return [{k: r["payload"][k] for k in keep if k in r["payload"]} for r in rows]


# ------------------------------------------------------------------ Studio models as elements
def text_id(model_id: Any) -> str:
    return str(uuid.uuid5(uuid.UUID(str(model_id)), "textual-representation"))


def description_id(model_id: Any) -> str:
    return str(uuid.uuid5(uuid.UUID(str(model_id)), "documentation"))


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
    m = {"@id": member}
    return {
        "@type": "OwningMembership", "@id": mid, "elementId": mid, "owner": {"@id": owner},
        "owningRelatedElement": {"@id": owner}, "membershipOwningNamespace": {"@id": owner},
        "memberElement": m, "ownedMemberElement": m, "ownedRelatedElement": [m], "relatedElement": [{"@id": owner}, m],
        "source": [{"@id": owner}], "target": [m], "isImplied": False, "ownedRelationship": [], "aliasIds": [],
    }


def to_api(
    elements: list[dict[str, Any]], project_id: Any, model_id: Any, name: str, description: str, source: str
) -> dict[str, dict[str, Any]]:
    """Payloads (by @id) of one model: its interpreted elements with UUID ids, under a
    root Namespace that also owns the description and the SysML text.

    Ids are derived from the interpreter's ids (stable across saves), so an
    unchanged element keeps its id and payload. Ids that already are UUIDs (the
    standard library) are kept."""
    model_id = str(model_id)
    ids = {e["@id"] for e in elements if "@id" in e}
    root = next((e for e in elements if e.get("@type") == "Namespace" and not e.get("owningRelationship")), None)
    root_orig = root["@id"] if root else next(
        (r["@id"] for e in elements if isinstance(r := e.get("owningRelatedElement"), dict) and r.get("@id") not in ids), None
    )
    pns = uuid.uuid5(ELEMENT_NS, str(project_id))

    def mid(i: str) -> str:
        return model_id if i == root_orig else i if is_uuid(i) else str(uuid.uuid5(pns, i))

    def fix(v: Any) -> Any:
        if isinstance(v, dict):
            if set(v) == {"@id"}:
                return {"@id": mid(v["@id"])}
            return {k: fix(x) for k, x in v.items() if not k.startswith("sysx:")}
        if isinstance(v, list):
            return [fix(x) for x in v]
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


def changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any] | None]]:
    """(element id, payload or None) turning ``before`` into ``after``."""
    out: list[tuple[str, dict[str, Any] | None]] = [(i, p) for i, p in after.items() if before.get(i) != p]
    return out + [(i, None) for i in before if i not in after]


def save_model(
    c: Connection, model: dict[str, Any], elements: list[dict[str, Any]] | None, source: str, *,
    message: str, version: int, created: datetime | None = None,
) -> dict[str, Any]:
    """Commit a model version to the project's default branch: only the elements that
    changed. ``elements`` None (the text did not interpret) keeps the previous
    elements and updates only the text."""
    p = project(c, model["project_id"])
    b = branch(c, p["id"], p["default_branch_id"], lock=True)
    before = model_elements(c, b["head_commit_id"], model["id"])
    after = to_api(elements or [], p["id"], model["id"], model["name"], model["description"], source)
    if elements is None and before:
        after = {**before, text_id(model["id"]): after[text_id(model["id"])], description_id(model["id"]): after[description_id(model["id"])]}
    return commit(
        c, p["id"], b["id"], changes(before, after), name=f"{model['name']} v{version}",
        description=message or None, model_id=model["id"], created=created,
    )


def delete_model(c: Connection, model: dict[str, Any]) -> dict[str, Any] | None:
    """Commit the deletion of every element of a model."""
    p = project(c, model["project_id"])
    b = branch(c, p["id"], p["default_branch_id"], lock=True)
    before = model_elements(c, b["head_commit_id"], model["id"])
    if not before:
        return None
    return commit(c, p["id"], b["id"], changes(before, {}), name=f"Delete {model['name']}", model_id=model["id"])


def studio_text_changes(c: Connection, project_id: Any, commit_row_: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """(model, new text) for each Studio model whose TextualRepresentation a commit changed."""
    rows = c.execute(
        "SELECT m.*, v.source FROM models.model m LEFT JOIN models.model_version v ON v.model_id=m.id AND v.version=m.head_version WHERE m.project_id=%s",
        (project_id,),
    ).fetchall()
    by_text = {text_id(m["id"]): m for m in rows}
    out = []
    for r in c.execute(
        "SELECT element_id, payload FROM models.data_versions WHERE commit_id=%s AND element_id = ANY(%s::uuid[])",
        (commit_row_["id"], list(by_text)),
    ):
        m = by_text[str(r["element_id"])]
        body = (r["payload"] or {}).get("body")
        if isinstance(body, str) and body != m["source"]:
            out.append((m, body))
    return out


def migrate(c: Connection) -> None:
    """Upgrade a database from before the SysML v2 API store: give every project a
    default branch and turn every saved model version into a commit (elements
    from the old ``models.element`` table), replayed in save order."""
    for p in c.execute("SELECT * FROM models.project WHERE default_branch_id IS NULL").fetchall():
        ensure_default_branch(c, p)
    has_old = c.execute("SELECT to_regclass('models.element') AS t").fetchone()["t"]
    pending = c.execute(
        """SELECT v.id, v.model_id, v.version, v.source, v.message, v.valid, v.created_at, m.project_id
           FROM models.model_version v JOIN models.model m ON m.id=v.model_id
           WHERE v.commit_id IS NULL ORDER BY m.project_id, v.created_at, v.id"""
    ).fetchall()
    models_: dict[Any, dict[str, Any]] = {}
    for v in pending:
        m = models_.get(v["model_id"]) or c.execute("SELECT * FROM models.model WHERE id=%s", (v["model_id"],)).fetchone()
        models_[v["model_id"]] = m
        els = [r["data"] for r in c.execute("SELECT data FROM models.element WHERE version_id=%s", (v["id"],))] if has_old else []
        cm = save_model(c, m, els if els or v["valid"] else None, v["source"], message=v["message"],
                        version=v["version"], created=v["created_at"])
        c.execute("UPDATE models.model_version SET commit_id=%s WHERE id=%s", (cm["id"], v["id"]))
