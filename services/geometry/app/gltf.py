"""Triangle meshes and a binary glTF 2.0 (GLB) writer, in plain Python.

Coordinates are metres, Y up (the glTF convention). Meshes are built in place
(vertices already at their final position), one mesh and one node per part,
so a viewer can pick and highlight parts by node.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any

Vec = tuple[float, float, float]


# ------------------------------------------------------------------ vectors
def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec) -> Vec:
    n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    return (a[0] / n, a[1] / n, a[2] / n) if n else (0.0, 1.0, 0.0)


# ------------------------------------------------------------------ meshes
@dataclass
class Mesh:
    positions: list[Vec] = field(default_factory=list)
    normals: list[Vec] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    def vertex(self, p: Vec, n: Vec) -> int:
        self.positions.append(p)
        self.normals.append(n)
        return len(self.positions) - 1

    def extend(self, other: Mesh) -> Mesh:
        base = len(self.positions)
        self.positions += other.positions
        self.normals += other.normals
        self.indices += [i + base for i in other.indices]
        return self


def merge(*meshes: Mesh) -> Mesh:
    out = Mesh()
    for m in meshes:
        out.extend(m)
    return out


def box(center: Vec, size: Vec, yaw: float = 0.0) -> Mesh:
    """An axis-aligned box (size = x, y, z extents), turned by ``yaw`` radians about Y."""
    c, s = math.cos(yaw), math.sin(yaw)
    ax = ((c, 0.0, -s), (0.0, 1.0, 0.0), (s, 0.0, c))  # the box's x, y, z axes
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    m = Mesh()
    for k in range(3):
        for sign in (1.0, -1.0):
            n = scale(ax[k], sign)
            # u x v = n, so the corners below run counter-clockwise seen from outside
            u_k, v_k = ((k + 1) % 3, (k + 2) % 3) if sign > 0 else ((k + 2) % 3, (k + 1) % 3)
            u, v = scale(ax[u_k], half[u_k]), scale(ax[v_k], half[v_k])
            face = add(center, scale(n, half[k]))
            ids = [m.vertex(add(face, add(scale(u, a), scale(v, b))), n) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            m.indices += [ids[0], ids[1], ids[2], ids[0], ids[2], ids[3]]
    return m


def cylinder(p0: Vec, p1: Vec, radius: float, segments: int = 32, caps: bool = True) -> Mesh:
    """A cylinder whose axis runs from ``p0`` to ``p1``."""
    d = norm(sub(p1, p0))
    u = norm(cross(d, (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (1.0, 0.0, 0.0)))
    v = cross(d, u)  # u, v, d right-handed: angles increase counter-clockwise about d
    m = Mesh()
    rim = [add(scale(u, math.cos(2 * math.pi * i / segments)), scale(v, math.sin(2 * math.pi * i / segments))) for i in range(segments)]
    side0 = [m.vertex(add(p0, scale(r, radius)), r) for r in rim]
    side1 = [m.vertex(add(p1, scale(r, radius)), r) for r in rim]
    for i in range(segments):
        j = (i + 1) % segments
        m.indices += [side0[i], side0[j], side1[j], side0[i], side1[j], side1[i]]
    if caps:
        for p, n, flip in ((p1, d, False), (p0, scale(d, -1), True)):
            centre = m.vertex(p, n)
            ring = [m.vertex(add(p, scale(r, radius)), n) for r in rim]
            for i in range(segments):
                j = (i + 1) % segments
                m.indices += [centre, ring[j], ring[i]] if flip else [centre, ring[i], ring[j]]
    return m


# ------------------------------------------------------------------ scene
@dataclass
class Material:
    color: tuple[float, float, float, float]  # linear RGBA
    metallic: float = 0.1
    roughness: float = 0.6
    double_sided: bool = False

    def to_json(self, name: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": name,
            "pbrMetallicRoughness": {"baseColorFactor": list(self.color), "metallicFactor": self.metallic, "roughnessFactor": self.roughness},
            "doubleSided": self.double_sided,
        }
        if self.color[3] < 1:
            out["alphaMode"] = "BLEND"
        return out


def hex_color(h: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """'#rrggbb' (sRGB) -> linear RGBA, as glTF base colors are linear."""

    def lin(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    h = h.lstrip("#")
    return (lin(int(h[0:2], 16)), lin(int(h[2:4], 16)), lin(int(h[4:6], 16)), alpha)


@dataclass
class Part:
    name: str
    mesh: Mesh
    material: str
    extras: dict[str, Any]


class Scene:
    def __init__(self) -> None:
        self.parts: list[Part] = []
        self.materials: dict[str, Material] = {}

    def material(self, name: str, m: Material) -> str:
        self.materials.setdefault(name, m)
        return name

    def add(self, name: str, mesh: Mesh, material: str, **extras: Any) -> None:
        if mesh.indices:
            self.parts.append(Part(name, mesh, material, extras))

    def bounds(self) -> tuple[Vec, Vec]:
        pts = [p for part in self.parts for p in part.mesh.positions] or [(0.0, 0.0, 0.0)]
        return (
            (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)),
        )

    def to_glb(self, extras: dict[str, Any] | None = None) -> bytes:
        mat_names = list(self.materials)
        buf = bytearray()
        views: list[dict[str, Any]] = []
        accessors: list[dict[str, Any]] = []
        meshes: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []

        def view(data: bytes, target: int) -> int:
            while len(buf) % 4:
                buf.append(0)
            views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(data), "target": target})
            buf.extend(data)
            return len(views) - 1

        for part in self.parts:
            m = part.mesh
            flat_p = [c for p in m.positions for c in p]
            flat_n = [c for n in m.normals for c in n]
            pos = view(struct.pack(f"<{len(flat_p)}f", *flat_p), 34962)
            nrm = view(struct.pack(f"<{len(flat_n)}f", *flat_n), 34962)
            idx = view(struct.pack(f"<{len(m.indices)}I", *m.indices), 34963)
            lo = [min(p[k] for p in m.positions) for k in range(3)]
            hi = [max(p[k] for p in m.positions) for k in range(3)]
            a = len(accessors)
            accessors += [
                {"bufferView": pos, "componentType": 5126, "count": len(m.positions), "type": "VEC3", "min": lo, "max": hi},
                {"bufferView": nrm, "componentType": 5126, "count": len(m.normals), "type": "VEC3"},
                {"bufferView": idx, "componentType": 5125, "count": len(m.indices), "type": "SCALAR"},
            ]
            meshes.append(
                {
                    "name": part.name,
                    "primitives": [{"attributes": {"POSITION": a, "NORMAL": a + 1}, "indices": a + 2, "material": mat_names.index(part.material)}],
                }
            )
            nodes.append({"name": part.name, "mesh": len(meshes) - 1, "extras": part.extras})

        doc = {
            "asset": {"version": "2.0", "generator": "sysml-platform geometry service"},
            "scene": 0,
            "scenes": [{"name": "system", "nodes": list(range(len(nodes))), "extras": extras or {}}],
            "nodes": nodes,
            "meshes": meshes,
            "materials": [self.materials[n].to_json(n) for n in mat_names],
            "accessors": accessors,
            "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}],
        }
        js = json.dumps(doc, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        while len(buf) % 4:
            buf.append(0)
        total = 12 + 8 + len(js) + 8 + len(buf)
        return b"".join(
            [
                struct.pack("<4sII", b"glTF", 2, total),
                struct.pack("<I4s", len(js), b"JSON"),
                js,
                struct.pack("<I4s", len(buf), b"BIN\x00"),
                bytes(buf),
            ]
        )
