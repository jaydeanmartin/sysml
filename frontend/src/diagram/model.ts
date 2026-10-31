// Diagram view-models: turn the interpreted graph into boxes, ports and edges per view.
import type { GEdge, GNode, Graph } from "../types";

export type Shape = "box" | "rounded" | "state" | "start" | "done" | "requirement" | "package" | "note";

export interface Line {
  text: string;
  nodeId?: string;
  muted?: boolean;
}

export interface Compartment {
  title?: string;
  lines: Line[];
}

export interface DPort {
  id: string;
  nodeId: string;
  label: string;
  side: "WEST" | "EAST" | "NORTH" | "SOUTH";
}

export interface DNode {
  id: string; // unique within the diagram
  nodeId?: string; // graph node it represents (selection, highlight)
  stereo?: string;
  label: string;
  shape: Shape;
  compartments: Compartment[];
  children: DNode[];
  ports: DPort[];
  badge?: string;
  status?: "pass" | "fail" | null;
  width?: number;
  height?: number;
}

export interface DEdge {
  id: string;
  kind: string;
  source: string;
  target: string;
  label?: string | null;
  edgeId?: string;
}

export interface DiagramModel {
  nodes: DNode[];
  edges: DEdge[];
  direction: "RIGHT" | "DOWN";
  empty?: string;
}

export type ViewKind = "structure" | "interconnection" | "requirements" | "behavior";

// ------------------------------------------------------------------ helpers
export class Index {
  byId = new Map<string, GNode>();
  children = new Map<string, GNode[]>();
  constructor(public graph: Graph) {
    for (const n of graph.nodes) {
      this.byId.set(n.id, n);
      if (n.owner) {
        const list = this.children.get(n.owner) || [];
        list.push(n);
        this.children.set(n.owner, list);
      }
    }
  }
  kids(id: string, ...kinds: string[]): GNode[] {
    const list = this.children.get(id) || [];
    return kinds.length ? list.filter((n) => kinds.includes(n.kind)) : list;
  }
  type(n: GNode): GNode | undefined {
    return n.typeId ? this.byId.get(n.typeId) : undefined;
  }
  /** Members of n merged with the inherited members of its type (redefinitions win). */
  features(n: GNode, kinds: string[], seen = new Set<string>()): { node: GNode; ids: Set<string> }[] {
    if (seen.has(n.id)) return [];
    seen.add(n.id);
    const own = this.kids(n.id, ...kinds).map((node) => ({ node, ids: new Set([node.id]) }));
    const t = this.type(n);
    const supers: GNode[] = [];
    if (t) supers.push(t);
    for (const e of this.graph.edges) {
      if (e.kind === "specializes" && e.source === n.id) {
        const g = this.byId.get(e.target);
        if (g) supers.push(g);
      }
    }
    for (const s of supers) {
      for (const inh of this.features(s, kinds, seen)) {
        const redef = own.find((o) => o.node.redefines && inh.ids.has(o.node.redefines));
        if (redef) inh.ids.forEach((i) => redef.ids.add(i));
        else if (!own.some((o) => o.node.name === inh.node.name)) own.push(inh);
      }
    }
    return own;
  }
}

const short = (s: string | null | undefined, max = 48) =>
  !s ? "" : s.length > max ? s.slice(0, max - 1) + "…" : s;

export function featureLine(n: GNode): string {
  const dir = n.direction && n.kind !== "attribute" && n.kind !== "subject" ? `${n.direction === "out" && n.kind === "param" ? "out" : n.direction} ` : "";
  const name = n.redefines && !n.typeName ? `:>> ${n.name}` : n.name;
  const type = n.typeName ? ` : ${n.typeName}` : "";
  const mult = n.multiplicity && n.multiplicity !== "[1]" ? ` ${n.multiplicity}` : "";
  const val = n.value ? ` = ${short(n.value, 40)}` : "";
  return `${dir}${name}${type}${mult}${val}`;
}

