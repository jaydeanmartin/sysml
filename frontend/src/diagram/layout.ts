import ELK from "elkjs/lib/elk.bundled.js";
import type { DEdge, DiagramModel, DNode, DPort } from "./model";

const elk = new ELK();

export const FONT = "12px Inter, ui-sans-serif, system-ui, sans-serif";
export const FONT_BOLD = "600 13px Inter, ui-sans-serif, system-ui, sans-serif";
export const FONT_MONO = "11.5px 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
export const HEADER = 40;
export const LINE = 16;
export const COMP_TITLE = 15;
const PORT = 10;

let ctx: CanvasRenderingContext2D | null = null;
export function measure(text: string, font = FONT): number {
  if (!ctx) ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return text.length * 7;
  ctx.font = font;
  return ctx.measureText(text).width;
}

export interface LPort {
  p: DPort;
  x: number;
  y: number;
  label?: { x: number; y: number };
}
export interface LNode {
  d: DNode;
  x: number;
  y: number;
  w: number;
  h: number;
  children: LNode[];
  ports: LPort[];
}
export interface LEdge {
  e: DEdge;
  points: { x: number; y: number }[];
  label?: { text: string; x: number; y: number; w: number; h: number };
}
export interface Layout {
  nodes: LNode[];
  edges: LEdge[];
  width: number;
  height: number;
}

function compHeight(d: DNode): number {
  return d.compartments.reduce((h, c) => h + (c.title ? COMP_TITLE : 4) + c.lines.length * LINE + 6, 0);
}

function headerHeight(d: DNode): number {
  if (d.shape === "start" || d.shape === "done") return 0;
  return HEADER + (d.badge ? 18 : 0);
}

function contentWidth(d: DNode): number {
  let w = Math.max(measure(d.label, FONT_BOLD), d.stereo ? measure(d.stereo) : 0) + 28;
  if (d.badge) w = Math.max(w, measure(d.badge) + 36);
  for (const c of d.compartments) {
    if (c.title) w = Math.max(w, measure(c.title) + 24);
    for (const l of c.lines) w = Math.max(w, measure(l.text, FONT_MONO) + 24);
  }
  return Math.ceil(w);
}


function toElk(d: DNode): Record<string, unknown> {
  const header = headerHeight(d);
  const comps = compHeight(d);
  const ports = d.ports.map((p) => ({
    id: `port:${p.id}`,
    width: PORT,
    height: PORT,
    layoutOptions: { "elk.port.side": p.side },
    labels: [{ text: p.label, width: measure(p.label) + 4, height: 14 }],
  }));
  // port labels are placed outside the node by ELK, which reserves the space
  const west = 0;
  const east = 0;
  const base: Record<string, unknown> = {
    id: d.id,
    ports,
    layoutOptions: {
      "elk.portConstraints": "FIXED_SIDE",
      "elk.portAlignment.default": "CENTER",
      "elk.spacing.portPort": "22",
      "elk.portLabels.placement": "OUTSIDE",
    },
  };
  if (d.shape === "start" || d.shape === "done") {
    return { ...base, width: 22, height: 22 };
  }
  if (d.children.length) {
    const top = header + comps + 14;
    const side = Math.max(24, west + 8, east + 8);
    return {
      ...base,
      layoutOptions: {
        ...(base.layoutOptions as object),
        "elk.padding": `[top=${top},left=${side},bottom=24,right=${side}]`,
        "elk.nodeSize.constraints": "MINIMUM_SIZE",
        "elk.nodeSize.minimum": `(${contentWidth(d) + west + east}, ${top + 40})`,
      },
      children: d.children.map(toElk),
    };
  }
  const nPortRows = Math.max(
    d.ports.filter((p) => p.side === "WEST").length,
    d.ports.filter((p) => p.side === "EAST").length
  );
  return {
    ...base,
    width: Math.max(contentWidth(d) + (d.ports.length ? Math.max(west, east) * 0.6 : 0), west + east + 40, 130),
    height: Math.max(header + comps + 8, d.ports.length ? header + nPortRows * 22 + 8 : 0, 52),
  };
}

function collectIds(nodes: DNode[], ids: Set<string>, ports: Set<string>) {
  for (const n of nodes) {
    ids.add(n.id);
    n.ports.forEach((p) => ports.add(p.id));
    collectIds(n.children, ids, ports);
  }
}

export async function layout(model: DiagramModel): Promise<Layout> {
  const ids = new Set<string>();
  const ports = new Set<string>();
  collectIds(model.nodes, ids, ports);
  const endpoint = (x: string) => (ports.has(x) ? `port:${x}` : ids.has(x) ? x : null);
  const edges = model.edges
    .map((e) => ({ e, s: endpoint(e.source), t: endpoint(e.target) }))
    .filter((x) => x.s && x.t);

  const graph = {
    id: "__root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": model.direction,
      "elk.hierarchyHandling": model.nodes.some((n) => n.children.length) ? "INCLUDE_CHILDREN" : "SEPARATE_CHILDREN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "36",
      "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.spacing.edgeNode": "18",
      "elk.spacing.edgeEdge": "12",
      "elk.spacing.edgeLabel": "4",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      "elk.aspectRatio": "1.3",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "40",
    },
    children: model.nodes.map(toElk),
    edges: edges.map(({ e, s, t }) => ({
      id: e.id,
      sources: [s],
      targets: [t],
      labels: e.label ? [{ text: e.label, width: measure(e.label, FONT) + 8, height: 16 }] : [],
    })),
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const res: any = await elk.layout(graph as any);

  const abs = new Map<string, { x: number; y: number }>();
  abs.set("__root", { x: 0, y: 0 });
  const byId = new Map<string, DNode>();
  const index = (list: DNode[]) =>
    list.forEach((d) => {
      byId.set(d.id, d);
      index(d.children);
    });
  index(model.nodes);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const walk = (en: any, ox: number, oy: number): LNode => {
    const x = ox + (en.x || 0);
    const y = oy + (en.y || 0);
    abs.set(en.id, { x, y });
    const d = byId.get(en.id)!;
    d.width = en.width;
    d.height = en.height;
    return {
      d,
      x,
      y,
      w: en.width,
      h: en.height,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      children: (en.children || []).map((c: any) => walk(c, x, y)),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ports: (en.ports || []).map((p: any) => ({
        p: d.ports.find((dp) => `port:${dp.id}` === p.id)!,
        x: x + p.x,
        y: y + p.y,
        label: p.labels?.[0] ? { x: x + p.x + p.labels[0].x, y: y + p.y + p.labels[0].y } : undefined,
      })),
    };
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodes = (res.children || []).map((c: any) => walk(c, 0, 0));

  const byEdge = new Map(model.edges.map((e) => [e.id, e]));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ledges: LEdge[] = (res.edges || []).map((ee: any) => {
    const off = abs.get(ee.container || "__root") || { x: 0, y: 0 };
    const pts: { x: number; y: number }[] = [];
    for (const s of ee.sections || []) {
      pts.push(s.startPoint, ...(s.bendPoints || []), s.endPoint);
    }
    const lab = ee.labels?.[0];
    return {
      e: byEdge.get(ee.id)!,
      points: pts.map((p) => ({ x: p.x + off.x, y: p.y + off.y })),
      label: lab ? { text: lab.text, x: lab.x + off.x, y: lab.y + off.y, w: lab.width, h: lab.height } : undefined,
    };
  });
  return { nodes, edges: ledges, width: res.width || 0, height: res.height || 0 };
}

export { headerHeight, compHeight, PORT };
