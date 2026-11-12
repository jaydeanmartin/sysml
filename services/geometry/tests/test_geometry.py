"""Geometry generation and the GLB writer (no model service needed)."""

import json
import math
import struct

import pytest

from app import generators, gltf


def inst(qn, component, **values):
    return {"qualifiedName": qn, "component": component, "values": {k: str(v) for k, v in values.items()}}


def catalog(rotors="[4]", wheelbase=700, prop=15, payload=None):
    payload = payload or inst("L::Cat::box", "L::ParcelBox", mass="0.2 [SI::kg]", volume=8)
    comps = [
        {"qualifiedName": q, "specializes": s}
        for q, s in [("L::Frame", []), ("L::Motor", []), ("L::Propeller", []), ("L::Battery", []), ("L::PayloadModule", []),
                     ("L::ParcelBox", ["L::PayloadModule"]), ("L::Winch", ["L::PayloadModule"])]
    ]
    instances = [
        inst("L::Cat::frame", "L::Frame", wheelbase=wheelbase, hubMass="0.5 [SI::kg]"),
        inst("L::Cat::motor", "L::Motor", mass="0.2 [SI::kg]"),
        inst("L::Cat::prop", "L::Propeller", diameter=prop),
        inst("L::Cat::bat", "L::Battery", mass="1.4 [SI::kg]"),
        payload,
    ]
    slots = [("airframe", "L::Frame", None), ("battery", "L::Battery", None), ("motors", "L::Motor", rotors),
             ("propellers", "L::Propeller", rotors), ("payload", "L::PayloadModule", None)]
    return {
        "components": comps,
        "instances": instances,
        "architectures": [{"id": "A", "qualifiedName": "A::Quad", "slots": [{"name": n, "type": t, "multiplicity": m} for n, t, m in slots]}],
        "configurations": [{
            "qualifiedName": "A::Cfg::d1", "architectureId": "A", "architecture": "A::Quad",
            "assignments": {"airframe": "L::Cat::frame", "battery": "L::Cat::bat", "motors": "L::Cat::motor",
                            "propellers": "L::Cat::prop", "payload": payload["qualifiedName"]},
        }],
    }


def parse_glb(data: bytes) -> dict:
    magic, version, total = struct.unpack_from("<4sII", data)
    assert (magic, version, total) == (b"glTF", 2, len(data))
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    assert jtype == b"JSON"
    doc = json.loads(data[20 : 20 + jlen])
    blen, btype = struct.unpack_from("<I4s", data, 20 + jlen)
    assert btype == b"BIN\x00" and blen == doc["buffers"][0]["byteLength"]
    return doc


def test_multirotor_has_a_part_per_rotor_and_metrics():
    res = generators.build(catalog(), "A::Cfg::d1")
    assert res.generator == "multirotor"
    names = [p.name for p in res.scene.parts]
    assert sum(n.startswith("motors[") for n in names) == 4
    assert sum(n.startswith("propellers[") and not n.endswith("/disc") for n in names) == 4
    assert "battery" in names and "payload" in names
    assert res.metrics["rotors"] == 4
    assert res.metrics["wheelbase"] == pytest.approx(0.7)
    # 0.7 m wheelbase, 4 rotors: motors 0.495 m apart; 15 in = 0.381 m propellers
    assert res.metrics["tipClearance"] == pytest.approx(0.7 * math.sin(math.pi / 4) - 0.381)
    assert not res.warnings


def test_blades_follow_the_stations_and_the_frame_its_tubes():
    cat = catalog()
    frame, _, prop = cat["instances"][:3]
    prop["values"].update(stations="(0.2, 0.6, 1)", chord="(0.15, 0.12, 0.05)", twist="(30, 12, 6)", bladeCount="3", hubRadius="0.12")
    frame["values"].update(armOuterDiameter="22", hubDiameter="220")
    res = generators.build(cat, "A::Cfg::d1")
    prop1 = next(p for p in res.scene.parts if p.name == "propellers[1]")
    hubcap = gltf.cylinder((0, 0, 0), (0, 1, 0), 1, 16)
    # 3 blades, each 4 sections (hub root + 3 stations) of 2 vertices, plus the hubcap
    assert len(prop1.mesh.positions) == 3 * 4 * 2 + len(hubcap.positions)
    # the tip of each blade at the propeller radius from the rotor axis
    arm = next(p for p in res.scene.parts if p.name == "airframe/arm1")
    assert max(abs(v[0]) for v in arm.mesh.positions) < 0.7 / 2 + 0.012  # 11 mm tube radius
    xs = [math.hypot(v[0], v[2]) for v in next(p for p in res.scene.parts if p.name == "airframe/hub").mesh.positions]
    assert max(xs) == pytest.approx(0.11, abs=1e-6)
    assert generators.sequence({"twist": "(30, 12.5, -1e-1)"}, "twist") == [30, 12.5, -0.1]
    # a twisted blade: the chord line rises towards the leading edge, more at the root
    blade = generators.blade_surface((0, 0, 0), 0.0, 1.0, [0.2, 1.0], [0.1, 0.1], [30, 6], 0.2)
    rise = [blade.positions[i][1] - blade.positions[i + 1][1] for i in (0, 2)]
    assert rise[0] > rise[1] > 0


