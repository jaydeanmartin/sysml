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


# ------------------------------------------------------------------ requirement library, usages and tests
REQ_GRAPH = {
    "nodes": [
        node("R", "package", "R"),
        node("R__Component", "metadata def", "Component", "R"),
        node("R__SystemArchitecture", "metadata def", "SystemArchitecture", "R"),
        # library: abstract, an attribute without a value (minimum) left for each instance
        node("R__Min", "requirement def", "Min", "R", shortName="LIB-1", isAbstract=True, doc="at least {minimum}", constraints=[{"kind": "require", "text": "actual >= minimum"}]),
        node("R__Min__actual", "attribute", "actual", "R__Min", typeName="Real"),
        node("R__Min__minimum", "attribute", "minimum", "R__Min", typeName="Real"),
        # a composite requirement def with a nested library requirement
        node("R__Perf", "requirement def", "Perf", "R", shortName="PERF-1"),
        node("R__Perf__payload", "requirement", "payload", "R__Perf", typeId="R__Min", typeName="Min"),
        node("R__Battery", "part def", "Battery", "R", metadata=["Component"]),
        node("R__Battery__energy", "attribute", "energy", "R__Battery", typeName="Real"),
        node("R__Battery__cap", "requirement", "cap", "R__Battery", typeId="R__Min", typeName="Min"),
        node("R__Battery__cap__actual", "attribute", "actual", "R__Battery__cap", value="energy", redefines="R__Min__actual"),
        node("R__Cat", "package", "Cat", "R"),
        node("R__Cat__b1", "part", "b1", "R__Cat", typeId="R__Battery", typeName="Battery"),
        node("R__Cat__b1__cap", "requirement", "cap", "R__Cat__b1", redefines="R__Battery__cap"),
        node("R__Cat__b1__cap__minimum", "attribute", "minimum", "R__Cat__b1__cap", value="100", redefines="R__Min__minimum"),
        node("R__Cat__b2", "part", "b2", "R__Cat", typeId="R__Battery", typeName="Battery"),  # states no number
        node("R__Drone", "part def", "Drone", "R", metadata=["SystemArchitecture"]),
        node("R__Drone__battery", "part", "battery", "R__Drone", typeId="R__Battery", typeName="Battery"),
        node("R__Drone__perf", "requirement", "perf", "R__Drone", typeId="R__Perf", typeName="Perf"),
        node("R__Drone__perf__payload", "requirement", "payload", "R__Drone__perf"),
        node("R__Drone__perf__payload__actual", "attribute", "actual", "R__Drone__perf__payload", value="cap"),
        node("R__Cfg", "package", "Cfg", "R"),
        node("R__Cfg__d1", "part", "d1", "R__Cfg", typeId="R__Drone", typeName="Drone"),
        node("R__Cfg__d1__perf", "requirement", "perf", "R__Cfg__d1"),
        node("R__Cfg__d1__perf__payload", "requirement", "payload", "R__Cfg__d1__perf"),
        node("R__Cfg__d1__perf__payload__minimum", "attribute", "minimum", "R__Cfg__d1__perf__payload", value="2"),
        # tests: one verifies a usage (through its subject), one a def
        node("R__Bench", "verification def", "Bench", "R"),
        node("R__Bench__battery", "subject", "battery", "R__Bench", typeId="R__Battery", typeName="Battery"),
        node("R__Weigh", "verification def", "Weigh", "R"),
        node("R__Weigh__drone", "subject", "drone", "R__Weigh", typeId="R__Drone", typeName="Drone"),
    ],
    "edges": [
        {"id": "v1", "kind": "verify", "source": "R__Bench", "target": "R__Battery__cap"},
        {"id": "v2", "kind": "verify", "source": "R__Weigh", "target": "R__Min"},
    ],
}


@pytest.fixture(scope="module")
def rcat():
    return catalog.build(REQ_GRAPH, {"Lib.sysml": {"id": "lib", "name": "Lib"}})


def test_library_requirements_are_templates_not_applied_by_subject(rcat):
    reqs = {r["name"]: r for r in rcat["requirements"]}
    assert reqs["Min"]["template"] and reqs["Min"]["subjectTypeId"] is None
    assert [t["name"] for t in reqs["Min"]["thresholds"]] == ["actual", "minimum"]
    assert reqs["Perf"]["children"] == [{"name": "payload", "type": "Min"}]
    assert reqs["Min"]["tests"] == ["R::Weigh"]
    [arch] = rcat["architectures"]
    assert arch["requirements"] == [] and arch["missionRequirements"] == []


def test_requirement_usages_of_defs_and_instances(rcat):
    comps = {c["name"]: c for c in rcat["components"]}
    [cap] = comps["Battery"]["requirementUsages"]
    # the def binds what it constrains; the minimum is left for each instance
    assert cap["def"] == "R::Min" and cap["values"] == {"actual": "energy"} and cap["missing"] == ["minimum"]
    assert cap["declarations"] == ["R::Battery::cap"]
    inst = {i["name"]: i for i in rcat["instances"]}
    [b1cap] = inst["b1"]["requirementUsages"]
    assert b1cap["values"] == {"actual": "energy", "minimum": "100"} and b1cap["ownValues"] == {"minimum": "100"}
    assert b1cap["missing"] == [] and b1cap["bound"] == ["actual", "minimum"]
    assert inst["b1"]["missingRequirementValues"] == []
    assert inst["b2"]["missingRequirementValues"] == ["cap.minimum"]
    assert catalog.own_requirement_values(inst["b1"]["requirementUsages"]) == {"cap.minimum": "100"}


