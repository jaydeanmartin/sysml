"""Geometry of a system instance, generated from the project's catalog.

The catalog (from the model service) says which component instance fills each
slot of the system instance and gives the instance's attribute values. A
generator turns those into parts of a ``gltf.Scene``:

* ``multirotor``: an architecture with slots typed by a ``Frame``, a ``Motor``
  and a ``Propeller`` (or specializations of them). Uses the frame's
  ``wheelbase`` (mm), the propeller ``diameter`` (in), the number of motors
  (the slot multiplicity), the masses of motors and battery, and the payload's
  ``volume`` (l, a box) or ``tetherLength`` (m, a winch);
* ``generic``: any other architecture. One box per slot (and per element of a
  multiple slot), sized by the instance's ``mass``.

Values are SysML expressions as the catalog gives them ('0.35 [SI::kg]',
'450', '[12]'); the first number is used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .gltf import Material, Mesh, Scene, box, cylinder, hex_color, merge

_NUM = re.compile(r"-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")
MAX_COPIES = 12  # elements drawn for a slot with an open multiplicity


class GeometryError(ValueError):
    """The request names no system instance of the project (HTTP 404)."""


def number(values: dict[str, str], name: str, default: float | None = None) -> float | None:
    m = _NUM.search(values.get(name) or "")
    return float(m.group(0)) if m else default


def copies(multiplicity: str | None) -> int:
    """Elements of a slot: '[4]' -> 4, '[2..6]' -> 2, none -> 1, '[1..*]' -> 1."""
    nums = [int(n) for n in re.findall(r"\d+", multiplicity or "")]
    return max(1, min(nums[0], MAX_COPIES)) if nums else 1


@dataclass
class SlotFill:
    name: str
    type: str
    lineage: set[str]  # simple names of the slot type and every type it specializes
    count: int
    instance: dict[str, Any] | None  # catalog instance, None when the slot is open

    @property
    def values(self) -> dict[str, str]:
        return (self.instance or {}).get("values") or {}

    def extras(self, index: int | None = None) -> dict[str, Any]:
        inst = self.instance or {}
        return {
            "slot": self.name,
            "index": index,
            "type": self.type,
            "instance": inst.get("qualifiedName"),
            "component": inst.get("component"),
            "values": inst.get("values") or {},
        }


@dataclass
class Result:
    scene: Scene
    generator: str
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _lineage(catalog: dict[str, Any], qn: str) -> set[str]:
    comps = {c["qualifiedName"]: c for c in catalog.get("components", [])}
    out: set[str] = set()
    todo = [qn]
    while todo:
        q = todo.pop()
        if q in out:
            continue
        out.add(q)
        todo += comps.get(q, {}).get("specializes", [])
    return {q.rsplit("::", 1)[-1] for q in out}


def system_slots(catalog: dict[str, Any], system_qn: str) -> tuple[dict[str, Any], dict[str, Any], list[SlotFill]]:
    cfg = next((c for c in catalog.get("configurations", []) if c["qualifiedName"] == system_qn), None)
    if not cfg:
        raise GeometryError(f"system instance '{system_qn}' not found")
    arch = next(a for a in catalog["architectures"] if a["id"] == cfg["architectureId"])
    insts = {i["qualifiedName"]: i for i in catalog.get("instances", [])}
    fills = []
    for s in arch["slots"]:
        inst = insts.get(cfg["assignments"].get(s["name"]) or "")
        fills.append(SlotFill(s["name"], s["type"], _lineage(catalog, s["type"]), copies(s.get("multiplicity")), inst))
    return cfg, arch, fills


def build(catalog: dict[str, Any], system_qn: str) -> Result:
    cfg, arch, fills = system_slots(catalog, system_qn)
    roles = {r: next((f for f in fills if r in f.lineage), None) for r in ("Frame", "Motor", "Propeller", "Battery", "PayloadModule")}
    if roles["Frame"] and roles["Motor"] and roles["Propeller"]:
        res = multirotor(roles, [f for f in fills if f not in roles.values()])
    else:
        res = generic(fills)
    lo, hi = res.scene.bounds()
    res.metrics |= {"length": hi[0] - lo[0], "height": hi[1] - lo[1], "width": hi[2] - lo[2]}
    return res


# ------------------------------------------------------------------ multirotor
CARBON = Material(hex_color("#30343b"), metallic=0.3, roughness=0.45)
MOTOR = Material(hex_color("#c2410c"), metallic=0.7, roughness=0.3)
PROP = Material(hex_color("#e5e7eb"), roughness=0.4, double_sided=True)
DISC = Material(hex_color("#60a5fa", 0.18), roughness=0.2, double_sided=True)
DISC_CLASH = Material(hex_color("#ef4444", 0.35), roughness=0.2, double_sided=True)
BATTERY = Material(hex_color("#2563eb"), metallic=0.1, roughness=0.5)
CARDBOARD = Material(hex_color("#b8875a"), roughness=0.9)
WINCH = Material(hex_color("#52525b"), metallic=0.6, roughness=0.35)
TETHER = Material(hex_color("#facc15"), roughness=0.6)
GEAR = Material(hex_color("#6b7280"), metallic=0.4, roughness=0.5)
PLACEHOLDER = Material(hex_color("#9ca3af", 0.35), roughness=0.8)


def multirotor(roles: dict[str, SlotFill | None], others: list[SlotFill]) -> Result:
    frame, motor, prop = roles["Frame"], roles["Motor"], roles["Propeller"]
    battery, payload = roles["Battery"], roles["PayloadModule"]
    assert frame and motor and prop
    sc = Scene()
    res = Result(sc, "multirotor")
    for f in (frame, motor, prop, battery, payload):
        if f and not f.instance:
            res.warnings.append(f"{f.name} is not chosen: drawn with default dimensions")

    n = motor.count
    wheelbase = number(frame.values, "wheelbase", 500) / 1000  # m, motor to motor across
    radius = wheelbase / 2
    prop_d = number(prop.values, "diameter", 10) * 0.0254  # m
    motor_m = number(motor.values, "mass", 0.15)
    hub_r = max(0.06, 0.16 * wheelbase)
    hub_h = max(0.02, 0.04 * wheelbase)
    arm_r = max(0.007, 0.018 * wheelbase)
    motor_r = 0.022 * (motor_m / 0.1) ** (1 / 3)
    motor_h = 1.1 * motor_r

    # payload size first: the landing gear must clear it
    pay_h, pay_draw = 0.0, None
    if payload:
        vals = payload.values
        if "tetherLength" in vals:
            drum_r = 0.05 + 0.004 * number(vals, "capacity", 5)
            pay_h = 2 * drum_r + 0.02
            pay_draw = ("winch", drum_r)
        else:
            vol = (number(vals, "volume", 0) or 0) / 1000  # m³
            if not vol:
                vol = max(0.001, number(vals, "mass", 0.2) / 100)
            side = (vol / (1.6 * 1.0 * 0.8)) ** (1 / 3)
            pay_h = 0.8 * side
            pay_draw = ("box", (1.6 * side, 0.8 * side, 1.0 * side))
    leg_h = max(0.12 * wheelbase + 0.05, pay_h + 0.04)
    hub_y = leg_h + hub_h / 2  # hub centre above the ground

    # hub, arms, motors, propellers
    sc.add("airframe/hub", cylinder((0, hub_y - hub_h / 2, 0), (0, hub_y + hub_h / 2, 0), hub_r), sc.material("carbon", CARBON), **frame.extras())
    spacing = 2 * radius * math.sin(math.pi / n)  # between adjacent motors
    clearance = spacing - prop_d
    disc_mat = sc.material("disc-clash", DISC_CLASH) if clearance < 0 and n > 1 else sc.material("disc", DISC)
    if clearance < 0 and n > 1:
        res.warnings.append(f"adjacent propeller tips overlap by {-clearance * 1000:.0f} mm")
    for i in range(n):
        a = 2 * math.pi * i / n + math.pi / n  # X layout: no arm straight ahead
        x, z = radius * math.cos(a), radius * math.sin(a)
        sc.add(f"airframe/arm{i + 1}", cylinder((0, hub_y, 0), (x, hub_y, z), arm_r, 16), "carbon", **frame.extras())
        top = hub_y + arm_r + motor_h
        sc.add(f"motors[{i + 1}]", cylinder((x, hub_y + arm_r, z), (x, top, z), motor_r, 24), sc.material("motor", MOTOR), **motor.extras(i))
        y = top + 0.012
        blade_w = max(0.012, 0.09 * prop_d)
        blades = box((x, y, z), (prop_d, 0.004, blade_w), yaw=a)  # two blades: one bar through the hub
        hubcap = cylinder((x, top, z), (x, y + 0.006, z), max(0.008, 0.25 * motor_r), 16)
        sc.add(f"propellers[{i + 1}]", merge(blades, hubcap), sc.material("prop", PROP), **prop.extras(i))
        sc.add(f"propellers[{i + 1}]/disc", cylinder((x, y - 0.001, z), (x, y + 0.001, z), prop_d / 2, 48), disc_mat, **prop.extras(i))

    # battery on top of the hub
    if battery:
        mass = number(battery.values, "mass", 1.0)
        vol = mass / 2000  # m³ at ~2 kg/l (LiPo)
        h = (vol / (2.2 * 1.0 * 0.7)) ** (1 / 3)
        size = (2.2 * h, 0.7 * h, 1.0 * h)
        sc.add("battery", box((0, hub_y + hub_h / 2 + size[1] / 2, 0), size), sc.material("battery", BATTERY), **battery.extras())

    # landing gear: two skids
    gear = Mesh()
    skid_len, skid_z, tube = 0.75 * wheelbase, max(hub_r * 1.1, 0.35 * wheelbase), max(0.005, 0.012 * wheelbase)
    for side in (-1, 1):
        zz = side * skid_z
        gear.extend(cylinder((-skid_len / 2, tube, zz), (skid_len / 2, tube, zz), tube, 12))
        for xx in (-0.25 * skid_len, 0.25 * skid_len):
            gear.extend(cylinder((xx, tube, zz), (xx * 0.4, hub_y - hub_h / 2, side * hub_r * 0.6), tube * 0.8, 12))
    sc.add("airframe/landing gear", gear, sc.material("gear", GEAR), **frame.extras())

    # payload under the hub
    if payload and pay_draw:
        kind, dims = pay_draw
        bottom = hub_y - hub_h / 2
        if kind == "winch":
            r = dims
            y = bottom - r - 0.01
            sc.add("payload/drum", cylinder((0, y, -1.2 * r), (0, y, 1.2 * r), r, 32), sc.material("winch", WINCH), **payload.extras())
            line = min(number(payload.values, "tetherLength", 10), 1.0) * 0.25  # a short drop, not to scale
            sc.add("payload/tether", cylinder((0, y - r, 0), (0, y - r - line, 0), 0.003, 8), sc.material("tether", TETHER), **payload.extras())
            sc.add("payload/hook", box((0, y - r - line - 0.02, 0), (0.04, 0.04, 0.04)), "winch", **payload.extras())
        else:
            sx, sy, sz = dims
            sc.add("payload", box((0, bottom - sy / 2 - 0.005, 0), (sx, sy, sz)), sc.material("cardboard", CARDBOARD), **payload.extras())

    # any other slot: a small box on the hub edge, so nothing chosen goes missing
    for k, f in enumerate(others):
        s = 0.06 * (number(f.values, "mass", 0.2) / 0.2) ** (1 / 3)
        sc.add(f.name, box((-hub_r - s, hub_y, (k - len(others) / 2) * s * 1.5), (s, s, s)), sc.material("placeholder", PLACEHOLDER), **f.extras())

    res.metrics = {
        "rotors": n,
        "wheelbase": wheelbase,
        "propellerDiameter": prop_d,
        "tipClearance": clearance if n > 1 else None,
    }
    return res


# ------------------------------------------------------------------ generic
PALETTE = ["#2563eb", "#0d9488", "#b45309", "#7c3aed", "#be185d", "#475569", "#15803d", "#c2410c"]


def generic(fills: list[SlotFill]) -> Result:
    """One box per slot element, side ∝ cube root of its mass, in a row per slot."""
    sc = Scene()
    res = Result(sc, "generic")
    sides = [0.15 * max(number(f.values, "mass", 1.0), 0.05) ** (1 / 3) if f.instance else 0.15 for f in fills]
    pitch = 1.5 * max(sides or [0.15])
    for k, (f, s) in enumerate(zip(fills, sides)):
        if f.instance:
            mat = sc.material(f"slot{k % len(PALETTE)}", Material(hex_color(PALETTE[k % len(PALETTE)]), roughness=0.5))
        else:
            mat = sc.material("placeholder", PLACEHOLDER)
            res.warnings.append(f"{f.name} is not chosen")
        for i in range(f.count):
            name = f.name if f.count == 1 else f"{f.name}[{i + 1}]"
            sc.add(name, box((k * pitch, s / 2, i * pitch), (s, s, s)), mat, **f.extras(i if f.count > 1 else None))
    res.metrics = {"slots": len(fills)}
    return res
