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
* a simulation tagged ``#SystemSimulation`` is of a **system instance on its
  own**, not of a mission: the performed behaviors' parameters are sent instead
  of flown steps (Configurator). A requirement def with ``in`` parameters that
  a system simulation verifies is a *system requirement*, the others mission
  requirements;
* a requirement def tagged ``#ControlTest`` is a **control test** type: the
  attributes it gives a value (``command``, ``step``, ``rate``, ``hold``) say
  what a system simulation flies, those it leaves open are limits its
  instances set, its ``in`` parameters which of the test's results it is
  checked with. The simulation flies the test *instances* in the
  specification that apply to the system;
* the **specification** is the hierarchy of requirement usages in packages:
  *statements* (no type, no constraint) hold more statements or *instances*
  (a requirement def, usually abstract, with its parameters, the attributes,
  set). An instance applies to the elements its ``satisfy`` relations name;
  with none, to every system instance (or component instance) its subject type
  fits, except those that satisfy another instance of the same type (which
  replaces it for them: per-system limits). A statement holds when every
  instance under it that applies does;
* **simulation evidence** is a verification usage typed by a mission
  simulation and tagged ``#SimulationEvidence`` (metadata): its subject is the
  system instance, its metadata attributes point at a stored result (see
  ``evidence_text``);
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
EVIDENCE_TAG = "SimulationEvidence"
SYSTEM_SIM_TAG = "SystemSimulation"
TEST_TAG = "ControlTest"
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


