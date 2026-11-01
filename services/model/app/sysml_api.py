"""SysML v2 API (OMG Systems Modeling API and Services 1.0, REST/HTTP PSM).

Served under ``/sysml-v2``: point any SysML v2 API client at
``http://<host>:8001/sysml-v2`` (or ``/api/sysml-v2`` through the gateway).
Collections are paged with ``page[size]``, ``page[after]`` and ``page[before]``;
the next and previous pages are announced in a ``Link`` header. Errors are
``{"@type": "Error", "description": ...}``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from . import db, repo
from .repo import RepoError

PREFIX = "/sysml-v2"
DEFAULT_PAGE_SIZE = int(os.environ.get("SYSML_API_PAGE_SIZE", "100"))
MAX_PAGE_SIZE = 10000

router = APIRouter(prefix=PREFIX, tags=["SysML v2 API"])

# Called after a commit made through this API: (project id, commit row). The model
# service uses it to record a Studio version when a commit changes a model's text.
on_commit: Callable[[Any, dict[str, Any]], None] = lambda project_id, commit: None


def error(status: int, description: str, body: Any = None) -> JSONResponse:
    return JSONResponse(body if body is not None else {"@type": "Error", "description": description}, status_code=status)


def install(app: Any) -> None:
    app.include_router(router)

    @app.exception_handler(RepoError)
    def _repo_error(request: Request, exc: RepoError) -> JSONResponse:
        if not request.url.path.startswith(PREFIX):  # the Studio's own endpoints
            return JSONResponse({"detail": exc.description}, status_code=exc.status)
        return error(exc.status, exc.description, exc.body)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith(PREFIX):
            return error(400, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
        return await request_validation_exception_handler(request, exc)


# ------------------------------------------------------------------ paging
class Page:
    def __init__(self, request: Request):
        q = request.query_params
        try:
            self.size = min(int(q.get("page[size]") or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)
        except ValueError as exc:
            raise RepoError(400, "page[size] must be an integer") from exc
        if self.size < 1:
            raise RepoError(400, "page[size] must be positive")
        self.after, self.before, self.url = q.get("page[after]"), q.get("page[before]"), _public_url(request)

    def link(self, response: Response, first: str | None, last: str | None, more_before: bool, more_after: bool) -> None:
        base = self.url.remove_query_params(["page[after]", "page[before]"]).include_query_params(**{"page[size]": self.size})
        links = []
        if more_after and last:
            links.append(f'<{base.include_query_params(**{"page[after]": last})}>; rel="next"')
        if more_before and first:
            links.append(f'<{base.include_query_params(**{"page[before]": first})}>; rel="prev"')
        if links:
            response.headers["Link"] = ", ".join(links)

    def slice(self, response: Response, items: list[dict[str, Any]], key: str = "@id") -> list[dict[str, Any]]:
        """A page of an ordered, in-memory collection; cursors are the items' ids."""
        keys = [str(i.get(key)) for i in items]
        start = 0
        if self.after:
            start = keys.index(self.after) + 1 if self.after in keys else len(keys)
        elif self.before:
            end = keys.index(self.before) if self.before in keys else 0
            start = max(0, end - self.size)
        out = items[start:start + self.size] if not self.before else items[start:end]
        self.link(response, out and keys[start], out and keys[start + len(out) - 1], start > 0, start + len(out) < len(items))
        return out

    def keyset(self, response: Response, c: Any, sql: str, params: list[Any], col: str) -> list[dict[str, Any]]:
        """A page of ``sql`` (rows with ``key`` and ``data``) ordered by the UUID column ``col``."""
        cursor = self.after or self.before
        if cursor and not repo.is_uuid(cursor):
            raise RepoError(400, "page cursors are ids")
        if self.before:
            rows = c.execute(f"{sql} AND {col} < %s ORDER BY {col} DESC LIMIT %s", [*params, cursor, self.size + 1]).fetchall()
            more_before, rows = len(rows) > self.size, rows[:self.size][::-1]
            more_after = True
        else:
            extra = f" AND {col} > %s" if self.after else ""
            rows = c.execute(f"{sql}{extra} ORDER BY {col} LIMIT %s", [*params, *([cursor] if self.after else []), self.size + 1]).fetchall()
            more_after, rows = len(rows) > self.size, rows[:self.size]
            more_before = bool(self.after)
        self.link(response, rows and str(rows[0]["key"]), rows and str(rows[-1]["key"]), more_before, more_after)
        return [r["data"] for r in rows]