def test_overlapping_propellers_are_flagged():
    res = generators.build(catalog(wheelbase=450, prop=15), "A::Cfg::d1")
    assert res.metrics["tipClearance"] < 0
    assert any("overlap" in w for w in res.warnings)
    assert {p.material for p in res.scene.parts if p.name.endswith("/disc")} == {"disc-clash"}


def test_rotor_count_follows_the_multiplicity_and_winch_payload():
    winch = inst("L::Cat::winch", "L::Winch", mass="1.6 [SI::kg]", capacity="10 [SI::kg]", tetherLength=20)
    res = generators.build(catalog(rotors="[8]", wheelbase=1600, prop=22, payload=winch), "A::Cfg::d1")
    names = [p.name for p in res.scene.parts]
    assert sum(n.startswith("motors[") for n in names) == 8
    assert {"payload/drum", "payload/tether", "payload/hook"} <= set(names)


def test_generic_layout_for_other_architectures():
    cat = catalog()
    cat["architectures"][0]["slots"] = [{"name": "battery", "type": "L::Battery", "multiplicity": None}, {"name": "spare", "type": "L::Motor", "multiplicity": "[2]"}]
    cat["configurations"][0]["assignments"] = {"battery": "L::Cat::bat", "spare": None}
    res = generators.build(cat, "A::Cfg::d1")
    assert res.generator == "generic"
    assert [p.name for p in res.scene.parts] == ["battery", "spare[1]", "spare[2]"]
    assert res.warnings == ["spare is not chosen"]


def test_unknown_system():
    with pytest.raises(generators.GeometryError):
        generators.build(catalog(), "A::Cfg::nope")


def test_glb_is_valid_gltf_with_extras():
    res = generators.build(catalog(), "A::Cfg::d1")
    doc = parse_glb(res.scene.to_glb({"generator": res.generator}))
    assert doc["scenes"][0]["extras"] == {"generator": "multirotor"}
    assert len(doc["nodes"]) == len(res.scene.parts) == len(doc["meshes"])
    motor = next(n for n in doc["nodes"] if n["name"] == "motors[1]")
    assert motor["extras"]["instance"] == "L::Cat::motor" and motor["extras"]["index"] == 0
    for a in doc["accessors"]:
        v = doc["bufferViews"][a["bufferView"]]
        assert v["byteOffset"] % 4 == 0
        assert v["byteLength"] == a["count"] * (12 if a["type"] == "VEC3" else 4)


def test_box_faces_point_outwards():
    m = gltf.box((0, 0, 0), (2, 1, 3), yaw=0.3)
    for t in range(0, len(m.indices), 3):
        a, b, c = (m.positions[i] for i in m.indices[t : t + 3])
        n = gltf.cross(gltf.sub(b, a), gltf.sub(c, a))
        centroid = gltf.scale(gltf.add(gltf.add(a, b), c), 1 / 3)
        assert sum(n[k] * centroid[k] for k in range(3)) > 0  # the box is centred on the origin


def test_cylinder_faces_point_outwards():
    m = gltf.cylinder((0, -1, 0), (0, 1, 0), 0.5, segments=12)
    for t in range(0, len(m.indices), 3):
        a, b, c = (m.positions[i] for i in m.indices[t : t + 3])
        n = gltf.cross(gltf.sub(b, a), gltf.sub(c, a))
        centroid = gltf.scale(gltf.add(gltf.add(a, b), c), 1 / 3)
        assert sum(n[k] * centroid[k] for k in range(3)) > 0
