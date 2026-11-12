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

* a **behavior** is an (abstract) ``action def`` tagged ``#Behavior`` (or
  specializing one). Its ``in`` parameters are *performance parameters*, set by
  the system that performs it; its attributes without a value are *quantities*,
  set by each mission step; the rest are derived. Behaviors are listed with the
  components, with ``kind: "action"``;
* a **behavior instance** is an action usage declared in a package and typed by
  a behavior; it is *complete* when every parameter has a value::

      action cruise12 : Cruise { in :>> speed = 12; in :>> powerFactor = 1.1; }

* an architecture's ``perform action`` usages typed by a behavior are its
  *behavior slots*; a system instance fills each with a complete behavior
  instance (``perform action :>> cruise :> Behaviors::cruise12;``);
* a **mission type** is an ``action def`` tagged ``#Mission``; its attributes
  are *totals*, each the sum of the same-named attribute over the steps;
* a **mission** is an action usage declared in a package and typed by a mission
  type; its steps are action usages, in order, typed by a behavior and setting
  its quantities::

      action shortHop : DeliveryMission {
          first start;
          then action takeoff : Takeoff { attribute :>> altitude = 30; }
          then action cruise : Cruise { attribute :>> distance = 2000; }
          then done;
      }

  A system instance flies a step with the behavior it performs for the step's
  type (see ``flown_step``);
* a **mission requirement** is a requirement def with ``in`` parameters: it is
  checked with a system instance as subject and the totals of the mission as
  flown by that system as arguments;
* a **mission simulation** is a def bound to an external analysis
  (``ToolExecution`` metadata) with a subject: its ``in`` parameters with a
  value are evaluated with a system instance as subject and sent to the
  analysis, together with the flown steps of a mission. Its ``out`` parameters
  name the results it relies on; as a verification def, the requirements its
  objective verifies are checked with those results (by parameter name).

The catalog is computed from one parse of every model of a project, so the IDs
and qualified names line up across models. Creating or editing an instance or a
system instance rewrites the declaring model's text and saves a new version.
"""

from __future__ import annotations

import re
from typing import Any

COMPONENT_TAG = "Component"
ARCHITECTURE_TAG = "SystemArchitecture"
BEHAVIOR_TAG = "Behavior"
MISSION_TAG = "Mission"

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


def number_ids(usages: list[dict[str, Any]], parent: str | None = None) -> None:
    """Ids of nested requirements without a short name of their own: the parent's
    id and their position, SYS-07 -> SYS-07.1, SYS-07.2."""
    for i, u in enumerate(usages, 1):
        if parent and not u["shortId"]:
            u["shortId"] = f"{parent}.{i}"
        number_ids(u["children"], u["shortId"] or u["defShortId"])


def declared_ids(usages: list[dict[str, Any]]) -> dict[str, str]:
    """``path -> short name`` of the usages declared with one."""
    return {u["path"]: u["declaredShortId"] for u in walk_usages(usages) if u.get("declaredShortId")}


def walk_usages(usages: list[dict[str, Any]]):
    """Every requirement usage, nested ones included, parents first."""
    for u in usages:
        yield u
        yield from walk_usages(u["children"])


def missing_values(usages: list[dict[str, Any]]) -> list[str]:
    """``path.attribute`` of every required value still missing."""
    return [f"{u['path']}.{m}" for u in walk_usages(usages) for m in u["missing"]]


def own_requirement_values(usages: list[dict[str, Any]]) -> dict[str, str]:
    """An instance's own requirement values, as ``path.attribute -> value``."""
    return {f"{u['path']}.{k}": v for u in walk_usages(usages) for k, v in u["ownValues"].items()}


def apply_values(usages: list[dict[str, Any]], given: dict[str, str]) -> list[dict[str, Any]]:
    """Requirement usages of a def as an unsaved instance setting ``given``
    (``path.attribute -> value``) would have them."""
    out = []
    for u in usages:
        own = {k.rsplit(".", 1)[1]: v for k, v in given.items() if k.rsplit(".", 1)[0] == u["path"] and (v or "").strip()}
        values = {**u["values"], **own}
        out.append(
            {
                **u, "values": values, "ownValues": own, "bound": sorted(set(u["bound"]) | set(own)),
                "thresholds": [{**t, "value": values.get(t["name"])} for t in u["thresholds"]],
                "missing": [m for m in u["missing"] if m not in own],
                "children": apply_values(u["children"], given),
            }
        )
    return out


