"""Component library / configurator logic (no database or interpreter needed)."""

import pytest

from app import catalog


def node(id, kind, name, owner=None, file="Lib.sysml", line=1, **kw):
    qn = id.replace("__", "::")
    return {"id": id, "kind": kind, "name": name, "qualifiedName": qn, "owner": owner, "typeId": None, "typeName": None,
            "value": None, "loc": {"file": file, "line": line, "column": 1, "endLine": line, "endColumn": 2}, **kw}


GRAPH = {
    "nodes": [
        node("L", "package", "L"),
        node("L__Component", "metadata def", "Component", "L"),
        node("L__SystemArchitecture", "metadata def", "SystemArchitecture", "L"),
        node("L__Gearbox", "part def", "Gearbox", "L", metadata=["Component"], isAbstract=True),
        node("L__Gearbox__mass", "attribute", "mass", "L__Gearbox", typeName="MassValue"),
        node("L__Manual", "part def", "Manual", "L"),  # a component by specialization (no tag)
        node("L__Manual__gears", "attribute", "gears", "L__Manual", typeName="Integer"),
        node("L__Motor", "part def", "Motor", "L", metadata=["Component"]),
        node("L__Motor__mass", "attribute", "mass", "L__Motor", typeName="MassValue", value="40 [SI::kg]"),
        node("L__Cat", "package", "Cat", "L"),
        node("L__Cat__m6", "part", "m6", "L__Cat", typeId="L__Manual", typeName="Manual"),
        node("L__Cat__m6__gears", "attribute", "gears", "L__Cat__m6", value="6"),
        node("L__Cat__e1", "part", "e1", "L__Cat", typeId="L__Motor", typeName="Motor"),
        node("A", "package", "A", file="Arch.sysml"),
        node("A__Car", "part def", "Car", "A", file="Arch.sysml", metadata=["SystemArchitecture"]),
        node("A__Car__gearbox", "part", "gearbox", "A__Car", file="Arch.sysml", typeId="L__Gearbox", typeName="Gearbox"),
        node("A__Car__motor", "part", "motor", "A__Car", file="Arch.sysml", typeId="L__Motor", typeName="Motor"),
        node("A__Req", "requirement def", "Req", "A", file="Arch.sysml"),
        node("A__Req__car", "subject", "car", "A__Req", file="Arch.sysml", typeId="A__Car"),
        node("A__base", "part", "base", "A", file="Arch.sysml", typeId="A__Car", typeName="Car"),
        node("A__base__gearbox", "part", "gearbox", "A__base", file="Arch.sysml", subsets="L__Cat__m6", redefines="A__Car__gearbox"),
    ],
    "edges": [{"id": "s1", "kind": "specializes", "source": "L__Manual", "target": "L__Gearbox"}],
}
FILES = {"Lib.sysml": {"id": "lib", "name": "Lib"}, "Arch.sysml": {"id": "arch", "name": "Arch"}}


@pytest.fixture(scope="module")
def cat():
    return catalog.build(GRAPH, FILES)


def test_components_include_specializations_and_inherited_attributes(cat):
    comps = {c["name"]: c for c in cat["components"]}
    assert set(comps) == {"Gearbox", "Manual", "Motor"}
    assert comps["Gearbox"]["isAbstract"]
    assert [a["name"] for a in comps["Manual"]["attributes"]] == ["mass", "gears"]  # inherited first
    assert comps["Manual"]["attributes"][0]["declaredIn"] == "L::Gearbox"
    assert comps["Manual"]["modelId"] == "lib"


def test_instances_carry_defaults_and_own_values(cat):
    inst = {i["name"]: i for i in cat["instances"]}
    assert inst["m6"]["component"] == "L::Manual"
    assert inst["m6"]["ownValues"] == {"gears": "6"}
    assert inst["e1"]["values"] == {"mass": "40 [SI::kg]"}  # the component's default


