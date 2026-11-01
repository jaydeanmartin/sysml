"""Component library and system configuration, derived from the SysML v2 text.

Nothing here is stored outside the models. The conventions are plain SysML v2:

* a **component** is a ``part def`` tagged ``#Component`` (or specializing one);
* a **component instance** (catalog item) is a part usage declared directly in a
  package and typed by a component, redefining its attributes with values;
* a **system architecture** is a ``part def`` tagged ``#SystemArchitecture``
  (or specializing one); its part usages typed by part defs are *slots*;
* a **system instance** is a part usage declared in a package and typed by an
  architecture; each slot is redefined to subset a catalog instance::

      part economy : CombustionVehicle {
          part :>> engine :> ComponentLibrary::Catalog::i4Engine;
      }

The catalog is computed from one parse of every model of a project, so the IDs
and qualified names line up across models. Creating or editing an instance or a
system instance rewrites the declaring model's text and saves a new version.
"""

from __future__ import annotations

import re
from typing import Any

COMPONENT_TAG = "Component"
ARCHITECTURE_TAG = "SystemArchitecture"

NAME_RE = re.compile(r"^[A-Za-z_]\w*$")
TYPE_RE = re.compile(r"^~?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*$")
_BAD_EXPR = re.compile(r"[;{}\n\r]|/\*|\*/|//")


class CatalogError(ValueError):
    """A request that cannot be turned into valid model text (HTTP 422)."""


# ------------------------------------------------------------------ graph queries
class Index:
    def __init__(self, graph: dict[str, Any]):
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in graph["nodes"]}
        self.children: dict[str, list[dict[str, Any]]] = {}
        for n in graph["nodes"]:
            if n.get("owner"):
                self.children.setdefault(n["owner"], []).append(n)
        self.generals: dict[str, list[str]] = {}
        for e in graph["edges"]:
            if e["kind"] == "specializes" and e["source"] in self.nodes and e["target"] in self.nodes:
                self.generals.setdefault(e["source"], []).append(e["target"])
        self.by_qn = {n["qualifiedName"]: n for n in graph["nodes"] if n.get("qualifiedName")}

    def ancestors(self, def_id: str) -> list[str]:
        """The def and every def it specializes, most specific first."""
        out: list[str] = []
        todo = [def_id]
        while todo:
            cur = todo.pop(0)
            if cur in out:
                continue
            out.append(cur)
            todo += self.generals.get(cur, [])
        return out

    def tagged(self, def_id: str, tag: str) -> bool:
        return any(tag in (self.nodes[a].get("metadata") or []) for a in self.ancestors(def_id))

    def features(self, def_id: str, kinds: tuple[str, ...]) -> list[tuple[dict[str, Any], str]]:
        """Owned and inherited features of the given kinds, by name, the most
        specific declaration winning, inherited ones listed first.
        Returns (node, declaring def id)."""
        seen: dict[str, tuple[dict[str, Any], str]] = {}
        for a in reversed(self.ancestors(def_id)):
            for c in self.children.get(a, []):
                if c["kind"] in kinds:
                    seen[c["name"]] = (c, a)
        # a redefinition without a type keeps the type it redefines
        out = []
        for name, (n, owner) in seen.items():
            if not n.get("typeName"):
                for a in self.ancestors(def_id):
                    base = next((c for c in self.children.get(a, []) if c["name"] == name and c.get("typeName")), None)
                    if base:
                        n = {**n, "typeName": base["typeName"], "typeId": base.get("typeId")}
                        break
            out.append((n, owner))
        return out

    def in_package(self, n: dict[str, Any]) -> bool:
        owner = self.nodes.get(n.get("owner") or "")
        return bool(owner and owner["kind"] == "package")


def _qn(n: dict[str, Any] | None) -> str | None:
    return n and (n.get("qualifiedName") or n.get("name"))


def _attr(n: dict[str, Any], owner: str, idx: Index) -> dict[str, Any]:
    return {
        "name": n["name"],
        "type": n.get("typeName"),
        "value": n.get("value"),
        "declaredIn": _qn(idx.nodes.get(owner)),
    }