def build(graph: dict[str, Any], file_models: dict[str, dict[str, str]], texts: dict[str, str] | None = None) -> dict[str, Any]:
    """Catalog of a project from its combined graph.

    ``file_models`` maps a source file name to ``{"id", "name"}`` of its model;
    ``texts`` maps it to its source, read for evidence metadata values.
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
    use_case_defs = [n for n in graph["nodes"] if n["kind"] == "use case def"]
    mission_ids = {n["id"] for n in action_defs + use_case_defs if idx.tagged(n["id"], MISSION_TAG)} - behavior_ids
    # components and behaviors share the library: instances of either are usages in a package
    library_ids = component_ids | behavior_ids

    def specializations(def_id: str) -> set[str]:
        return {d["id"] for d in part_defs + action_defs if def_id in idx.ancestors(d["id"])}

    def params(def_id: str) -> list[tuple[dict[str, Any], str]]:
        return [(p, o) for p, o in idx.features(def_id, ("param",)) if p.get("direction") in ("in", None)]

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
        instances.append(
            {
                **located(n), "kind": n["kind"], "componentId": comp["id"], "component": _qn(comp),
                "values": merged, "ownValues": own,
                # performance parameters still without a value: the instance cannot be performed
                "missing": [p["name"] for p, _ in params(comp["id"]) if p["name"] not in merged],
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
                "lineageIds": idx.ancestors(n["id"]),
            }
        )

    # ---------------------------------------------------------- requirement defs and their subject type
    requirements = []
    for n in graph["nodes"]:
        if n["kind"] != "requirement def":
            continue
        subject = next((c for c in idx.children.get(n["id"], []) if c["kind"] == "subject"), None)
        if subject and subject.get("typeId"):
            params = [c["name"] for c in idx.children.get(n["id"], []) if c["kind"] == "param" and c.get("direction") == "in"]
            requirements.append({
                **located(n), "subject": subject["name"], "subjectTypeId": subject["typeId"], "params": params,
                # a control test (#ControlTest): its attributes say what the simulation flies
                "isTest": idx.tagged(n["id"], TEST_TAG),
                "attributes": {a["name"]: a["value"] for a, _ in idx.features(n["id"], ("attribute",)) if a.get("value")},
            })

    # ---------------------------------------------------------- mission simulations: tool-bound defs with a subject
    # A tool-bound def whose subject is a component (or behavior) is a
    # *component analysis*, run for a catalog instance from the Library; the
    # others are simulations of system instances.
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
                "inputs": inputs, "outputs": outputs, "verifies": verifies, "kind": n["kind"],
                "scope": "system" if SYSTEM_SIM_TAG in (n.get("metadata") or []) else "mission",
            }
        )
    for r in requirements:
        r["verifiedBy"] = [s["qualifiedName"] for s in simulations if r["qualifiedName"] in s["verifies"]]
    for c in components:
        c["analyses"] = [a["qualifiedName"] for a in component_analyses if a["subjectTypeId"] in c["lineageIds"]]
    system_verified = {q for s in simulations if s["scope"] == "system" for q in s["verifies"]}

    # ---------------------------------------------------------- specification: requirement usages in packages
    req_by_id = {r["id"]: r for r in requirements}
    satisfied: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["kind"] == "satisfy" and e["source"] in idx.nodes and e["target"] in idx.nodes:
            satisfied.setdefault(e["target"], []).append(_qn(idx.nodes[e["source"]]))

    def in_spec(n: dict[str, Any]) -> bool:
        """A requirement usage in a package, or nested in such a usage (not in an objective)."""
        owner = idx.nodes.get(n.get("owner") or "")
        while owner and owner["kind"] == "requirement":
            owner = idx.nodes.get(owner.get("owner") or "")
        return bool(owner and owner["kind"] == "package")

    arch_lineages = [idx.ancestors(a) for a in arch_ids]
    def req_entry(n: dict[str, Any]) -> dict[str, Any]:
        """A requirement usage: a statement, or an instance of a requirement type with its parameters."""
        owner = idx.nodes.get(n.get("owner") or "")
        tdef = req_by_id.get(n.get("typeId") or "")
        own_attrs = {c["name"]: c["value"] for c in idx.children.get(n["id"], []) if c["kind"] == "attribute" and c.get("value")}
        subject_type = tdef["subjectTypeId"] if tdef else None
        if subject_type and any(subject_type in lin for lin in arch_lineages):
            level = "system"
        elif subject_type and (subject_type in component_ids or any(subject_type in idx.ancestors(c) for c in component_ids)):
            level = "component"
        else:
            level = "statement" if not tdef else "other"
        return (
            {
                **located(n),
                "shortName": n.get("shortName"),
                "parent": _qn(owner) if owner and owner["kind"] == "requirement" else None,
                "type": tdef["qualifiedName"] if tdef else (n.get("typeName") or None),
                "typeId": n.get("typeId"),
                "isAbstractType": bool(tdef and idx.nodes[tdef["id"]].get("isAbstract")),
                "level": level,
                "subject": tdef["subject"] if tdef else None,
                "subjectTypeId": subject_type,
                "subjectType": _qn(idx.nodes.get(subject_type or "")) if subject_type else None,
                # parameters: the type's attributes, with the values this instance (or the type) gives them
                "parameters": [
                    {**_attr(a, o, idx), "value": own_attrs.get(a["name"], a.get("value")), "bound": a["name"] in own_attrs}
                    for a, o in (idx.features(tdef["id"], ("attribute",)) if tdef else [])
                ],
                # measured values a verification supplies (an in parameter of the type)
                "measured": tdef["params"] if tdef else [],
                # a control test: what the simulation flies, the attributes its type gives a
                # value (the instance may override them); those left open are its limits
                "isTest": bool(tdef and tdef["isTest"]),
                "flies": {
                    a["name"]: own_attrs.get(a["name"], a["value"])
                    for a, _ in (idx.features(tdef["id"], ("attribute",)) if tdef and tdef["isTest"] else []) if a.get("value")
                },
                "constraints": (idx.nodes[tdef["id"]].get("constraints") or []) if tdef else (n.get("constraints") or []),
                "satisfiedBy": satisfied.get(n["id"], []),
            }
        )

    specification = [req_entry(n) for n in graph["nodes"] if n["kind"] == "requirement" and in_spec(n)]

    # ---------------------------------------------------------- simulation evidence
    sim_ids = {s["id"] for s in simulations}
    evidence = []
    for n in graph["nodes"]:
        if n["kind"] != "verification" or EVIDENCE_TAG not in (n.get("metadata") or []) or n.get("typeId") not in sim_ids or not idx.in_package(n):
            continue
        subject = next((c for c in idx.children.get(n["id"], []) if c["kind"] == "subject"), None)
        loc = n.get("loc") or {}
        evidence.append(
            {
                **located(n),
                "simulation": _qn(idx.nodes[n["typeId"]]),
                "system": subject.get("value") if subject else None,
                "values": metadata_values(_slice((texts or {}).get(loc.get("file") or ""), loc)),
            }
        )

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
                    r["qualifiedName"] for r in requirements if r["subjectTypeId"] in idx.ancestors(n["id"]) and not r["params"]
                ],
                # ... those that take mission results: Req(system, total or simulation result, ...)
                "missionRequirements": [
                    r["qualifiedName"] for r in requirements
                    if r["subjectTypeId"] in idx.ancestors(n["id"]) and r["params"] and r["qualifiedName"] not in system_verified
                ],
                # ... and those a system simulation verifies: Req(system, result, ...)
                "systemRequirements": [
                    r["qualifiedName"] for r in requirements
                    if r["subjectTypeId"] in idx.ancestors(n["id"]) and r["params"] and r["qualifiedName"] in system_verified
                ],
                # simulations this architecture can be the subject of: of missions, and of the system on its own
                "simulations": [s["qualifiedName"] for s in simulations if s["subjectTypeId"] in idx.ancestors(n["id"]) and s["scope"] == "mission"],
                "systemSimulations": [s["qualifiedName"] for s in simulations if s["subjectTypeId"] in idx.ancestors(n["id"]) and s["scope"] == "system"],
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
        # in the architecture's order (a set's order changes from one process to the next)
        performs: dict[str, str | None] = {b["name"]: None for b in arch["behaviors"]}
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
        configurations.append(
            {
                **located(n), "architectureId": arch["id"], "architecture": arch["qualifiedName"],
                "assignments": assignments, "behaviors": performs,
            }
        )

    # ---------------------------------------------------------- mission types and missions
    def objective_requirements(owner_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """The requirement instances in the objective of a mission type or a mission, and the objective."""
        objective = next((c for c in idx.children.get(owner_id, []) if c["kind"] == "requirement" and not c.get("typeId")), None)
        if not objective:
            return [], None
        return [req_entry(c) for c in idx.children.get(objective["id"], []) if c["kind"] == "requirement" and c.get("typeId")], objective

    mission_requirements: list[dict[str, Any]] = []
    mission_types = []
    for n in action_defs + use_case_defs:
        if n["id"] not in mission_ids:
            continue
        reqs, _ = objective_requirements(n["id"])
        for r in reqs:
            r["owner"] = _qn(n)
        mission_requirements += reqs
        mission_types.append(
            {
                **located(n),
                "kind": n["kind"],
                "isAbstract": n.get("isAbstract", False),
                "specializes": [_qn(idx.nodes[g]) for g in idx.generals.get(n["id"], [])],
                "totals": [_attr(a, o, idx) for a, o in idx.features(n["id"], ("attribute",))],
                # the requirements every mission of this type puts on the drone flying it
                "requirements": [r["qualifiedName"] for r in reqs],
            }
        )
    # assignments: allocations of a mission to a system instance
    allocated: dict[str, list[dict[str, Any]]] = {}
    missions = []
    for n in graph["nodes"]:
        if n["kind"] not in ("action", "use case") or n.get("typeId") not in mission_ids or not idx.in_package(n):
            continue
        steps = []
        for c in idx.children.get(n["id"], []):  # document order is the order of the steps
            if c["kind"] != "action":
                continue
            behavior = idx.nodes.get(c.get("typeId") or "")
            values = {a["name"]: a["value"] for a in idx.children.get(c["id"], []) if a["kind"] == "attribute" and a.get("value")}
            steps.append({"name": c["name"], "behavior": _qn(behavior) if c.get("typeId") in behavior_ids else None, "values": values})
        own, objective = objective_requirements(n["id"])
        for r in own:
            r["owner"] = _qn(n)
        mission_requirements += own
        missions.append({
            **located(n), "missionTypeId": n["typeId"], "missionType": _qn(idx.nodes[n["typeId"]]), "steps": steps,
            # its own requirements, besides its type's; the objective is kept when the mission is rewritten
            "requirements": [r["qualifiedName"] for r in own],
            "objectiveLoc": objective.get("loc") if objective else None,
        })
    mission_by_id = {m["id"]: m for m in missions}
    cfg_by_id = {c["id"]: c for c in configurations}
    assignments = []
    for e in graph["edges"]:
        if e["kind"] == "allocation" and e["source"] in mission_by_id and e["target"] in cfg_by_id:
            assignments.append({"mission": mission_by_id[e["source"]]["qualifiedName"], "system": cfg_by_id[e["target"]]["qualifiedName"], "loc": e.get("loc")})
    for m in missions:
        m["assignedTo"] = [a["system"] for a in assignments if a["mission"] == m["qualifiedName"]]
    # where a mission requirement can apply: the architectures its subject type fits
    for r in mission_requirements:
        r["architectures"] = [a["qualifiedName"] for a in architectures if r["subjectTypeId"] in idx.ancestors(a["id"])]

    # applicability: what each instance's satisfy relations name, else every system
    # (component) instance its subject type fits
    for r in specification:
        if r["satisfiedBy"]:
            r["appliesTo"], r["explicit"] = list(r["satisfiedBy"]), True
        elif r["level"] == "system":
            r["appliesTo"] = [c["qualifiedName"] for c in configurations if r["subjectTypeId"] in idx.ancestors(c["architectureId"])]
            r["explicit"] = False
        elif r["level"] == "component":
            r["appliesTo"] = [i["qualifiedName"] for i in instances if i["kind"] == "part" and r["subjectTypeId"] in idx.ancestors(i["componentId"])]
            r["explicit"] = False
        else:
            r["appliesTo"], r["explicit"] = [], False
    # per-subject limits: an instance satisfied by a subject replaces, for that
    # subject, the instances of the same type that apply to it by subject type
    for r in specification:
        if r["explicit"] or not r["typeId"]:
            continue
        replaced = {
            subj: o["qualifiedName"] for o in specification
            if o["explicit"] and o["typeId"] == r["typeId"] for subj in o["appliesTo"]
        }
        r["replacedFor"] = {subj: replaced[subj] for subj in r["appliesTo"] if subj in replaced}
        r["appliesTo"] = [subj for subj in r["appliesTo"] if subj not in replaced]

    packages = []
    for n in graph["nodes"]:
        if n["kind"] == "package":
            m = model_of(n) or {}
            packages.append({"qualifiedName": _qn(n), "modelId": m.get("id"), "modelName": m.get("name"), "loc": n.get("loc")})
    tags = {
        n["name"]: _qn(n)
        for n in graph["nodes"]
        if n["kind"] == "metadata def" and n["name"] in (COMPONENT_TAG, ARCHITECTURE_TAG, BEHAVIOR_TAG, MISSION_TAG, EVIDENCE_TAG, SYSTEM_SIM_TAG, TEST_TAG)
    }
    return {
        "components": components,
        "instances": instances,
        "architectures": architectures,
        "configurations": configurations,
        "missionTypes": mission_types,
        "missions": missions,
        "missionRequirements": mission_requirements,
        "assignments": assignments,
        "requirements": requirements,
        "simulations": simulations,
        "componentAnalyses": component_analyses,
        "evidence": evidence,
        "specification": specification,
        "packages": packages,
        "tags": tags,
    }


_META_VALUE = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"')


def _slice(source: str | None, loc: dict[str, Any]) -> str:
    """The text an element spans."""
    if not source or not loc.get("line"):
        return ""
    lines = source.splitlines(keepends=True)
    return source[_offset(lines, loc["line"], loc["column"]) : _offset(lines, loc["endLine"], loc["endColumn"])]


def metadata_values(text: str) -> dict[str, str]:
    """String attribute values (``name = "value";``) written in an element's text."""
    return {k: v.replace('\\"', '"') for k, v in _META_VALUE.findall(text)}


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