def test_slots_accept_instances_of_specializations(cat):
    (car,) = cat["architectures"]
    slots = {s["name"]: s for s in car["slots"]}
    assert slots["gearbox"]["candidates"] == ["L__Cat__m6"]
    assert slots["motor"]["candidates"] == ["L__Cat__e1"]
    assert car["requirements"] == ["A::Req"]


def test_configurations_resolve_assignments(cat):
    (cfg,) = cat["configurations"]
    assert cfg["qualifiedName"] == "A::base"
    assert cfg["assignments"] == {"gearbox": "L::Cat::m6"}
    assert cfg["modelId"] == "arch"


def test_ref_uses_simple_name_only_when_visible():
    assert catalog.ref("L::Motor", "L::Cat") == "Motor"
    assert catalog.ref("L::Cat::e1", "A::Configs") == "L::Cat::e1"


def test_generated_text_is_validated():
    assert catalog.instance_text("m7", "Manual", {"gears": "7", "mass": ""}, "seven") == [
        "part m7 : Manual {", "    doc /* seven */", "    attribute :>> gears = 7;", "}",
    ]
    assert catalog.configuration_text("c", "Car", {}) == ["part c : Car;"]
    for bad in ({"gears": "1; part x"}, {"gears": "{"}, {"gears": "1 // c"}):
        with pytest.raises(catalog.CatalogError):
            catalog.instance_text("m7", "Manual", bad)
    with pytest.raises(catalog.CatalogError):
        catalog.instance_text("7up", "Manual", {})
    with pytest.raises(catalog.CatalogError):
        catalog.instance_text("m7", "Manual", {}, "a */ b")


SRC = """package P {
    package Cat {
        part a : T;
    }
    package Empty {}
}
"""


def test_insert_member_before_closing_brace():
    out = catalog.insert_member(SRC, {"line": 2, "column": 5, "endLine": 4, "endColumn": 6}, ["part b : T;"])
    assert "        part a : T;\n        part b : T;\n    }\n" in out
    out = catalog.insert_member(SRC, {"line": 5, "column": 5, "endLine": 5, "endColumn": 21}, ["part c : T {", "}"])
    assert "    package Empty {\n        part c : T {\n        }\n    }\n" in out


def test_replace_and_remove_element():
    loc = {"line": 3, "column": 9, "endLine": 3, "endColumn": 20}
    out = catalog.replace_element(SRC, loc, ["part a : U {", "    doc /* x */", "}"])
    assert "        part a : U {\n            doc /* x */\n        }\n    }" in out
    assert catalog.remove_element(SRC, loc) == SRC.replace("        part a : T;\n", "")