function wrap(text: string, width = 44): string[] {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > width) {
      if (cur) lines.push(cur);
      cur = w;
    } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  return lines;
}

const stereo = (n: GNode) => `«${n.isAbstract ? "abstract " : ""}${n.kind}»`;

// ------------------------------------------------------------------ structure
const STRUCT_DEFS = new Set([
  "part def", "port def", "item def", "attribute def", "enum def", "connection def", "interface def",
  "occurrence def", "calc def", "constraint def", "allocation def", "flow def", "analysis def", "verification def",
]);
const STRUCT_USAGES = new Set(["part", "item", "occurrence", "analysis"]);

function compartmentsFor(ix: Index, n: GNode, opts: { nested?: boolean } = {}): Compartment[] {
  const comps: Compartment[] = [];
  const own = ix.kids(n.id);
  const lines = (kinds: string[]) =>
    own.filter((c) => kinds.includes(c.kind)).map((c) => ({ text: featureLine(c), nodeId: c.id }));
  const attrs = lines(["attribute", "enum"]);
  const ports = lines(["port"]);
  const params = lines(["param", "subject"]);
  const parts = lines(["part", "item", "ref", "occurrence"]);
  if (params.length) comps.push({ title: "parameters", lines: params });
  if (attrs.length) comps.push({ title: "attributes", lines: attrs });
  if (ports.length) comps.push({ title: "ports", lines: ports });
  if (parts.length) comps.push({ title: "parts", lines: parts });
  if (opts.nested) {
    // instance values of nested parts, e.g. engine.mass = 180 [kg]
    const nested: Line[] = [];
    const walk = (p: GNode, prefix: string) => {
      for (const c of ix.kids(p.id)) {
        if ((c.kind === "attribute" || c.kind === "enum") && c.value) {
          nested.push({ text: `${prefix}${c.name} = ${short(c.value, 36)}`, nodeId: c.id });
        } else if (c.kind === "part" || c.kind === "item") walk(c, `${prefix}${c.name}.`);
      }
    };
    for (const c of own) if (c.kind === "part" || c.kind === "item") walk(c, `${c.name}.`);
    if (nested.length) comps.push({ title: "values", lines: nested });
  }
  for (const c of n.constraints || []) comps.push({ title: c.kind, lines: [{ text: short(c.text, 60) }] });
  return comps;
}

export function structureView(graph: Graph): DiagramModel {
  const ix = new Index(graph);
  const shown = new Map<string, DNode>();
  const roots: DNode[] = [];
  const packages = graph.nodes.filter((n) => n.kind === "package");
  const nested = packages.length > 1;
  const pkgNodes = new Map<string, DNode>();
  if (nested) {
    for (const p of packages) {
      const d: DNode = { id: p.id, nodeId: p.id, stereo: "«package»", label: p.name, shape: "package", compartments: [], children: [], ports: [] };
      pkgNodes.set(p.id, d);
    }
    for (const p of packages) {
      const d = pkgNodes.get(p.id)!;
      const parent = p.owner && pkgNodes.get(p.owner);
      (parent ? parent.children : roots).push(d);
    }
  }
  for (const n of graph.nodes) {
    const isDef = STRUCT_DEFS.has(n.kind);
    const isTopUsage = STRUCT_USAGES.has(n.kind) && (!n.owner || ix.byId.get(n.owner)?.kind === "package");
    if (!isDef && !isTopUsage) continue;
    const d: DNode = {
      id: n.id,
      nodeId: n.id,
      stereo: stereo(n),
      label: isTopUsage && n.typeName ? `${n.name} : ${n.typeName}` : n.name,
      shape: isTopUsage ? "rounded" : "box",
      compartments: compartmentsFor(ix, n, { nested: isTopUsage }),
      children: [],
      ports: [],
      badge: n.tool ? `⚡ REST · ${n.tool.toolName}` : undefined,
    };
    shown.set(n.id, d);
    const pkg = nested ? pkgNodes.get(nearestPackage(ix, n) || "") : undefined;
    (pkg ? pkg.children : roots).push(d);
  }
  const edges: DEdge[] = [];
  for (const n of graph.nodes) {
    if (!(n.kind === "part" || n.kind === "item") || !n.owner || !n.typeId) continue;
    if (shown.has(n.owner) && shown.has(n.typeId) && STRUCT_DEFS.has(ix.byId.get(n.owner)!.kind)) {
      edges.push({ id: `comp:${n.id}`, kind: "composition", source: n.owner, target: n.typeId, label: `${n.name}${n.multiplicity && n.multiplicity !== "[1]" ? " " + n.multiplicity : ""}` });
    }
  }
  for (const e of graph.edges) {
    if (e.kind === "specializes" && shown.has(e.source) && shown.has(e.target)) {
      edges.push({ id: e.id, kind: "specializes", source: e.source, target: e.target, edgeId: e.id });
    }
    if (e.kind === "typing" && shown.has(e.source) && shown.has(e.target) && STRUCT_USAGES.has(ix.byId.get(e.source)!.kind)) {
      edges.push({ id: e.id, kind: "definedBy", source: e.source, target: e.target, edgeId: e.id });
    }
  }
  pruneEmptyPackages(roots);
  return {
    nodes: roots,
    edges,
    direction: "DOWN",
    empty: shown.size ? undefined : "No definitions yet. Add a `part def` to see the structure.",
  };
}

