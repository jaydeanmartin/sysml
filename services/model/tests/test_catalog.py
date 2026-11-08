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
        ],
        "edges": MISSION_GRAPH["edges"] + [
            {"id": "v1", "kind": "verify", "source": "B__Ctl", "target": "B__Stable"},
            {"id": "v2", "kind": "verify", "source": "B__Ctl", "target": "B__Climb"},
        ],
    }
    cat = catalog.build(graph, {"Lib.sysml": {"id": "lib", "name": "Lib"}})
    sims = {s["name"]: s for s in cat["simulations"]}
    # a control test: its attributes say what to fly
    climb = next(r for r in cat["requirements"] if r["name"] == "Climb")
    assert climb["isTest"] and climb["attributes"] == {"command": '"climbRate"', "step": "drone.takeoff.climbRate"}
    assert not next(r for r in cat["requirements"] if r["name"] == "Stable")["isTest"]
    assert (sims["Ctl"]["scope"], sims["Ctl"]["kind"]) == ("system", "verification def")
    assert (sims["Tune"]["scope"], sims["Tune"]["kind"]) == ("system", "analysis def")
    [arch] = cat["architectures"]
    assert arch["systemSimulations"] == ["B::Ctl", "B::Tune"] and arch["simulations"] == []
    # a requirement a system simulation verifies is a system requirement, not a mission one
    assert arch["systemRequirements"] == ["B::Stable", "B::Climb"]
    assert arch["missionRequirements"] == ["B::Energy"]
    assert cat["tags"]["SystemSimulation"] == "B::SystemSimulation"