def instance_text(
    name: str, component_ref: str, values: dict[str, str], doc: str = "", keyword: str = "part", params: frozenset[str] = frozenset(),
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
    if not inner:
        return [f"{keyword} {name} : {component_ref};"]
    return [f"{keyword} {name} : {component_ref} {{", *_body(inner), "}"]


def configuration_text(name: str, arch_ref: str, assignments: dict[str, str], doc: str = "", behaviors: dict[str, str] | None = None) -> list[str]:
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
    name: str, type_ref: str, steps: list[tuple[str, str, dict[str, str]]], doc: str = "", keyword: str = "action", objective: list[str] | None = None,
) -> list[str]:
    """A mission: ``steps`` are (step name, behavior reference, quantity -> expression),
    in order. ``keyword`` is ``use case`` for a use-case mission type; ``objective``
    (lines) keeps the mission's own requirements."""
    check_name(name)
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    inner += objective or []
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
    if not inner:
        return [f"{keyword} {name} : {type_ref};"]
    return [f"{keyword} {name} : {type_ref} {{", *_body(inner), "}"]


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
    return [f"{head} {{", *_body(inner), "}"]


EVIDENCE_PACKAGE = """package {name} {{
    private import ScalarValues::*;

    // Mission simulation results recorded as verification evidence. Each
    // verification is the mission simulation of one system instance; the result
    // itself (inputs, outputs, timeline) is stored outside the model, at uri.
    metadata def SimulationEvidence {{
        doc /* A recorded mission simulation result, stored outside the model. */
        attribute resultId : String;      // the stored result
        attribute uri : String;           // where to read it
        attribute commitId : String;      // the model version that was simulated
        attribute mission : String;       // qualified name of the mission flown
        attribute verdict : String;       // holds when every requirement it verifies holds
        attribute verdicts : String;      // requirement: verdict, ...
        attribute recordedAt : String;
    }}
}}
"""


def _string(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def evidence_text(
    name: str, simulation_ref: str, subject: str, system_ref: str, values: dict[str, str], doc: str = "", tag_ref: str = EVIDENCE_TAG
) -> list[str]:
    """A recorded result: the simulation with its ``subject`` bound to the system
    instance, and metadata pointing at the stored result."""
    check_name(name)
    check_name(subject, "subject")
    for r in (simulation_ref, system_ref):
        if not TYPE_RE.match(r):
            raise CatalogError(f"'{r}' is not a qualified name")
    inner = []
    if doc.strip():
        check_doc(doc)
        inner.append(f"doc /* {doc.strip()} */")
    inner.append(f"subject {subject} = {system_ref};")
    inner += [f"metadata {tag_ref} {{", *_body([f"{k} = {_string(v)};" for k, v in values.items()]), "}"]
    return [f"verification {name} : {simulation_ref} {{", *_body(inner), "}"]


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