function nearestPackage(ix: Index, n: GNode): string | null {
  let cur = n.owner ? ix.byId.get(n.owner) : undefined;
  while (cur && cur.kind !== "package") cur = cur.owner ? ix.byId.get(cur.owner) : undefined;
  return cur?.id || null;
}

function pruneEmptyPackages(list: DNode[]) {
  for (let i = list.length - 1; i >= 0; i--) {
    const d = list[i];
    if (d.shape === "package") {
      pruneEmptyPackages(d.children);
      if (!d.children.length) list.splice(i, 1);
    }
  }
}

// ------------------------------------------------------------------ interconnection
interface Inst {
  key: string;
  node: GNode;
  ids: Set<string>;
  children: Inst[];
  ports: { key: string; node: GNode; ids: Set<string> }[];
}

export function interconnectionCandidates(graph: Graph): GNode[] {
  const ix = new Index(graph);
  return graph.nodes.filter(
    (n) =>
      (n.kind === "part def" || n.kind === "part" || n.kind === "item def") &&
      ix.features(n, ["part", "item"]).length > 0 &&
      (!n.owner || ix.byId.get(n.owner)?.kind === "package")
  );
}

export function defaultContext(graph: Graph): string | null {
  const cands = interconnectionCandidates(graph);
  const withConn = cands.find((c) =>
    graph.edges.some((e) => ["connection", "interface", "flow", "binding"].includes(e.kind) && (e.context === c.id || e.context === c.typeId))
  );
  return (withConn || cands[0])?.id || null;
}