MISSION_GRAPH = {
    "nodes": [
        node("B", "package", "B"),
        node("B__Behavior", "metadata def", "Behavior", "B"),
        node("B__Mission", "metadata def", "Mission", "B"),
        node("B__Step", "action def", "Step", "B", metadata=["Behavior"], isAbstract=True),
        node("B__Step__factor", "param", "factor", "B__Step", direction="in"),
        node("B__Step__duration", "attribute", "duration", "B__Step", typeName="Real"),
        node("B__Fly", "action def", "Fly", "B", isAbstract=True),  # a behavior by specialization
        node("B__Fly__factor", "param", "factor", "B__Fly", direction="in"),
        node("B__Fly__speed", "param", "speed", "B__Fly", direction="in"),
        node("B__Fly__distance", "attribute", "distance", "B__Fly", typeName="Real"),
        node("B__Fly__duration", "attribute", "duration", "B__Fly", value="distance / speed"),
        node("B__Lib", "package", "Lib", "B"),
        node("B__Lib__fast", "action", "fast", "B__Lib", typeId="B__Fly", typeName="Fly"),
        node("B__Lib__fast__speed", "param", "speed", "B__Lib__fast", direction="in", value="15"),
        node("B__Lib__fast__factor", "param", "factor", "B__Lib__fast", direction="in", value="1.2"),
        node("B__Lib__draft", "action", "draft", "B__Lib", typeId="B__Fly", typeName="Fly"),
        node("B__Lib__draft__speed", "param", "speed", "B__Lib__draft", direction="in", value="18"),
        node("B__Delivery", "action def", "Delivery", "B", metadata=["Mission"]),
        node("B__Delivery__duration", "attribute", "duration", "B__Delivery", typeName="Real"),
        node("B__Drone", "part def", "Drone", "B", metadata=["SystemArchitecture"]),
        node("B__Drone__fly", "perform", "fly", "B__Drone", typeId="B__Fly", typeName="Fly"),
        node("B__Energy", "requirement def", "Energy", "B"),
        node("B__Energy__drone", "subject", "drone", "B__Energy", typeId="B__Drone"),
        node("B__Energy__duration", "param", "duration", "B__Energy", direction="in"),
        node("B__Cfg", "package", "Cfg", "B"),
        node("B__Cfg__d1", "part", "d1", "B__Cfg", typeId="B__Drone", typeName="Drone"),
        node("B__Cfg__d1__fly", "perform", "fly", "B__Cfg__d1", subsets="B__Lib__fast"),
        node("B__Ms", "package", "Ms", "B"),
        node("B__Ms__trip", "action", "trip", "B__Ms", typeId="B__Delivery", typeName="Delivery"),
        node("B__Ms__trip__fly", "action", "fly", "B__Ms__trip", typeId="B__Fly", typeName="Fly"),
        node("B__Ms__trip__fly__distance", "attribute", "distance", "B__Ms__trip__fly", value="2000"),
        node("B__Ms__trip__other", "action", "other", "B__Ms__trip"),
    ],
    "edges": [{"id": "s1", "kind": "specializes", "source": "B__Fly", "target": "B__Step"}],
}