def _public_url(request: Request) -> Any:
    """The request URL as the client sees it behind the gateway (X-Forwarded-* headers)."""
    url, h = request.url, request.headers
    if h.get("x-forwarded-host"):
        url = url.replace(netloc=h["x-forwarded-host"].split(",")[0].strip())
    if h.get("x-forwarded-proto"):
        url = url.replace(scheme=h["x-forwarded-proto"].split(",")[0].strip())
    if h.get("x-forwarded-prefix"):
        url = url.replace(path=h["x-forwarded-prefix"].rstrip("/") + url.path)
    return url


def _identified(v: Any, what: str) -> str | None:
    if v is None:
        return None
    if not isinstance(v, dict) or not repo.is_uuid(v.get("@id")):
        raise RepoError(400, f"'{what}' must be {{\"@id\": <uuid>}}")
    return str(v["@id"])


def _obj(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RepoError(400, "the request body must be a JSON object")
    return body


def _alias(body: dict[str, Any]) -> list[str]:
    a = body.get("alias") or []
    if not isinstance(a, list) or not all(isinstance(x, str) for x in a):
        raise RepoError(400, "'alias' must be a list of strings")
    return a


def _commit_or_head(c: Any, project_id: str, commit_id: str | None) -> Any:
    repo.project(c, project_id)
    return repo.commit_row(c, project_id, commit_id)["id"] if commit_id else repo.head(c, project_id)


# ------------------------------------------------------------------ projects
@router.get("/projects")
def get_projects(request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        rows = c.execute("SELECT * FROM models.project ORDER BY created_at, id").fetchall()
    return Page(request).slice(response, [repo.project_json(r) for r in rows])


@router.post("/projects", status_code=201)
def post_project(body: Any = Body(None)) -> dict[str, Any]:
    body = _obj(body)
    if not isinstance(body.get("name"), str) or not body["name"].strip():
        raise RepoError(400, "'name' is required")
    with db.pool.connection() as c, c.transaction():
        return repo.project_json(repo.create_project(c, body["name"], body.get("description"), _alias(body)))


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        return repo.project_json(repo.project(c, project_id))


@router.put("/projects/{project_id}")
def put_project(project_id: str, body: Any = Body(None)) -> dict[str, Any]:
    body = _obj(body)
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        name = body.get("name", p["name"])
        if not isinstance(name, str) or not name.strip():
            raise RepoError(400, "'name' must be a non-empty string")
        if name != p["name"] and c.execute("SELECT 1 FROM models.project WHERE name=%s", (name,)).fetchone():
            raise RepoError(409, f"project '{name}' already exists")
        branch_id = _identified(body.get("defaultBranch"), "defaultBranch") or p["default_branch_id"]
        repo.branch(c, p["id"], branch_id)
        r = c.execute(
            "UPDATE models.project SET name=%s, description=%s, alias=%s, default_branch_id=%s WHERE id=%s RETURNING *",
            (name, body.get("description", p["description"]) or "", _alias(body) if "alias" in body else p["alias"], branch_id, p["id"]),
        ).fetchone()
    return repo.project_json(r)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        c.execute("DELETE FROM models.project WHERE id=%s", (p["id"],))
    return repo.project_json(p)


# ------------------------------------------------------------------ branches and tags
@router.get("/projects/{project_id}/branches")
def get_branches(project_id: str, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        repo.project(c, project_id)
        rows = c.execute("SELECT * FROM models.branches WHERE project_id=%s ORDER BY created_at, id", (project_id,)).fetchall()
    return Page(request).slice(response, [repo.branch_json(r) for r in rows])


@router.post("/projects/{project_id}/branches", status_code=201)
def post_branch(project_id: str, body: Any = Body(None)) -> dict[str, Any]:
    body = _obj(body)
    if not isinstance(body.get("name"), str) or not body["name"]:
        raise RepoError(400, "'name' is required")
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        head = _identified(body.get("head"), "head")
        if head:
            repo.commit_row(c, p["id"], head)
        r = c.execute(
            """INSERT INTO models.branches (project_id, name, description, alias, head_commit_id) VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (project_id, name) DO NOTHING RETURNING *""",
            (p["id"], body["name"], body.get("description"), _alias(body), head),
        ).fetchone()
    if not r:
        raise RepoError(409, f"branch '{body['name']}' already exists")
    return repo.branch_json(r)


@router.get("/projects/{project_id}/branches/{branch_id}")
def get_branch(project_id: str, branch_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        return repo.branch_json(repo.branch(c, repo.project(c, project_id)["id"], branch_id))


@router.delete("/projects/{project_id}/branches/{branch_id}")
def delete_branch(project_id: str, branch_id: str) -> dict[str, Any]:
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        b = repo.branch(c, p["id"], branch_id, lock=True)
        if b["id"] == p["default_branch_id"]:
            raise RepoError(409, "the default branch of a project cannot be deleted")
        now = c.execute("DELETE FROM models.branches WHERE id=%s RETURNING now() AS t", (b["id"],)).fetchone()["t"]
    return repo.branch_json(b, deleted=now)


@router.get("/projects/{project_id}/tags")
def get_tags(project_id: str, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        repo.project(c, project_id)
        rows = c.execute("SELECT * FROM models.tags WHERE project_id=%s ORDER BY created_at, id", (project_id,)).fetchall()
    return Page(request).slice(response, [repo.tag_json(r) for r in rows])


@router.post("/projects/{project_id}/tags", status_code=201)
def post_tag(project_id: str, body: Any = Body(None)) -> dict[str, Any]:
    body = _obj(body)
    if not isinstance(body.get("name"), str) or not body["name"]:
        raise RepoError(400, "'name' is required")
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        tagged = _identified(body.get("taggedCommit"), "taggedCommit")
        if not tagged:
            raise RepoError(400, "'taggedCommit' is required")
        repo.commit_row(c, p["id"], tagged)
        r = c.execute(
            """INSERT INTO models.tags (project_id, name, description, alias, tagged_commit_id) VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (project_id, name) DO NOTHING RETURNING *""",
            (p["id"], body["name"], body.get("description"), _alias(body), tagged),
        ).fetchone()
    if not r:
        raise RepoError(409, f"tag '{body['name']}' already exists")
    return repo.tag_json(r)


def _tag(c: Any, project_id: str, tag_id: str) -> dict[str, Any]:
    p = repo.project(c, project_id)
    t = c.execute("SELECT * FROM models.tags WHERE id=%s AND project_id=%s", (tag_id, p["id"])).fetchone() if repo.is_uuid(tag_id) else None
    if not t:
        raise RepoError(404, "tag not found")
    return t


@router.get("/projects/{project_id}/tags/{tag_id}")
def get_tag(project_id: str, tag_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        return repo.tag_json(_tag(c, project_id, tag_id))


@router.delete("/projects/{project_id}/tags/{tag_id}")
def delete_tag(project_id: str, tag_id: str) -> dict[str, Any]:
    with db.pool.connection() as c, c.transaction():
        t = _tag(c, project_id, tag_id)
        now = c.execute("DELETE FROM models.tags WHERE id=%s RETURNING now() AS t", (t["id"],)).fetchone()["t"]
    return repo.tag_json(t, deleted=now)


# ------------------------------------------------------------------ commits
@router.get("/projects/{project_id}/commits")
def get_commits(project_id: str, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        repo.project(c, project_id)
        rows = c.execute("SELECT * FROM models.commits WHERE project_id=%s ORDER BY seq", (project_id,)).fetchall()
    return Page(request).slice(response, [repo.commit_json(r) for r in rows])


@router.post("/projects/{project_id}/commits", status_code=201)
def post_commit(project_id: str, body: Any = Body(None), branchId: str | None = None) -> dict[str, Any]:
    """Create a commit on a branch (default: the project's default branch) from
    DataVersions: a payload creates or replaces an element, a null payload deletes it."""
    body = _obj(body)
    changes = repo.parse_changes(body.get("change"))
    expected: Any = "any"
    if "previousCommit" in body:  # older clients name the commit they build on
        prev = body["previousCommit"]
        prev = prev[0] if isinstance(prev, list) and prev else prev
        expected = _identified(prev or None, "previousCommit")
    with db.pool.connection() as c, c.transaction():
        p = repo.project(c, project_id)
        cm = repo.commit(
            c, p["id"], branchId or p["default_branch_id"], changes, name=body.get("name"),
            description=body.get("description"), alias=_alias(body), expected_head=expected,
        )
    on_commit(p["id"], cm)
    return repo.commit_json(cm)


@router.get("/projects/{project_id}/commits/{commit_id}")
def get_commit(project_id: str, commit_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        return repo.commit_json(repo.commit_row(c, repo.project(c, project_id)["id"], commit_id))


@router.get("/projects/{project_id}/commits/{commit_id}/changes")
def get_changes(project_id: str, commit_id: str, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        cm = repo.commit_row(c, repo.project(c, project_id)["id"], commit_id)
        rows = Page(request).keyset(
            response, c, "SELECT element_id AS key, to_jsonb(dv) AS data FROM models.data_versions dv WHERE commit_id=%s",
            [cm["id"]], "element_id",
        )
    return [repo.version_json(r) for r in rows]


@router.get("/projects/{project_id}/commits/{commit_id}/changes/{change_id}")
def get_change(project_id: str, commit_id: str, change_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        cm = repo.commit_row(c, repo.project(c, project_id)["id"], commit_id)
        r = c.execute(
            "SELECT * FROM models.data_versions WHERE commit_id=%s AND (id=%s OR element_id=%s)", (cm["id"], change_id, change_id)
        ).fetchone() if repo.is_uuid(change_id) else None
    if not r:
        raise RepoError(404, "change not found")
    return repo.version_json(r)


# ------------------------------------------------------------------ elements
_AT = "SELECT ce.element_id AS key, dv.payload AS data FROM models.commit_elements ce JOIN models.data_versions dv ON dv.id=ce.version_id WHERE ce.commit_id=%s"


@router.get("/projects/{project_id}/commits/{commit_id}/elements")
def get_elements(project_id: str, commit_id: str, request: Request, response: Response, excludeUsed: bool = False) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        cm = repo.commit_row(c, repo.project(c, project_id)["id"], commit_id)
        return Page(request).keyset(response, c, _AT, [cm["id"]], "ce.element_id")


def _element(c: Any, project_id: str, commit_id: str, element_id: str) -> dict[str, Any]:
    cm = repo.commit_row(c, repo.project(c, project_id)["id"], commit_id)
    r = c.execute(f"{_AT} AND ce.element_id=%s", (cm["id"], element_id)).fetchone() if repo.is_uuid(element_id) else None
    if not r:
        raise RepoError(404, "element not found")
    return r["data"]


@router.get("/projects/{project_id}/commits/{commit_id}/elements/{element_id}")
def get_element(project_id: str, commit_id: str, element_id: str, excludeUsed: bool = False) -> dict[str, Any]:
    with db.pool.connection() as c:
        return _element(c, project_id, commit_id, element_id)


@router.get("/projects/{project_id}/commits/{commit_id}/elements/{element_id}/projectUsage")
def get_project_usage(project_id: str, commit_id: str, element_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        _element(c, project_id, commit_id, element_id)
    raise RepoError(404, "the element belongs to this project, not to a used project")


@router.get("/projects/{project_id}/commits/{commit_id}/roots")
def get_roots(project_id: str, commit_id: str, request: Request, response: Response, excludeUsed: bool = False) -> list[dict[str, Any]]:
    """Elements without an owner (neither an owning membership nor, for a relationship, an owning element)."""
    with db.pool.connection() as c:
        cm = repo.commit_row(c, repo.project(c, project_id)["id"], commit_id)
        sql = (f"{_AT} AND coalesce(dv.payload->'owningRelationship', 'null'::jsonb) = 'null'::jsonb"
               f" AND coalesce(dv.payload->'owningRelatedElement', 'null'::jsonb) = 'null'::jsonb"
               f" AND NOT (dv.payload->>'@type' = ANY(%s))")
        return Page(request).keyset(response, c, sql, [cm["id"], list(repo.NOT_ELEMENTS)], "ce.element_id")


@router.get("/projects/{project_id}/commits/{commit_id}/elements/{element_id}/relationships")
def get_relationships(
    project_id: str, commit_id: str, element_id: str, request: Request, response: Response,
    direction: str = Query("both", pattern="^(in|out|both)$"), excludeUsed: bool = False,
) -> list[dict[str, Any]]:
    """Relationships whose source (out), target (in) or either (both) is the element."""
    with db.pool.connection() as c:
        _element(c, project_id, commit_id, element_id)
        cm = repo.commit_row(c, project_id, commit_id)
        props = {"out": ["source"], "in": ["target"], "both": ["source", "target"]}[direction]
        term = " OR ".join("dv.payload -> %s::text @> %s::jsonb" for _ in props)
        params: list[Any] = [cm["id"]]
        for p in props:
            params += [p, json.dumps([{"@id": element_id.lower()}])]
        return Page(request).keyset(response, c, f"{_AT} AND ({term})", params, "ce.element_id")


@router.get("/projects/{project_id}/commits/{compare_commit_id}/diff")
def get_diff(project_id: str, compare_commit_id: str, request: Request, response: Response, baseCommitId: str = Query(...)) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        pid = repo.project(c, project_id)["id"]
        base, comp = repo.commit_row(c, pid, baseCommitId), repo.commit_row(c, pid, compare_commit_id)
        items = repo.diff(c, base["id"], comp["id"])
    for d in items:
        d["@id"] = (d["compareData"] or d["baseData"])["identity"]["@id"]
    out = Page(request).slice(response, items)
    for d in out:
        d.pop("@id")
    return out


@router.post("/projects/{project_id}/branches/{target_branch_id}/merge", status_code=201)
def post_merge(
    project_id: str, target_branch_id: str, sourceCommitId: str = Query(..., description="comma separated"),
    description: str | None = None, body: Any = Body(None),
) -> dict[str, Any]:
    """Merge commits into a branch. Conflicting elements (changed on both sides) are
    returned with 409 unless the body gives the payloads to keep for them."""
    resolutions = body or []
    if not isinstance(resolutions, list):
        raise RepoError(400, "the body must be a list of resolved element payloads")
    sources = [s.strip() for s in sourceCommitId.split(",") if s.strip()]
    if not sources:
        raise RepoError(400, "sourceCommitId is required")
    with db.pool.connection() as c, c.transaction():
        pid = repo.project(c, project_id)["id"]
        cm = None
        for s in sources:
            cm = repo.merge(c, pid, target_branch_id, s, description, resolutions)
    on_commit(pid, cm)
    return repo.commit_json(cm)


# ------------------------------------------------------------------ queries
def _query_body(body: Any) -> dict[str, Any]:
    body = _obj(body)
    select = body.get("select") or []
    if not isinstance(select, list) or not all(isinstance(s, str) for s in select):
        raise RepoError(400, "'select' must be a list of property names")
    where = body.get("where")
    if where is not None:
        repo.where_sql(where)  # validate
    return {"select": select, "where": where}


def _query_row(c: Any, project_id: str, query_id: str) -> dict[str, Any]:
    p = repo.project(c, project_id)
    r = c.execute("SELECT * FROM models.queries WHERE id=%s AND project_id=%s", (query_id, p["id"])).fetchone() if repo.is_uuid(query_id) else None
    if not r:
        raise RepoError(404, "query not found")
    return r


@router.get("/projects/{project_id}/queries")
def get_queries(project_id: str, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        repo.project(c, project_id)
        rows = c.execute("SELECT * FROM models.queries WHERE project_id=%s ORDER BY created_at, id", (project_id,)).fetchall()
    return Page(request).slice(response, [repo.query_json(r) for r in rows])


@router.post("/projects/{project_id}/queries", status_code=201)
def post_query(project_id: str, body: Any = Body(None)) -> dict[str, Any]:
    q = _query_body(body)
    with db.pool.connection() as c:
        p = repo.project(c, project_id)
        r = c.execute(
            "INSERT INTO models.queries (project_id, name, description, alias, query) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (p["id"], body.get("name") or "", body.get("description"), _alias(body), Jsonb(q)),
        ).fetchone()
    return repo.query_json(r)


@router.get("/projects/{project_id}/queries/{query_id}")
def get_query(project_id: str, query_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        return repo.query_json(_query_row(c, project_id, query_id))


@router.put("/projects/{project_id}/queries/{query_id}")
def put_query(project_id: str, query_id: str, body: Any = Body(None)) -> dict[str, Any]:
    q = _query_body(body)
    with db.pool.connection() as c:
        old = _query_row(c, project_id, query_id)
        r = c.execute(
            "UPDATE models.queries SET name=%s, description=%s, alias=%s, query=%s WHERE id=%s RETURNING *",
            (body.get("name", old["name"]) or "", body.get("description", old["description"]),
             _alias(body) if "alias" in body else old["alias"], Jsonb(q), old["id"]),
        ).fetchone()
    return repo.query_json(r)


@router.delete("/projects/{project_id}/queries/{query_id}")
def delete_query(project_id: str, query_id: str) -> dict[str, Any]:
    with db.pool.connection() as c:
        r = _query_row(c, project_id, query_id)
        c.execute("DELETE FROM models.queries WHERE id=%s", (r["id"],))
    return repo.query_json(r)


def _results(project_id: str, q: dict[str, Any], commit_id: str | None, request: Request, response: Response) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        cid = _commit_or_head(c, project_id, commit_id)
        rows = repo.query(c, cid, q["where"], q["select"])
    return Page(request).slice(response, rows)


@router.get("/projects/{project_id}/queries/{query_id}/results")
def get_query_results(project_id: str, query_id: str, request: Request, response: Response, commitId: str | None = None) -> list[dict[str, Any]]:
    with db.pool.connection() as c:
        q = _query_row(c, project_id, query_id)["query"]
    return _results(project_id, q, commitId, request, response)


@router.api_route("/projects/{project_id}/query-results", methods=["GET", "POST"])
def query_results(project_id: str, request: Request, response: Response, commitId: str | None = None, body: Any = Body(None)) -> list[dict[str, Any]]:
    """Run a Query given in the request body (at ``commitId``, default the head of the default branch)."""
    return _results(project_id, _query_body(body or {}), commitId, request, response)