export function interconnectionView(graph: Graph, contextId: string | null): DiagramModel {
  const ix = new Index(graph);
  const ctx = contextId ? ix.byId.get(contextId) : undefined;
  if (!ctx) {
    return { nodes: [], edges: [], direction: "RIGHT", empty: "No part with nested parts to show. Add parts and `connect` statements." };
  }
  const build = (node: GNode, key: string, ids: Set<string>, depth: number): Inst => ({
    key,
    node,
    ids,
    children:
      depth < 3
        ? ix.features(node, ["part", "item"]).map((f) => build(f.node, `${key}/${f.node.name}`, f.ids, depth + 1))
        : [],
    ports: ix.features(node, ["port"]).map((p) => ({ key: `${key}/${p.node.name}`, node: p.node, ids: p.ids })),
  });
  const root = build(ctx, ctx.name, new Set([ctx.id]), 0);

  const toD = (inst: Inst, isRoot = false): DNode => {
    const n = inst.node;
    const type = ix.type(n);
    const attrs = ix
      .features(n, ["attribute"])
      .filter((a) => !isRoot || a.node.value)
      .slice(0, 6)
      .map((a) => ({ text: featureLine(a.node), nodeId: a.node.id }));
    return {
      id: inst.key,
      nodeId: n.id,
      stereo: `«${n.kind}»`,
      label: n.kind.endsWith("def") ? n.name : `${n.name}${n.typeName || type ? " : " + (n.typeName || type?.name) : ""}`,
      shape: n.kind.endsWith("def") ? "box" : "rounded",
      compartments: attrs.length ? [{ title: "attributes", lines: attrs }] : [],
      children: inst.children.map((c) => toD(c)),
      ports: inst.ports.map((p) => ({
        id: p.key,
        nodeId: p.node.id,
        label: p.node.name,
        side: p.node.conjugated || p.node.direction === "in" ? "WEST" : "EAST",
      })),
    };
  };

  const edges: DEdge[] = [];
  const connKinds = ["connection", "interface", "flow", "binding", "allocation"];
  const resolve = (inst: Inst, path: string[]): string | null => {
    let cur = inst;
    for (let i = 0; i < path.length; i++) {
      const id = path[i];
      const child = cur.children.find((c) => c.ids.has(id));
      if (child) {
        cur = child;
        continue;
      }
      const port = cur.ports.find((p) => p.ids.has(id));
      if (port) return port.key;
      return i === 0 ? null : cur.key;
    }
    return cur.key;
  };
  const visit = (inst: Inst) => {
    const ctxIds = new Set<string>([inst.node.id]);
    let t = ix.type(inst.node);
    while (t && !ctxIds.has(t.id)) {
      ctxIds.add(t.id);
      t = ix.type(t);
    }
    for (const e of graph.edges) {
      if (!connKinds.includes(e.kind) || !e.context || !ctxIds.has(e.context)) continue;
      const s = resolve(inst, e.sourcePath || [e.source]);
      const d = resolve(inst, e.targetPath || [e.target]);
      if (s && d && s !== d) edges.push({ id: `${inst.key}#${e.id}`, kind: e.kind, source: s, target: d, label: e.label, edgeId: e.id });
    }
    inst.children.forEach(visit);
  };
  visit(root);
  return { nodes: [toD(root, true)], edges, direction: "RIGHT" };
}

// ------------------------------------------------------------------ requirements
export function requirementsView(graph: Graph, verdicts: Record<string, string>): DiagramModel {
  const ix = new Index(graph);
  const nodes: DNode[] = [];
  const shown = new Set<string>();
  const reqKinds = ["requirement def", "requirement", "concern def", "concern"];
  for (const n of graph.nodes) {
    if (!reqKinds.includes(n.kind)) continue;
    const owner = n.owner ? ix.byId.get(n.owner) : undefined;
    if (owner && !["package"].includes(owner.kind) && !reqKinds.includes(owner.kind)) continue; // objectives etc.
    const comps: Compartment[] = [];
    if (n.doc) comps.push({ lines: wrap(n.doc).map((t) => ({ text: t })) });
    const subj = ix.kids(n.id, "subject")[0];
    if (subj) comps.push({ title: "subject", lines: [{ text: featureLine(subj), nodeId: subj.id }] });
    const attrs = ix.kids(n.id, "attribute");
    if (attrs.length) comps.push({ title: "attributes", lines: attrs.map((a) => ({ text: featureLine(a), nodeId: a.id })) });
    for (const c of n.constraints || []) comps.push({ title: c.kind, lines: wrap(c.text, 50).map((t) => ({ text: t })) });
    const v = verdicts[n.qualifiedName || ""] || verdicts[n.name];
    nodes.push({
      id: n.id,
      nodeId: n.id,
      stereo: stereo(n),
      label: n.kind.endsWith("def") ? n.name : `${n.name}${n.typeName ? " : " + n.typeName : ""}`,
      shape: "requirement",
      compartments: comps,
      children: [],
      ports: [],
      status: v === "holds" || v === "pass" ? "pass" : v ? "fail" : null,
    });
    shown.add(n.id);
  }
  const edges: DEdge[] = [];
  for (const e of graph.edges) {
    if ((e.kind === "satisfy" || e.kind === "verify") && shown.has(e.target)) {
      if (!shown.has(e.source)) {
        const s = ix.byId.get(e.source);
        if (!s) continue;
        nodes.push({
          id: s.id,
          nodeId: s.id,
          stereo: `«${s.kind}»`,
          label: `${s.name}${s.typeName ? " : " + s.typeName : ""}`,
          shape: "rounded",
          compartments: [],
          children: [],
          ports: [],
        });
        shown.add(s.id);
      }
      edges.push({ id: e.id, kind: e.kind, source: e.source, target: e.target, label: `«${e.kind}»`, edgeId: e.id });
    }
    if (e.kind === "typing" && shown.has(e.source) && shown.has(e.target)) {
      edges.push({ id: e.id, kind: "definedBy", source: e.source, target: e.target, edgeId: e.id });
    }
  }
  return {
    nodes,
    edges,
    direction: "RIGHT",
    empty: nodes.length ? undefined : "No requirements yet. Use “+ Requirement” in the editor toolbar to capture one.",
  };
}