def test_behaviors_missions_and_mission_requirements():
    cat = catalog.build(MISSION_GRAPH, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    comps = {c["name"]: c for c in cat["components"]}
    assert set(comps) == {"Step", "Fly"} and comps["Fly"]["kind"] == "action"
    assert [p["name"] for p in comps["Fly"]["params"]] == ["factor", "speed"]
    # quantities: attributes without a value, set by mission steps
    assert [a["name"] for a in comps["Fly"]["attributes"] if not a["value"]] == ["distance"]
    inst = {i["name"]: i for i in cat["instances"]}
    assert inst["fast"]["kind"] == "action" and inst["fast"]["missing"] == []
    assert inst["draft"]["missing"] == ["factor"]
    [arch] = cat["architectures"]
    [slot] = arch["behaviors"]
    assert slot["name"] == "fly" and slot["type"] == "B::Fly"
    assert slot["candidates"] == [inst["fast"]["id"]]  # the incomplete one cannot be performed
    assert arch["requirements"] == [] and arch["missionRequirements"] == ["B::Energy"]
    assert cat["requirements"][0]["params"] == ["duration"]
    [cfg] = cat["configurations"]
    assert cfg["behaviors"] == {"fly": "B::Lib::fast"}
    [mt] = cat["missionTypes"]
    assert [t["name"] for t in mt["totals"]] == ["duration"]
    [m] = cat["missions"]
    assert m["steps"] == [
        {"name": "fly", "behavior": "B::Fly", "values": {"distance": "2000"}},
        {"name": "other", "behavior": None, "values": {}},
    ]


def test_behavior_and_mission_text():
    names = catalog.step_names(["B::Fly", "B::Land", "B::Fly", "B::Start"])
    assert names == ["fly", "land", "fly_2", "startStep"]
    text = catalog.mission_text("trip", "Delivery", [("fly", "Fly", {"distance": "2000"}), ("land", "Land", {})], "2 km")
    assert text == [
        "action trip : Delivery {",
        "    doc /* 2 km */",
        "    first start;",
        "    then action fly : Fly { attribute :>> distance = 2000; }",
        "    then action land : Land;",
        "    then done;",
        "}",
    ]
    assert catalog.mission_text("empty", "Delivery", []) == ["action empty : Delivery;"]
    assert catalog.instance_text("fast", "Fly", {"speed": "15", "note": "1"}, keyword="action", params=frozenset({"speed"}))[1:3] == [
        "    in :>> speed = 15;",
        "    attribute :>> note = 1;",
    ]
    # a specialized behavior restates the inherited parameters before its own
    assert catalog.component_text("Fly", "Behavior", "Step", [], [], keyword="action", params=[{"name": "speed", "type": "Real"}], inherited_params=["factor"]) == [
        "#Behavior action def Fly :> Step {",
        "    in :>> factor;",
        "    in speed : Real;",
        "}",
    ]
    assert catalog.configuration_text("d1", "Drone", {}, behaviors={"fly": "Lib::fast"}) == ["part d1 : Drone {", "    perform action :>> fly :> Lib::fast;", "}"]
    assert catalog.flown_step("f1", "Cfg::d1.fly", "Ms::trip.fly") == "action f1 :> Cfg::d1.fly, Ms::trip.fly;"
    with pytest.raises(catalog.CatalogError):
        catalog.mission_text("x", "Delivery", [("1bad", "Fly", {})])


def test_mission_simulations_are_tool_bound_defs_with_a_subject():
    graph = {
        "nodes": MISSION_GRAPH["nodes"]
        + [
            node("B__Sim", "verification def", "Sim", "B", tool={"toolName": "missionSimulator", "uri": "analysis://missionSimulator"}),
            node("B__Sim__drone", "subject", "drone", "B__Sim", typeId="B__Drone"),
            node("B__Sim__hoverPower", "param", "hoverPower", "B__Sim", direction="in", value="drone.hoverPower"),
            node("B__Sim__note", "param", "note", "B__Sim", direction="in"),  # no value: not sent
            node("B__Sim__duration", "param", "duration", "B__Sim", direction="out"),
            node("B__Calc", "calc def", "Calc", "B", tool={"toolName": "rangeEstimate"}),  # no subject: not a simulation
        ],
        "edges": MISSION_GRAPH["edges"] + [{"id": "v1", "kind": "verify", "source": "B__Sim", "target": "B__Energy"}],
    }
    cat = catalog.build(graph, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    [sim] = cat["simulations"]
    # a verification case: what it verifies, and the results it checks them with
    assert sim["outputs"] == ["duration"] and sim["verifies"] == ["B::Energy"]
    assert cat["requirements"][0]["verifiedBy"] == ["B::Sim"]
    assert sim["toolName"] == "missionSimulator" and sim["subject"] == "drone"
    assert sim["inputs"] == [{"name": "hoverPower", "value": "drone.hoverPower"}]
    assert cat["architectures"][0]["simulations"] == ["B::Sim"]


def test_simulation_evidence_is_found_with_its_metadata_values():
    block = catalog.evidence_text(
        "d1_trip", "B::Sim", "drone", "B::Cfg::d1",
        {"resultId": "r-1", "uri": "/api/models/results/r-1", "verdict": "holds", "verdicts": 'Energy "holds"'},
        "trip flown by d1",
    )
    assert block[:3] == ["verification d1_trip : B::Sim {", "    doc /* trip flown by d1 */", "    subject drone = B::Cfg::d1;"]
    assert '        verdicts = "Energy \\"holds\\"";' in block
    text = "package Ev {\n" + "".join(f"    {l}\n" for l in block) + "}\n"
    # the element spans lines 2..11 of Ev.sysml
    graph = {
        "nodes": MISSION_GRAPH["nodes"]
        + [
            node("B__Sim", "verification def", "Sim", "B", tool={"toolName": "missionSimulator"}),
            node("B__Sim__drone", "subject", "drone", "B__Sim", typeId="B__Drone"),
            node("Ev", "package", "Ev", file="Ev.sysml"),
            {**node("Ev__d1_trip", "verification", "d1_trip", "Ev", file="Ev.sysml", typeId="B__Sim", metadata=["SimulationEvidence"]),
             "loc": {"file": "Ev.sysml", "line": 2, "column": 5, "endLine": 1 + len(block), "endColumn": 6}},
            node("Ev__d1_trip__drone", "subject", "drone", "Ev__d1_trip", file="Ev.sysml", value="B::Cfg::d1"),
            node("Ev__plain", "verification", "plain", "Ev", file="Ev.sysml", typeId="B__Sim"),  # no metadata: not evidence
        ],
        "edges": MISSION_GRAPH["edges"],
    }
    cat = catalog.build(graph, {"Lib.sysml": {"id": "lib", "name": "Lib"}, "Ev.sysml": {"id": "ev", "name": "Ev"}}, {"Ev.sysml": text})
    [ev] = cat["evidence"]
    assert ev["simulation"] == "B::Sim" and ev["system"] == "B::Cfg::d1" and ev["modelName"] == "Ev"
    assert ev["values"] == {"resultId": "r-1", "uri": "/api/models/results/r-1", "verdict": "holds", "verdicts": 'Energy "holds"'}
    with pytest.raises(catalog.CatalogError):
        catalog.evidence_text("1bad", "B::Sim", "drone", "B::Cfg::d1", {})


def test_system_simulations_and_their_requirements():
    graph = {
        "nodes": MISSION_GRAPH["nodes"]
        + [
            node("B__SystemSimulation", "metadata def", "SystemSimulation", "B"),
            node("B__Ctl", "verification def", "Ctl", "B", tool={"toolName": "controlSimulator"}, metadata=["SystemSimulation"]),
            node("B__Ctl__drone", "subject", "drone", "B__Ctl", typeId="B__Drone"),
            node("B__Ctl__margin", "param", "margin", "B__Ctl", direction="out"),
            node("B__Tune", "analysis def", "Tune", "B", tool={"toolName": "controlSimulator"}, metadata=["SystemSimulation"]),
            node("B__Tune__drone", "subject", "drone", "B__Tune", typeId="B__Drone"),
            node("B__Stable", "requirement def", "Stable", "B"),
            node("B__Stable__drone", "subject", "drone", "B__Stable", typeId="B__Drone"),
            node("B__Stable__margin", "param", "margin", "B__Stable", direction="in"),
            node("B__ControlTest", "metadata def", "ControlTest", "B"),
            node("B__Climb", "requirement def", "Climb", "B", metadata=["ControlTest"]),
            node("B__Climb__drone", "subject", "drone", "B__Climb", typeId="B__Drone"),
            node("B__Climb__command", "attribute", "command", "B__Climb", value='"climbRate"'),
            node("B__Climb__step", "attribute", "step", "B__Climb", value="drone.takeoff.climbRate"),
            node("B__Climb__settlingTime", "param", "settlingTime", "B__Climb", direction="in"),
            node("B__Climb__settlingLimit", "attribute", "settlingLimit", "B__Climb"),
            # test instances: the limits set, one also overriding what it flies
            node("B__Spec", "package", "Spec", "B"),
            node("B__Spec__climb", "requirement", "climb", "B__Spec", typeId="B__Climb"),
            node("B__Spec__climb__settlingLimit", "attribute", "settlingLimit", "B__Spec__climb", value="4"),
            node("B__Spec__stable", "requirement", "stable", "B__Spec", typeId="B__Stable"),
            node("B__Spec__steep", "requirement", "steep", "B__Spec", typeId="B__Climb"),
            node("B__Spec__steep__step", "attribute", "step", "B__Spec__steep", value="1.5"),
        ],
        "edges": MISSION_GRAPH["edges"] + [
            {"id": "v1", "kind": "verify", "source": "B__Ctl", "target": "B__Stable"},
            {"id": "v2", "kind": "verify", "source": "B__Ctl", "target": "B__Climb"},
        ],
    }
    cat = catalog.build(graph, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    spec = {r["name"]: r for r in cat["specification"]}
    assert spec["climb"]["appliesTo"] == ["B::Cfg::d1"]
    # with d1 satisfying another climb test, that one replaces it for d1, flying its own step
    graph["edges"] = graph["edges"] + [{"id": "s1", "kind": "satisfy", "source": "B__Cfg__d1", "target": "B__Spec__steep"}]
    steep = {r["name"]: r for r in catalog.build(graph, {"Lib.sysml": {"id": "lib", "name": "Lib"}})["specification"]}
    assert steep["climb"]["appliesTo"] == [] and steep["steep"]["appliesTo"] == ["B::Cfg::d1"]
    assert steep["steep"]["flies"] == {"command": '"climbRate"', "step": "1.5"}
    sims = {s["name"]: s for s in cat["simulations"]}
    # a control test: its attributes say what to fly
    climb = next(r for r in cat["requirements"] if r["name"] == "Climb")
    assert climb["isTest"] and climb["attributes"] == {"command": '"climbRate"', "step": "drone.takeoff.climbRate"}
    assert not next(r for r in cat["requirements"] if r["name"] == "Stable")["isTest"]
    # a test instance flies what its type gives a value; the open attributes are its limits
    spec = {r["name"]: r for r in cat["specification"]}
    assert spec["climb"]["isTest"] and spec["climb"]["flies"] == {"command": '"climbRate"', "step": "drone.takeoff.climbRate"}
    assert spec["climb"]["measured"] == ["settlingTime"]
    assert not spec["stable"]["isTest"] and spec["stable"]["flies"] == {}
    assert (sims["Ctl"]["scope"], sims["Ctl"]["kind"]) == ("system", "verification def")
    assert (sims["Tune"]["scope"], sims["Tune"]["kind"]) == ("system", "analysis def")
    [arch] = cat["architectures"]
    assert arch["systemSimulations"] == ["B::Ctl", "B::Tune"] and arch["simulations"] == []
    # a requirement a system simulation verifies is a system requirement, not a mission one
    assert arch["systemRequirements"] == ["B::Stable", "B::Climb"]
    assert arch["missionRequirements"] == ["B::Energy"]
    assert cat["tags"]["SystemSimulation"] == "B::SystemSimulation"


def test_specification_hierarchy_parameters_and_applicability():
    nodes = GRAPH["nodes"] + [
        # requirement types on an abstract system and an abstract component
        node("R", "package", "R"),
        node("R__Weight", "requirement def", "Weight", "R", isAbstract=True),
        node("R__Weight__car", "subject", "car", "R__Weight", typeId="A__Car"),
        node("R__Weight__limit", "attribute", "limit", "R__Weight", typeName="MassValue"),
        node("R__Weight__margin", "attribute", "margin", "R__Weight", value="0"),
        node("R__GearCount", "requirement def", "GearCount", "R", isAbstract=True),
        node("R__GearCount__box", "subject", "box", "R__GearCount", typeId="L__Gearbox"),
        node("R__GearCount__minimum", "attribute", "minimum", "R__GearCount"),
        # the specification: a statement holding instances
        node("R__Spec", "package", "Spec", "R"),
        node("R__Spec__top", "requirement", "top", "R__Spec", shortName="STK-1", doc="Cars shall be good."),
        node("R__Spec__top__light", "requirement", "light", "R__Spec__top", shortName="SYS-1", typeId="R__Weight", typeName="Weight"),
        node("R__Spec__top__light__limit", "attribute", "limit", "R__Spec__top__light", value="1500 [SI::kg]"),
        node("R__Spec__top__lighter", "requirement", "lighter", "R__Spec__top", shortName="SYS-2", typeId="R__Weight", typeName="Weight"),
        node("R__Spec__top__gears", "requirement", "gears", "R__Spec__top", shortName="CMP-1", typeId="R__GearCount", typeName="GearCount"),
        # a verify usage in a verification objective is not part of the specification
        node("R__Test", "verification def", "Test", "R"),
        node("R__Test__obj", "requirement", "obj", "R__Test"),
        node("R__Test__obj__light", "requirement", "light", "R__Test__obj", typeId="R__Weight"),
    ]
    edges = GRAPH["edges"] + [{"id": "s9", "kind": "satisfy", "source": "A__base", "target": "R__Spec__top__lighter"}]
    cat = catalog.build({"nodes": nodes, "edges": edges}, FILES)
    spec = {r["name"]: r for r in cat["specification"]}
    assert list(spec) == ["top", "light", "lighter", "gears"]
    top, light, lighter, gears = spec["top"], spec["light"], spec["lighter"], spec["gears"]
    assert (top["level"], top["type"], top["parent"], top["shortName"]) == ("statement", None, None, "STK-1")
    assert light["parent"] == "R::Spec::top" and light["type"] == "R::Weight" and light["isAbstractType"]
    params = {p["name"]: (p["value"], p["bound"]) for p in light["parameters"]}
    assert params == {"limit": ("1500 [SI::kg]", True), "margin": ("0", False)}
    # applicability: every system the subject type fits, or what satisfy names
    # ... except that an instance a system satisfies replaces, for it, those of the same type
    assert light["level"] == "system" and light["appliesTo"] == [] and not light["explicit"]
    assert light["replacedFor"] == {"A::base": "R::Spec::top::lighter"}
    assert lighter["explicit"] and lighter["appliesTo"] == ["A::base"]
    assert gears["level"] == "component" and gears["appliesTo"] == ["L::Cat::m6"]  # a Manual is a Gearbox


def test_missions_as_use_cases_with_objective_requirements_and_assignments():
    nodes = MISSION_GRAPH["nodes"] + [
        node("B__Time", "requirement def", "Time", "B", isAbstract=True),
        node("B__Time__drone", "subject", "drone", "B__Time", typeId="B__Drone"),
        node("B__Time__limit", "attribute", "limit", "B__Time"),
        node("B__Time__duration", "param", "duration", "B__Time", direction="in"),
        # a use-case mission type: the drone as subject, requirements in its objective
        node("B__Run", "use case def", "Run", "B", metadata=["Mission"]),
        node("B__Run__duration", "attribute", "duration", "B__Run", typeName="Real"),
        node("B__Run__goals", "requirement", "goals", "B__Run"),
        node("B__Run__goals__onTime", "requirement", "onTime", "B__Run__goals", typeId="B__Time", shortName="MIS-1"),
        node("B__Run__goals__onTime__limit", "attribute", "limit", "B__Run__goals__onTime", value="1800"),
        # a mission adding a requirement of its own in its (redefined) objective
        node("B__Ms__far", "use case", "far", "B__Ms", typeId="B__Run", typeName="Run"),
        node("B__Ms__far__goals", "requirement", "goals", "B__Ms__far", redefines="B__Run__goals"),
        node("B__Ms__far__goals__window", "requirement", "window", "B__Ms__far__goals", typeId="B__Time", shortName="MIS-F1"),
        node("B__Ms__far__fly", "action", "fly", "B__Ms__far", typeId="B__Fly", typeName="Fly"),
    ]
    edges = MISSION_GRAPH["edges"] + [{"id": "a1", "kind": "allocation", "source": "B__Ms__far", "target": "B__Cfg__d1", "loc": None}]
    cat = catalog.build({"nodes": nodes, "edges": edges}, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    run = next(t for t in cat["missionTypes"] if t["name"] == "Run")
    assert run["kind"] == "use case def" and run["requirements"] == ["B::Run::goals::onTime"]
    far = next(m for m in cat["missions"] if m["name"] == "far")
    assert far["missionType"] == "B::Run" and [s["name"] for s in far["steps"]] == ["fly"]
    assert far["requirements"] == ["B::Ms::far::goals::window"] and far["assignedTo"] == ["B::Cfg::d1"]
    reqs = {r["name"]: r for r in cat["missionRequirements"]}
    assert reqs["onTime"]["owner"] == "B::Run" and reqs["onTime"]["measured"] == ["duration"]
    assert {p["name"]: p["value"] for p in reqs["onTime"]["parameters"]} == {"limit": "1800"}
    assert reqs["window"]["owner"] == "B::Ms::far" and reqs["window"]["architectures"] == ["B::Drone"]
    assert cat["assignments"] == [{"mission": "B::Ms::far", "system": "B::Cfg::d1", "loc": None}]
    # objective requirements are not part of the specification
    assert not any(r["name"] in ("onTime", "window", "goals") for r in cat["specification"])


def test_use_case_mission_text_keeps_its_objective():
    obj = ["objective :>> goals {", "    requirement window : Time { attribute :>> limit = 1500; }", "}"]
    text = catalog.mission_text("far", "Run", [("fly", "Fly", {"distance": "2000"})], "8 km", "use case", obj)
    assert text[0] == "use case far : Run {" and text[1] == "    doc /* 8 km */"
    assert text[2:5] == ["    " + l for l in obj] and text[5] == "    first start;"


# ------------------------------------------------------------------ sub-parts and component analyses
PROP_GRAPH = {
    "nodes": [
        node("P", "package", "P"),
        node("P__Material", "part def", "Material", "P", metadata=["Component"]),
        node("P__Material__density", "attribute", "density", "P__Material", typeName="Real"),
        node("P__Prop", "part def", "Prop", "P", metadata=["Component"]),
        node("P__Prop__ct0", "attribute", "ct0", "P__Prop", typeName="Real"),
        node("P__Prop__material", "part", "material", "P__Prop", typeId="P__Material", typeName="Material"),
        node("P__Cat", "package", "Cat", "P"),
        node("P__Cat__nylon", "part", "nylon", "P__Cat", typeId="P__Material", typeName="Material"),
        node("P__Cat__p1", "part", "p1", "P__Cat", typeId="P__Prop", typeName="Prop"),
        node("P__Cat__p1__material", "part", "material", "P__Cat__p1", subsets="P__Cat__nylon", redefines="P__Prop__material"),
        node("P__Cat__p2", "part", "p2", "P__Cat", typeId="P__Prop", typeName="Prop"),
        node("P__Aero", "analysis def", "Aero", "P", tool={"toolName": "propellerAero"}),
        node("P__Aero__prop", "subject", "prop", "P__Aero", typeId="P__Prop", typeName="Prop"),
        node("P__Aero__diameter", "param", "diameter", "P__Aero", direction="in", value="prop.diameter"),
        node("P__Aero__ct0", "param", "ct0", "P__Aero", direction="out"),
    ],
    "edges": [],
}


def test_instances_fill_component_typed_parts_and_component_analyses():
    cat = catalog.build(PROP_GRAPH, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    comps = {c["name"]: c for c in cat["components"]}
    [mat] = comps["Prop"]["parts"]
    assert mat["name"] == "material" and mat["candidates"] == ["P__Cat__nylon"]
    inst = {i["name"]: i for i in cat["instances"]}
    assert inst["p1"]["parts"] == {"material": "P::Cat::nylon"} and inst["p2"]["parts"] == {"material": None}
    # a tool-bound def with a component subject is a component analysis, not a mission simulation
    assert cat["simulations"] == []
    [a] = cat["componentAnalyses"]
    assert a["inputs"] == [{"name": "diameter", "value": "prop.diameter"}] and a["outputs"] == ["ct0"]
    assert comps["Prop"]["analyses"] == ["P::Aero"] and comps["Material"]["analyses"] == []
    assert catalog.instance_text("p3", "Prop", {"ct0": "0.1"}, parts={"material": "Cat::nylon"}) == [
        "part p3 : Prop {", "    attribute :>> ct0 = 0.1;", "    part :>> material :> Cat::nylon;", "}",
    ]