def build(graph: dict[str, Any], file_models: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Catalog of a project from its combined graph.

    ``file_models`` maps a source file name to ``{"id", "name"}`` of its model.
    """
    idx = Index(graph)

    def model_of(n: dict[str, Any]) -> dict[str, str] | None:
        return file_models.get((n.get("loc") or {}).get("file") or "")

    def located(n: dict[str, Any]) -> dict[str, Any]:
        m = model_of(n) or {}
        owner = idx.nodes.get(n.get("owner") or "")
        return {
            "id": n["id"],
            "name": n["name"],
            "qualifiedName": _qn(n),
            "package": _qn(owner),
            "modelId": m.get("id"),
            "modelName": m.get("name"),
            "doc": n.get("doc"),
            "loc": n.get("loc"),
        }

    part_defs = [n for n in graph["nodes"] if n["kind"] == "part def"]
    component_ids = {n["id"] for n in part_defs if idx.tagged(n["id"], COMPONENT_TAG)}
    arch_ids = {n["id"] for n in part_defs if idx.tagged(n["id"], ARCHITECTURE_TAG)} - component_ids

    def specializations(def_id: str) -> set[str]:
        return {d["id"] for d in part_defs if def_id in idx.ancestors(d["id"])}

    # ---------------------------------------------------------- instances
    instances = []
    for n in graph["nodes"]:
        if n["kind"] != "part" or n.get("typeId") not in component_ids or not idx.in_package(n):
            continue
        comp = idx.nodes[n["typeId"]]
        values = {a["name"]: a["value"] for a, _ in idx.features(comp["id"], ("attribute",)) if a.get("value")}
        own = {c["name"]: c["value"] for c in idx.children.get(n["id"], []) if c["kind"] == "attribute" and c.get("value")}
        instances.append(
            {**located(n), "componentId": comp["id"], "component": _qn(comp), "values": {**values, **own}, "ownValues": own}
        )
    inst_by_id = {i["id"]: i for i in instances}

    # ---------------------------------------------------------- components
    components = []
    for n in part_defs:
        if n["id"] not in component_ids:
            continue
        components.append(
            {
                **located(n),
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "attributes": [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))],
                "ports": [
                    {"name": p["name"], "type": p.get("typeName"), "declaredIn": _qn(idx.nodes.get(o))}
                    for p, o in idx.features(n["id"], ("port",))
                ],
                "parts": [
                    {"name": p["name"], "type": p.get("typeName"), "multiplicity": p.get("multiplicity")}
                    for p, _ in idx.features(n["id"], ("part",))
                ],
                "instances": [i["id"] for i in instances if i["componentId"] == n["id"]],
            }
        )

    # ---------------------------------------------------------- requirement defs and their subject type
    requirements = []
    for n in graph["nodes"]:
        if n["kind"] != "requirement def":
            continue
        subject = next((c for c in idx.children.get(n["id"], []) if c["kind"] == "subject"), None)
        if subject and subject.get("typeId"):
            requirements.append({**located(n), "subject": subject["name"], "subjectTypeId": subject["typeId"]})

    # ---------------------------------------------------------- architectures
    architectures = []
    for n in part_defs:
        if n["id"] not in arch_ids:
            continue
        slots = []
        for p, owner in idx.features(n["id"], ("part",)):
            tid = p.get("typeId")
            if not tid or idx.nodes.get(tid, {}).get("kind") != "part def":
                continue
            accepted = specializations(tid)
            slots.append(
                {
                    "name": p["name"],
                    "type": _qn(idx.nodes[tid]),
                    "typeId": tid,
                    "multiplicity": p.get("multiplicity"),
                    "doc": p.get("doc"),
                    "declaredIn": _qn(idx.nodes.get(owner)),
                    "candidates": [i["id"] for i in instances if i["componentId"] in accepted],
                }
            )
        attributes = [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))]
        architectures.append(
            {
                **located(n),
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "slots": slots,
                "attributes": attributes,
                "connections": sum(1 for e in graph["edges"] if e.get("context") in idx.ancestors(n["id"])),
                # requirement defs whose subject this architecture can be: Req(system) evaluates them
                "requirements": [r["qualifiedName"] for r in requirements if r["subjectTypeId"] in idx.ancestors(n["id"])],
            }
        )
    arch_by_id = {a["id"]: a for a in architectures}

    # ---------------------------------------------------------- system instances
    configurations = []
    for n in graph["nodes"]:
        if n["kind"] != "part" or n.get("typeId") not in arch_ids or not idx.in_package(n):
            continue
        arch = arch_by_id[n["typeId"]]
        slot_names = {s["name"] for s in arch["slots"]}
        assignments: dict[str, str | None] = {}
        for c in idx.children.get(n["id"], []):
            if c["kind"] != "part" or c["name"] not in slot_names:
                continue
            inst = inst_by_id.get(c.get("subsets") or "")
            if not inst and c.get("value"):  # binding form: part :>> engine = Catalog::i4;
                inst = next((i for i in instances if i["qualifiedName"].endswith(c["value"])), None)
            assignments[c["name"]] = inst["qualifiedName"] if inst else None
        configurations.append({**located(n), "architectureId": arch["id"], "architecture": arch["qualifiedName"], "assignments": assignments})

    packages = []
    for n in graph["nodes"]:
        if n["kind"] == "package":
            m = model_of(n) or {}
            packages.append({"qualifiedName": _qn(n), "modelId": m.get("id"), "modelName": m.get("name"), "loc": n.get("loc")})
    tags = {
        n["name"]: _qn(n)
        for n in graph["nodes"]
        if n["kind"] == "metadata def" and n["name"] in (COMPONENT_TAG, ARCHITECTURE_TAG)
    }
    return {
        "components": components,
        "instances": instances,
        "architectures": architectures,
        "configurations": configurations,
        "requirements": requirements,
        "packages": packages,
        "tags": tags,
    }


# ------------------------------------------------------------------ text generation
def ref(target_qn: str, scope_qn: str) -> str:
    """How ``target_qn`` is written inside package ``scope_qn``: the simple name
    when lexical scoping already finds it, else the qualified name."""
    parent, _, name = target_qn.rpartition("::")
    if parent and (scope_qn == parent or scope_qn.startswith(parent + "::")):
        return name
    return target_qn


def check_name(name: str, what: str = "name") -> None:
    if not NAME_RE.match(name or ""):
        raise CatalogError(f"{what} '{name}' is not a SysML name (letters, digits, _; not starting with a digit)")


def check_expr(value: str, what: str) -> None:
    if _BAD_EXPR.search(value):
        raise CatalogError(f"{what}: a value is one expression, without ';', braces or comments")


def check_doc(doc: str) -> None:
    if "*/" in doc:
        raise CatalogError("doc must not contain '*/'")


def _body(lines: list[str]) -> list[str]:
    return [f"    {line}" for line in lines]


def instance_text(name: str, component_ref: str, values: dict[str, str], doc: str = "") -> list[str]:
    check_name(name)
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    for attr, value in values.items():
        check_name(attr, "attribute")
        value = (value or "").strip()
        if not value:
            continue
        check_expr(value, attr)
        inner.append(f"attribute :>> {attr} = {value};")
    if not inner:
        return [f"part {name} : {component_ref};"]
    return [f"part {name} : {component_ref} {{", *_body(inner), "}"]


def configuration_text(name: str, arch_ref: str, assignments: dict[str, str], doc: str = "") -> list[str]:
    check_name(name)
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    for slot, inst_ref in assignments.items():
        check_name(slot, "slot")
        if inst_ref:
            inner.append(f"part :>> {slot} :> {inst_ref};")
    if not inner:
        return [f"part {name} : {arch_ref};"]
    return [f"part {name} : {arch_ref} {{", *_body(inner), "}"]


def component_text(
    name: str,
    tag_ref: str,
    specializes: str | None,
    attributes: list[dict[str, str]],
    ports: list[dict[str, str]],
    doc: str = "",
    abstract: bool = False,
) -> list[str]:
    check_name(name)
    head = f"{'abstract ' if abstract else ''}#{tag_ref} part def {name}"
    if specializes:
        if not TYPE_RE.match(specializes):
            raise CatalogError(f"'{specializes}' is not a qualified name")
        head += f" :> {specializes}"
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    for kind, feats in (("attribute", attributes), ("port", ports)):
        for f in feats:
            fname, ftype = (f.get("name") or "").strip(), (f.get("type") or "").strip()
            if not fname:
                continue
            check_name(fname, kind)
            if ftype and not TYPE_RE.match(ftype.replace("~ ", "~")):
                raise CatalogError(f"{kind} {fname}: '{ftype}' is not a type name")
            inner.append(f"{kind} {fname}{' : ' + ftype if ftype else ''};")
    return [f"{head} {{", *_body(inner), "}"]


# ------------------------------------------------------------------ text editing
def _offset(lines: list[str], line: int, column: int) -> int:
    return sum(len(l) for l in lines[: line - 1]) + column - 1


def insert_member(source: str, package_loc: dict[str, Any], block: list[str]) -> str:
    """Insert ``block`` as the last member of the package at ``package_loc``."""
    lines = source.splitlines(keepends=True)
    brace = _offset(lines, package_loc["endLine"], package_loc["endColumn"] - 1)
    if source[brace : brace + 1] != "}":
        raise CatalogError("the package's closing brace was not found where the interpreter reported it")
    indent = " " * (package_loc["column"] - 1)
    before = source[:brace].rstrip(" \t")
    if not before.endswith("\n"):
        before += "\n"
    text = "".join(f"{indent}    {l}\n" for l in block)
    return before + text + indent + source[brace:]


def replace_element(source: str, loc: dict[str, Any], block: list[str]) -> str:
    lines = source.splitlines(keepends=True)
    start = _offset(lines, loc["line"], loc["column"])
    end = _offset(lines, loc["endLine"], loc["endColumn"])
    indent = " " * (loc["column"] - 1)
    text = "\n".join([block[0], *(indent + l for l in block[1:])])
    return source[:start] + text + source[end:]


def remove_element(source: str, loc: dict[str, Any]) -> str:
    """Remove an element; whole lines go when nothing else is on them."""
    lines = source.splitlines(keepends=True)
    start = _offset(lines, loc["line"], loc["column"])
    end = _offset(lines, loc["endLine"], loc["endColumn"])
    line_start = source.rfind("\n", 0, start) + 1
    nl = source.find("\n", end)
    line_end = len(source) if nl < 0 else nl + 1
    if not source[line_start:start].strip() and not source[end:line_end].strip():
        start, end = line_start, line_end
    return source[:start] + source[end:]