def test_nested_requirement_usages(rcat):
    [arch] = rcat["architectures"]
    [perf] = arch["requirementUsages"]
    [payload] = perf["children"]
    assert payload["path"] == "perf.payload" and payload["def"] == "R::Min"
    assert payload["values"] == {"actual": "cap"} and payload["missing"] == ["minimum"]
    # declared in the requirement def, redefined in the architecture
    assert payload["declarations"] == ["R::Perf::payload", "R::Drone::perf::payload"]
    [cfg] = rcat["configurations"]
    assert cfg["missingRequirementValues"] == []
    assert catalog.own_requirement_values(cfg["requirementUsages"]) == {"perf.payload.minimum": "2"}
    # values a draft would state
    [drafted] = catalog.apply_values(arch["requirementUsages"], {"perf.payload.minimum": "3"})
    assert drafted["children"][0]["values"]["minimum"] == "3" and drafted["children"][0]["missing"] == []


def test_tests_verify_usages_and_defs_for_their_subject_type(rcat):
    tests = {t["name"]: t for t in rcat["tests"]}
    assert tests["Bench"]["method"] == "analysis" and tests["Bench"]["verifies"] == ["R::Battery::cap"]
    comps = {c["name"]: c for c in rcat["components"]}
    [cap] = comps["Battery"]["requirementUsages"]
    found = catalog.verifying_tests(rcat["tests"], cap["defLineage"], cap["declarations"], comps["Battery"]["lineageIds"])
    # Bench names the usage; Weigh verifies Min, but only with a Drone as subject
    assert [t["name"] for t in found] == ["Bench"]
    [arch] = rcat["architectures"]
    payload = arch["requirementUsages"][0]["children"][0]
    found = catalog.verifying_tests(rcat["tests"], payload["defLineage"], payload["declarations"], arch["lineageIds"])
    assert [t["name"] for t in found] == ["Weigh"]


def test_requirement_values_text():
    assert catalog.requirements_text({"cap.minimum": "100"}) == ["requirement :>> cap { attribute :>> minimum = 100; }"]
    assert catalog.requirements_text({"perf.payload.minimum": "2 [SI::kg]", "perf.mass.maximum": "25", "perf.limit": "1"}) == [
        "requirement :>> perf {",
        "    attribute :>> limit = 1;",
        "    requirement :>> payload { attribute :>> minimum = 2 [SI::kg]; }",
        "    requirement :>> mass { attribute :>> maximum = 25; }",
        "}",
    ]
    assert catalog.configuration_text("d1", "Drone", {}, requirements={"perf.limit": "3"}) == [
        "part d1 : Drone {",
        "    requirement :>> perf { attribute :>> limit = 3; }",
        "}",
    ]
    # an instance states numbers, not expressions
    for bad in ("energy * 2", "1; part x"):
        with pytest.raises(catalog.CatalogError):
            catalog.requirements_text({"cap.minimum": bad})
    # a usage declared with an id is redefined restating it
    assert catalog.requirements_text({"cap.minimum": "100", "perf.payload.minimum": "2"}, {"cap": "BAT-01", "perf.payload": "P-1"}) == [
        "requirement <'BAT-01'> cap :>> cap { attribute :>> minimum = 100; }",
        "requirement :>> perf {",
        "    requirement <'P-1'> payload :>> payload { attribute :>> minimum = 2; }",
        "}",
    ]
    assert catalog.requirement_usage_text("cap", "Min", {"actual": "energy"}) == ["requirement cap : Min { attribute :>> actual = energy; }"]
    assert catalog.check_def_text("r1", "R::Min", "R::Cat::b1.cap", ["actual", "minimum"]) == (
        "requirement def r1 :> R::Min { attribute :>> actual = R::Cat::b1.cap.actual; attribute :>> minimum = R::Cat::b1.cap.minimum; }"
    )


def test_requirement_ids_are_short_names(rcat):
    reqs = {r["name"]: r for r in rcat["requirements"]}
    assert reqs["Min"]["shortId"] == "LIB-1" and reqs["Perf"]["shortId"] == "PERF-1"
    [arch] = rcat["architectures"]
    [perf] = arch["requirementUsages"]
    # a usage without a short name of its own keeps its def's id apart
    assert perf["shortId"] is None and perf["defShortId"] == "PERF-1"
    # a nested one is numbered from its parent's id
    assert perf["children"][0]["shortId"] == "PERF-1.1" and perf["children"][0]["defShortId"] == "LIB-1"
    comps = {c["name"]: c for c in rcat["components"]}
    assert comps["Battery"]["requirementUsages"][0]["shortId"] is None  # top level: never numbered


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