def check_def_text(name: str, def_qn: str, chain: str, bound: list[str]) -> str:
    """A requirement def that checks one requirement usage: it specializes the
    usage's def and binds the attributes the usage (or its instance) sets to
    their values, reached through ``chain`` (``Configurations::parcel.endurance``).
    It is then called with the subject (and any in parameters): ``R(parcel)``."""
    binds = " ".join(f"attribute :>> {a} = {chain}.{a};" for a in bound)
    return f"requirement def {name} :> {def_qn} {{ {binds} }}" if binds else f"requirement def {name} :> {def_qn};"


def verifying_tests(tests: list[dict[str, Any]], lineage: list[str], declarations: list[str], context_ids: list[str]) -> list[dict[str, Any]]:
    """Tests that verify a requirement: those naming one of its declarations (a
    requirement usage), or its def or a def it specializes, when the test's
    subject type is the context's type or one it specializes (or it has none)."""
    out = []
    for t in tests:
        v = set(t["verifies"])
        if v & set(declarations) or (v & set(lineage) and (not t["subjectTypeId"] or t["subjectTypeId"] in context_ids)):
            out.append(t)
    return out


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
    action_defs = [n for n in graph["nodes"] if n["kind"] == "action def"]
    component_ids = {n["id"] for n in part_defs if idx.tagged(n["id"], COMPONENT_TAG)}
    arch_ids = {n["id"] for n in part_defs if idx.tagged(n["id"], ARCHITECTURE_TAG)} - component_ids
    behavior_ids = {n["id"] for n in action_defs if idx.tagged(n["id"], BEHAVIOR_TAG)}
    mission_ids = {n["id"] for n in action_defs if idx.tagged(n["id"], MISSION_TAG)} - behavior_ids
    # components and behaviors share the library: instances of either are usages in a package
    library_ids = component_ids | behavior_ids

    def specializations(def_id: str) -> set[str]:
        return {d["id"] for d in part_defs + action_defs if def_id in idx.ancestors(d["id"])}

    def params(def_id: str) -> list[tuple[dict[str, Any], str]]:
        return [(p, o) for p, o in idx.features(def_id, ("param",)) if p.get("direction") in ("in", None)]

    # ---------------------------------------------------------- requirement usages (see requirement_usages)
    req_def_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "requirement def"}

    def req_constraints(def_id: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for a in reversed(idx.ancestors(def_id)):
            out += [c for c in idx.nodes[a].get("constraints") or [] if c not in out]
        return out

    def usage(name: str, nodes: list[dict[str, Any]], own: dict[str, Any] | None, path: str) -> dict[str, Any]:
        """One requirement usage. ``nodes`` declare or redefine it, most general
        first; ``own`` is the instance's redefinition, if any."""
        type_id = next((n["typeId"] for n in reversed(nodes) if n.get("typeId") in req_def_ids), None)
        values: dict[str, str] = {}
        set_by: dict[str, str | None] = {}
        thresholds = []
        if type_id:
            for a, o in idx.features(type_id, ("attribute",)):
                thresholds.append({"name": a["name"], "type": a.get("typeName"), "required": not a.get("value")})
                if a.get("value"):
                    values[a["name"]], set_by[a["name"]] = a["value"], _qn(idx.nodes.get(o))
        own_values: dict[str, str] = {}
        for n in nodes:
            for c in idx.children.get(n["id"], []):
                if c["kind"] == "attribute" and c.get("value"):
                    values[c["name"]], set_by[c["name"]] = c["value"], _qn(idx.nodes.get(n.get("owner") or ""))
                    if n is own:
                        own_values[c["name"]] = c["value"]
        # nested requirements: declared in the requirement def, or redefined along the way
        kids: dict[str, list[dict[str, Any]]] = {}
        if type_id:
            for c, _ in idx.features(type_id, ("requirement",)):
                kids.setdefault(c["name"], []).append(c)
        own_kids: dict[str, dict[str, Any]] = {}
        for n in nodes:
            for c in idx.children.get(n["id"], []):
                if c["kind"] == "requirement":
                    kids.setdefault(c["name"], []).append(c)
                    if n is own:
                        own_kids[c["name"]] = c
        rdef = idx.nodes.get(type_id or "")
        constraints = req_constraints(type_id) if type_id else []
        for n in nodes:
            constraints += [c for c in n.get("constraints") or [] if c not in constraints]
        return {
            "name": name,
            "path": path,
            "def": _qn(rdef),
            "defName": rdef and rdef["name"],
            # its id: the short name of its most specific declaration (<'DEL-1'>), and its def's
            "shortId": next((n.get("shortName") for n in reversed(nodes) if n.get("shortName")), None),
            # the short name its declaration gives it: a redefinition restates it (see requirements_text)
            "declaredShortId": next((n.get("shortName") for n in reversed(nodes) if n is not own and n.get("shortName")), None),
            "defShortId": (rdef or {}).get("shortName"),
            "defLineage": [_qn(idx.nodes[a]) for a in idx.ancestors(type_id)] if type_id else [],
            "doc": next((n.get("doc") for n in reversed(nodes) if n.get("doc")), None) or (rdef or {}).get("doc"),
            "constraints": constraints,
            "subject": bool(type_id and any(c["kind"] == "subject" for a in idx.ancestors(type_id) for c in idx.children.get(a, []))),
            "params": [p["name"] for p, _ in idx.features(type_id, ("param",)) if p.get("direction") == "in"] if type_id else [],
            "thresholds": [{**t, "value": values.get(t["name"]), "setBy": set_by.get(t["name"])} for t in thresholds],
            "values": values,
            "ownValues": own_values,
            # what a usage sets along the way (not the def's own defaults): bound when it is checked
            "bound": sorted({c["name"] for n in nodes for c in idx.children.get(n["id"], []) if c["kind"] == "attribute" and c.get("value")}),
            "missing": [t["name"] for t in thresholds if t["required"] and t["name"] not in values],
            "declarations": [_qn(n) for n in nodes if n is not own],
            "declaredIn": _qn(idx.nodes.get(nodes[0].get("owner") or "")),
            "loc": (own or nodes[-1]).get("loc"),
            "modelId": (model_of(own or nodes[-1]) or {}).get("id"),
            "children": [usage(k, ns, own_kids.get(k), f"{path}.{k}") for k, ns in kids.items()],
        }

    def requirement_usages(def_id: str, inst: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Requirement usages of a def (and its ancestors), as an instance of it
        (``inst``) redefines them: each with its requirement def, the values of
        the def's attributes (thresholds) and those still missing, and its nested
        requirements."""
        layers = [idx.nodes[a] for a in reversed(idx.ancestors(def_id))] + ([inst] if inst else [])
        by_name: dict[str, list[dict[str, Any]]] = {}
        own: dict[str, dict[str, Any]] = {}
        for layer in layers:
            for c in idx.children.get(layer["id"], []):
                if c["kind"] == "requirement":
                    by_name.setdefault(c["name"], []).append(c)
                    if layer is inst:
                        own[c["name"]] = c
        usages = [usage(k, ns, own.get(k), k) for k, ns in by_name.items() if any(n.get("typeId") in req_def_ids for n in ns)]
        number_ids(usages)
        return usages

    # ---------------------------------------------------------- instances
    instances = []
    for n in graph["nodes"]:
        if n["kind"] not in ("part", "action") or n.get("typeId") not in library_ids or not idx.in_package(n):
            continue
        comp = idx.nodes[n["typeId"]]
        feats = idx.features(comp["id"], ("attribute",)) + params(comp["id"])
        values = {a["name"]: a["value"] for a, _ in feats if a.get("value")}
        own = {c["name"]: c["value"] for c in idx.children.get(n["id"], []) if c["kind"] in ("attribute", "param") and c.get("value")}
        merged = {**values, **own}
        reqs = requirement_usages(comp["id"], n)
        instances.append(
            {
                **located(n), "kind": n["kind"], "componentId": comp["id"], "component": _qn(comp),
                "values": merged, "ownValues": own,
                # performance parameters still without a value: the instance cannot be performed
                "missing": [p["name"] for p, _ in params(comp["id"]) if p["name"] not in merged],
                "requirementUsages": reqs, "missingRequirementValues": missing_values(reqs),
            }
        )
    inst_by_id = {i["id"]: i for i in instances}
    # a component's own part features typed by a component (a propeller's material)
    # are filled like slots: part :>> material :> Catalog::cfrpWoven;
    for i in instances:
        sub = {p["name"]: None for p, _ in idx.features(i["componentId"], ("part",)) if p.get("typeId") in component_ids}
        for c in idx.children.get(i["id"], []):
            if c["kind"] == "part" and c["name"] in sub:
                filled = inst_by_id.get(c.get("subsets") or "")
                sub[c["name"]] = filled and filled["qualifiedName"]
        i["parts"] = sub

    # ---------------------------------------------------------- components
    components = []
    for n in part_defs + action_defs:
        if n["id"] not in library_ids:
            continue
        components.append(
            {
                **located(n),
                "kind": "part" if n["kind"] == "part def" else "action",
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "attributes": [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))],
                "params": [_attr(p, o, idx) for p, o in params(n["id"])],
                "ports": [
                    {"name": p["name"], "type": p.get("typeName"), "declaredIn": _qn(idx.nodes.get(o))}
                    for p, o in idx.features(n["id"], ("port",))
                ],
                "parts": [
                    {
                        "name": p["name"], "type": p.get("typeName"), "multiplicity": p.get("multiplicity"),
                        # a part typed by a component: the instances that can fill it
                        "candidates": [i["id"] for i in instances if i["componentId"] in specializations(p["typeId"])]
                        if p.get("typeId") in component_ids else [],
                    }
                    for p, _ in idx.features(n["id"], ("part",))
                ],
                "instances": [i["id"] for i in instances if i["componentId"] == n["id"]],
                "requirementUsages": requirement_usages(n["id"]),
                "lineageIds": idx.ancestors(n["id"]),
            }
        )

    # ---------------------------------------------------------- requirement defs and their subject type
    # A requirement def is a *template* (for a library) when it is abstract or
    # leaves an attribute without a value: a requirement usage of it, in a
    # component, architecture, behavior or mission type, is given the missing
    # values by each instance. Other requirement defs with a subject apply to
    # every system instance whose architecture conforms to the subject type.
    requirements = []
    for n in graph["nodes"]:
        if n["kind"] != "requirement def":
            continue
        subject = next((c for a in idx.ancestors(n["id"]) for c in idx.children.get(a, []) if c["kind"] == "subject"), None)
        params = [p["name"] for p, _ in idx.features(n["id"], ("param",)) if p.get("direction") == "in"]
        attrs = idx.features(n["id"], ("attribute",))
        thresholds = [{"name": a["name"], "type": a.get("typeName"), "value": a.get("value")} for a, _ in attrs]
        requirements.append(
            {
                **located(n),
                "subject": subject and subject["name"],
                "subjectType": subject and subject.get("typeName"),
                "subjectTypeId": subject and subject.get("typeId"),
                "params": params,
                "shortId": n.get("shortName"),
                "isAbstract": n.get("isAbstract", False),
                "template": n.get("isAbstract", False) or any(not t["value"] for t in thresholds),
                "thresholds": thresholds,
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], []) if g in req_def_ids],
                "lineage": [_qn(idx.nodes[a]) for a in idx.ancestors(n["id"])],
                "constraints": req_constraints(n["id"]),
                "children": [{"name": c["name"], "type": c.get("typeName")} for c, _ in idx.features(n["id"], ("requirement",))],
            }
        )
    applicable = [r for r in requirements if r["subjectTypeId"] and not r["template"]]

    # ---------------------------------------------------------- mission simulations: tool-bound defs with a subject
    # A tool-bound def whose subject is a component (or behavior) is a
    # *component analysis*, run for a catalog instance from the Library; the
    # others are mission simulations, run for system instances.
    simulations = []
    component_analyses = []
    for n in graph["nodes"]:
        if not (n.get("tool") or {}).get("toolName") or not n["kind"].endswith(" def"):
            continue
        subject = next((c for c in idx.children.get(n["id"], []) if c["kind"] == "subject"), None)
        if not subject or not subject.get("typeId"):
            continue
        if subject["typeId"] in library_ids:
            component_analyses.append(
                {
                    **located(n), "toolName": n["tool"]["toolName"], "subject": subject["name"], "subjectTypeId": subject["typeId"],
                    "inputs": [
                        {"name": c["name"], "value": c["value"]}
                        for c in idx.children.get(n["id"], [])
                        if c["kind"] == "param" and c.get("direction") == "in" and c.get("value")
                    ],
                    "outputs": [c["name"] for c in idx.children.get(n["id"], []) if c["kind"] == "param" and c.get("direction") == "out"],
                }
            )
            continue
        inputs = [
            {"name": c["name"], "value": c["value"]}
            for c in idx.children.get(n["id"], [])
            if c["kind"] == "param" and c.get("direction") == "in" and c.get("value")
        ]
        outputs = [c["name"] for c in idx.children.get(n["id"], []) if c["kind"] == "param" and c.get("direction") == "out"]
        verifies = [_qn(idx.nodes[e["target"]]) for e in graph["edges"] if e["kind"] == "verify" and e["source"] == n["id"] and e["target"] in idx.nodes]
        simulations.append(
            {
                **located(n), "toolName": n["tool"]["toolName"], "subject": subject["name"], "subjectTypeId": subject["typeId"],
                "inputs": inputs, "outputs": outputs, "verifies": verifies,
            }
        )
    for r in requirements:
        r["verifiedBy"] = [s["qualifiedName"] for s in simulations if r["qualifiedName"] in s["verifies"]]

    # ---------------------------------------------------------- tests: verification defs (and simulations)
    # A test verifies the requirement defs (and requirement usages, such as
    # `verify drone.endurance;`) its objective names. Bound to an external
    # analysis it is a simulation; otherwise the requirement is checked by
    # evaluating it against the model (analysis).
    tests = []
    for n in graph["nodes"]:
        tool = (n.get("tool") or {}).get("toolName")
        if n["kind"] != "verification def" and not (tool and n["kind"].endswith(" def")):
            continue
        subject = next((c for c in idx.children.get(n["id"], []) if c["kind"] == "subject"), None)
        if tool and not (subject and subject.get("typeId")) and n["kind"] != "verification def":
            continue
        tests.append(
            {
                **located(n),
                "method": "simulation" if tool else "analysis",
                "toolName": tool,
                "subject": subject and subject["name"],
                "subjectType": subject and subject.get("typeName"),
                "subjectTypeId": subject and subject.get("typeId"),
                "verifies": [_qn(idx.nodes[e["target"]]) for e in graph["edges"] if e["kind"] == "verify" and e["source"] == n["id"] and e["target"] in idx.nodes],
            }
        )
    for r in requirements:
        r["tests"] = [t["qualifiedName"] for t in tests if r["qualifiedName"] in t["verifies"]]
    for c in components:
        c["analyses"] = [a["qualifiedName"] for a in component_analyses if a["subjectTypeId"] in c["lineageIds"]]

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
        behaviors = []
        for p, owner in idx.features(n["id"], ("perform",)):
            tid = p.get("typeId")
            if tid not in behavior_ids:
                continue
            accepted = specializations(tid)
            behaviors.append(
                {
                    "name": p["name"],
                    "type": _qn(idx.nodes[tid]),
                    "typeId": tid,
                    "doc": p.get("doc"),
                    "declaredIn": _qn(idx.nodes.get(owner)),
                    # only complete instances can be performed
                    "candidates": [i["id"] for i in instances if i["componentId"] in accepted and not i["missing"]],
                }
            )
        attributes = [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))]
        architectures.append(
            {
                **located(n),
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "slots": slots,
                "behaviors": behaviors,
                "attributes": attributes,
                "connections": sum(1 for e in graph["edges"] if e.get("context") in idx.ancestors(n["id"])),
                # requirement defs whose subject this architecture can be: Req(system) evaluates them
                "requirements": [
                    r["qualifiedName"] for r in applicable if r["subjectTypeId"] in idx.ancestors(n["id"]) and not r["params"]
                ],
                # ... and those that also take mission totals: Req(system, total, ...)
                "missionRequirements": [
                    r["qualifiedName"] for r in applicable if r["subjectTypeId"] in idx.ancestors(n["id"]) and r["params"]
                ],
                "requirementUsages": requirement_usages(n["id"]),
                "lineageIds": idx.ancestors(n["id"]),
                # mission simulations this architecture can be the subject of
                "simulations": [s["qualifiedName"] for s in simulations if s["subjectTypeId"] in idx.ancestors(n["id"])],
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
        behavior_names = {b["name"] for b in arch["behaviors"]}
        assignments: dict[str, str | None] = {}
        performs: dict[str, str | None] = {b: None for b in behavior_names}
        for c in idx.children.get(n["id"], []):
            if c["kind"] == "perform" and c["name"] in behavior_names:
                inst = inst_by_id.get(c.get("subsets") or "")
                performs[c["name"]] = inst["qualifiedName"] if inst else None
            if c["kind"] != "part" or c["name"] not in slot_names:
                continue
            inst = inst_by_id.get(c.get("subsets") or "")
            if not inst and c.get("value"):  # binding form: part :>> engine = Catalog::i4;
                inst = next((i for i in instances if i["qualifiedName"].endswith(c["value"])), None)
            assignments[c["name"]] = inst["qualifiedName"] if inst else None
        reqs = requirement_usages(arch["id"], n)
        configurations.append(
            {
                **located(n), "architectureId": arch["id"], "architecture": arch["qualifiedName"],
                "assignments": assignments, "behaviors": performs,
                "requirementUsages": reqs, "missingRequirementValues": missing_values(reqs),
            }
        )

    # ---------------------------------------------------------- mission types and missions
    mission_types = []
    for n in action_defs:
        if n["id"] not in mission_ids:
            continue
        mission_types.append(
            {
                **located(n),
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "totals": [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))],
                "requirementUsages": requirement_usages(n["id"]),
                "lineageIds": idx.ancestors(n["id"]),
            }
        )
    missions = []
    for n in graph["nodes"]:
        if n["kind"] != "action" or n.get("typeId") not in mission_ids or not idx.in_package(n):
            continue
        steps = []
        for c in idx.children.get(n["id"], []):  # document order is the order of the steps
            if c["kind"] != "action":
                continue
            behavior = idx.nodes.get(c.get("typeId") or "")
            values = {a["name"]: a["value"] for a in idx.children.get(c["id"], []) if a["kind"] == "attribute" and a.get("value")}
            steps.append({"name": c["name"], "behavior": _qn(behavior) if c.get("typeId") in behavior_ids else None, "values": values})
        reqs = requirement_usages(n["typeId"], n)
        missions.append(
            {
                **located(n), "missionTypeId": n["typeId"], "missionType": _qn(idx.nodes[n["typeId"]]), "steps": steps,
                "requirementUsages": reqs, "missingRequirementValues": missing_values(reqs),
            }
        )

    packages = []
    for n in graph["nodes"]:
        if n["kind"] == "package":
            m = model_of(n) or {}
            packages.append({"qualifiedName": _qn(n), "modelId": m.get("id"), "modelName": m.get("name"), "loc": n.get("loc")})
    tags = {
        n["name"]: _qn(n)
        for n in graph["nodes"]
        if n["kind"] == "metadata def" and n["name"] in (COMPONENT_TAG, ARCHITECTURE_TAG, BEHAVIOR_TAG, MISSION_TAG)
    }
    return {
        "components": components,
        "instances": instances,
        "architectures": architectures,
        "configurations": configurations,
        "missionTypes": mission_types,
        "missions": missions,
        "requirements": requirements,
        "simulations": simulations,
        "componentAnalyses": component_analyses,
        "tests": tests,
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


# an instance sets a requirement value with a number, optionally with a unit: 1800, 2.5 [SI::kg]
NUMBER_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?:\s*\[\s*[A-Za-z_][\w:]*(?:\s*[*/^]\s*[\w:]+)*\s*\])?$")


def requirements_text(values: dict[str, str], ids: dict[str, str] | None = None) -> list[str]:
    """Redefinitions of requirement usages that set their values:
    ``{"endurance.minimum": "15", "budget.inner.limit": "2 [SI::kg]"}`` gives
    ``requirement :>> endurance { attribute :>> minimum = 15; }`` and a nested
    ``requirement :>> inner { ... }`` inside ``budget``.

    A usage declared with an id (``ids``: path -> short name) is redefined
    restating it, ``requirement <'BAT-01'> cap :>> cap { ... }``: OpenSysML
    finds a feature with a short name by that name only."""
    ids = ids or {}
    tree: dict[str, Any] = {}
    for key, value in values.items():
        value = (value or "").strip()
        if not value:
            continue
        *path, attr = key.split(".")
        if not path:
            raise CatalogError(f"'{key}': a requirement value is named requirement.attribute")
        for name in (*path, attr):
            check_name(name, "requirement value")
        if not NUMBER_RE.match(value):
            raise CatalogError(f"{key} = '{value}': a requirement value is a number, optionally with a unit such as [SI::kg]")
        node = tree
        for name in path:
            node = node.setdefault(name, {})
        node.setdefault("", {})[attr] = value

    def emit(path: str, name: str, node: dict[str, Any]) -> list[str]:
        inner = [f"attribute :>> {a} = {v};" for a, v in node.get("", {}).items()]
        for k, sub in node.items():
            if k:
                inner += emit(f"{path}.{k}", k, sub)
        sid = ids.get(path)
        if sid and not re.match(r"^[\w.\- ]+$", sid):
            raise CatalogError(f"requirement id '{sid}' cannot be written")
        head = f"requirement <'{sid}'> {name} :>> {name}" if sid else f"requirement :>> {name}"
        if len(inner) == 1 and not inner[0].startswith("requirement"):
            return [f"{head} {{ {inner[0]} }}"]
        return [f"{head} {{", *_body(inner), "}"]

    return [line for k, sub in tree.items() for line in emit(k, k, sub)]


def instance_text(
    name: str, component_ref: str, values: dict[str, str], doc: str = "", keyword: str = "part", params: frozenset[str] = frozenset(),
    requirements: dict[str, str] | None = None, requirement_ids: dict[str, str] | None = None,
    parts: dict[str, str] | None = None,
) -> list[str]:
    """A component or behavior instance; values of ``params`` are written as parameter
    bindings, ``parts`` (part name -> instance reference) fill its component-typed parts."""
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
        inner.append(f"{'in' if attr in params else 'attribute'} :>> {attr} = {value};")
    for part, inst_ref in (parts or {}).items():
        check_name(part, "part")
        if inst_ref:
            if not TYPE_RE.match(inst_ref):
                raise CatalogError(f"'{inst_ref}' is not a qualified name")
            inner.append(f"part :>> {part} :> {inst_ref};")
    inner += requirements_text(requirements or {}, requirement_ids)
    if not inner:
        return [f"{keyword} {name} : {component_ref};"]
    return [f"{keyword} {name} : {component_ref} {{", *_body(inner), "}"]


def configuration_text(
    name: str, arch_ref: str, assignments: dict[str, str], doc: str = "", behaviors: dict[str, str] | None = None,
    requirements: dict[str, str] | None = None, requirement_ids: dict[str, str] | None = None,
) -> list[str]:
    check_name(name)
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    for slot, inst_ref in assignments.items():
        check_name(slot, "slot")
        if inst_ref:
            inner.append(f"part :>> {slot} :> {inst_ref};")
    for slot, inst_ref in (behaviors or {}).items():
        check_name(slot, "behavior")
        if inst_ref:
            inner.append(f"perform action :>> {slot} :> {inst_ref};")
    inner += requirements_text(requirements or {}, requirement_ids)
    if not inner:
        return [f"part {name} : {arch_ref};"]
    return [f"part {name} : {arch_ref} {{", *_body(inner), "}"]


# words that cannot name a step without quotes
_RESERVED = {"in", "out", "inout", "start", "done", "then", "first", "action", "state", "part", "attribute", "end", "if", "else"}


def step_names(behaviors: list[str]) -> list[str]:
    """Step names of a mission: the behavior's name lower-cased, numbered from its second use."""
    seen: dict[str, int] = {}
    out = []
    for qn in behaviors:
        name = qn.rsplit("::", 1)[-1]
        base = name[:1].lower() + name[1:]
        if base in _RESERVED:
            base += "Step"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def mission_text(
    name: str, type_ref: str, steps: list[tuple[str, str, dict[str, str]]], doc: str = "", requirements: dict[str, str] | None = None,
    requirement_ids: dict[str, str] | None = None,
) -> list[str]:
    """A mission: ``steps`` are (step name, behavior reference, quantity -> expression), in order."""
    check_name(name)
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    if steps:
        inner.append("first start;")
        for step, behavior_ref, values in steps:
            check_name(step, "step")
            if not TYPE_RE.match(behavior_ref):
                raise CatalogError(f"'{behavior_ref}' is not a qualified name")
            body = []
            for q, value in values.items():
                check_name(q, "quantity")
                value = (value or "").strip()
                if value:
                    check_expr(value, f"{step}.{q}")
                    body.append(f"attribute :>> {q} = {value};")
            inner.append(f"then action {step} : {behavior_ref}" + (f" {{ {' '.join(body)} }}" if body else ";"))
        inner.append("then done;")
    inner += requirements_text(requirements or {}, requirement_ids)
    if not inner:
        return [f"action {name} : {type_ref};"]
    return [f"action {name} : {type_ref} {{", *_body(inner), "}"]


def requirement_usage_text(name: str, type_ref: str, values: dict[str, str] | None = None) -> list[str]:
    """A requirement usage in a def, typed by a (library) requirement def; ``values``
    bind some of its attributes, e.g. ``actual = energy``, leaving the others
    (the thresholds) to each instance."""
    check_name(name, "requirement")
    if not TYPE_RE.match(type_ref):
        raise CatalogError(f"'{type_ref}' is not a qualified name")
    inner = []
    for attr, value in (values or {}).items():
        check_name(attr, f"{name} attribute")
        value = (value or "").strip()
        if value:
            check_expr(value, f"{name}.{attr}")
            inner.append(f"attribute :>> {attr} = {value};")
    if not inner:
        return [f"requirement {name} : {type_ref};"]
    return [f"requirement {name} : {type_ref} {{ {' '.join(inner)} }}"]


def component_text(
    name: str,
    tag_ref: str,
    specializes: str | None,
    attributes: list[dict[str, str]],
    ports: list[dict[str, str]],
    doc: str = "",
    abstract: bool = False,
    keyword: str = "part",
    params: list[dict[str, str]] | None = None,
    inherited_params: list[str] | None = None,
    requirements: list[dict[str, Any]] | None = None,
) -> list[str]:
    check_name(name)
    head = f"{'abstract ' if abstract else ''}#{tag_ref} {keyword} def {name}"
    if specializes:
        if not TYPE_RE.match(specializes):
            raise CatalogError(f"'{specializes}' is not a qualified name")
        head += f" :> {specializes}"
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    # a behavior's own parameters redefine the inherited ones by position:
    # restate those first so new parameters come after them
    inner += [f"in :>> {p};" for p in inherited_params or []] if params else []
    for kind, feats in (("in", params or []), ("attribute", attributes), ("port", ports)):
        for f in feats:
            fname, ftype = (f.get("name") or "").strip(), (f.get("type") or "").strip()
            if not fname:
                continue
            check_name(fname, kind)
            if ftype and not TYPE_RE.match(ftype.replace("~ ", "~")):
                raise CatalogError(f"{kind} {fname}: '{ftype}' is not a type name")
            inner.append(f"{kind} {fname}{' : ' + ftype if ftype else ''};")
    for r in requirements or []:
        if (r.get("name") or "").strip():
            inner += requirement_usage_text(r["name"].strip(), (r.get("type") or "").strip(), r.get("values"))
    return [f"{head} {{", *_body(inner), "}"]


def flown_step(name: str, perform_qn: str, step_qn: str) -> str:
    """A step as a system flies it: an action that is both the system's performed
    behavior (its parameters) and the mission step (its quantities), so the
    behavior's derived attributes evaluate with both."""
    return f"action {name} :> {perform_qn}, {step_qn};"


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
