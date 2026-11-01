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