// ------------------------------------------------------------------ behavior
const BEHAVIOR_CONTAINERS = ["action def", "action", "state def", "state", "use case def", "analysis def"];

export function behaviorView(graph: Graph): DiagramModel {
  const ix = new Index(graph);
  const roots: DNode[] = [];
  const done = new Set<string>();
  const isStep = (n: GNode) => ["action", "state", "perform", "send", "accept", "exhibit", "decide", "merge", "fork", "join", "entry", "do", "exit"].includes(n.kind);

  const build = (n: GNode): DNode => {
    done.add(n.id);
    const steps = ix.kids(n.id).filter(isStep);
    const attrs = ix.kids(n.id, "attribute", "param").map((a) => ({ text: featureLine(a), nodeId: a.id }));
    const isState = n.kind.startsWith("state");
    const d: DNode = {
      id: n.id,
      nodeId: n.id,
      stereo: `«${n.kind}»`,
      label: n.typeName && !n.kind.endsWith("def") ? `${n.name} : ${n.typeName}` : n.name,
      shape: isState ? "state" : "rounded",
      compartments: attrs.length ? [{ lines: attrs }] : [],
      children: steps.map(build),
      ports: [],
    };
    const pseudo = new Set<string>();
    for (const e of graph.edges) {
      if (e.context !== n.id) continue;
      for (const end of [e.source, e.target]) {
        if (end.startsWith(n.id + "::__") && !pseudo.has(end)) {
          pseudo.add(end);
          d.children.push({
            id: end,
            label: "",
            shape: end.endsWith("__start") ? "start" : "done",
            compartments: [],
            children: [],
            ports: [],
          });
        }
      }
    }
    return d;
  };
  for (const n of graph.nodes) {
    if (done.has(n.id) || !BEHAVIOR_CONTAINERS.includes(n.kind)) continue;
    const owner = n.owner ? ix.byId.get(n.owner) : undefined;
    if (owner && BEHAVIOR_CONTAINERS.includes(owner.kind)) continue;
    const hasSteps = ix.kids(n.id).some(isStep);
    if (!hasSteps && !n.kind.endsWith("def")) continue;
    if (n.kind === "analysis def" && !hasSteps) continue;
    roots.push(build(n));
  }
  const edges: DEdge[] = graph.edges
    .filter((e) => e.kind === "succession" || e.kind === "transition")
    .map((e: GEdge) => ({ id: e.id, kind: e.kind, source: e.source, target: e.target, label: e.label, edgeId: e.id }));
  return {
    nodes: roots,
    edges,
    direction: "RIGHT",
    empty: roots.length ? undefined : "No actions or state machines yet. Use “+ Action” or “+ States”.",
  };
}
