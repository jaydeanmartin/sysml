import { useCallback, useEffect, useRef, useState } from "react";
import type { DiagramModel } from "./model";
import { COMP_TITLE, FONT_MONO, LINE, headerHeight, layout, type LEdge, type LNode, type Layout } from "./layout";

interface Props {
  model: DiagramModel;
  selected: string | null;
  active: Set<string>;
  onSelect(nodeId: string | null): void;
  fitKey: string;
}

interface View {
  x: number;
  y: number;
  k: number;
}

export function Diagram({ model, selected, active, onSelect, fitKey }: Props) {
  const [lay, setLay] = useState<Layout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const lastFit = useRef<string>("");
  const fitKeyRef = useRef(fitKey);
  fitKeyRef.current = fitKey;

  const fitTo = useCallback((l: Layout | null) => {
    if (!l || !box.current) return;
    const { clientWidth: W, clientHeight: H } = box.current;
    if (!W || !H) return;
    // fit, but never so small that text becomes unreadable: then start at the top-left
    const k = Math.min(1.1, Math.max(0.55, Math.min((W - 32) / (l.width || 1), (H - 32) / (l.height || 1))));
    setView({ k, x: Math.max(12, (W - l.width * k) / 2), y: Math.max(8, (H - l.height * k) / 2) });
  }, []);
  const fit = useCallback(() => fitTo(lay), [fitTo, lay]);

  useEffect(() => {
    let cancelled = false;
    const key = fitKeyRef.current;
    layout(model)
      .then((l) => {
        if (cancelled) return;
        setLay(l);
        setError(null);
        // refit when the view/model changed, or the first time a diagram has content
        if (lastFit.current !== key && l.nodes.length) {
          lastFit.current = key;
          fitTo(l);
        }
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [model, fitTo]);

  const onWheel = (e: React.WheelEvent) => {
    const r = box.current!.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    const k = Math.min(3, Math.max(0.15, view.k * Math.exp(-e.deltaY * 0.0015)));
    setView({ k, x: mx - ((mx - view.x) * k) / view.k, y: my - ((my - view.y) * k) / view.k });
  };
  const onDown = (e: React.MouseEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
  };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true;
    setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
  };
  const onUp = (e: React.MouseEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved && (e.target as Element).classList.contains("bg")) onSelect(null);
  };
  const zoom = (f: number) => {
    const W = box.current!.clientWidth / 2;
    const H = box.current!.clientHeight / 2;
    const k = Math.min(3, Math.max(0.15, view.k * f));
    setView({ k, x: W - ((W - view.x) * k) / view.k, y: H - ((H - view.y) * k) / view.k });
  };

  return (
    <div className="diagram" ref={box} onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => (drag.current = null)}>
      <svg width="100%" height="100%">
        <defs>
          <marker id="m-arrow" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="11" markerHeight="11" orient="auto-start-reverse">
            <path d="M1,1 L11,6 L1,11" fill="none" stroke="context-stroke" strokeWidth="1.4" />
          </marker>
          <marker id="m-filled" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">
            <path d="M1,1 L11,6 L1,11 z" fill="context-stroke" />
          </marker>
          <marker id="m-triangle" viewBox="0 0 14 14" refX="13" refY="7" markerWidth="14" markerHeight="14" orient="auto-start-reverse">
            <path d="M1,1 L13,7 L1,13 z" fill="var(--surface)" stroke="context-stroke" strokeWidth="1.2" />
          </marker>
          <marker id="m-diamond" viewBox="0 0 16 10" refX="1" refY="5" markerWidth="16" markerHeight="10" orient="auto-start-reverse">
            <path d="M1,5 L8,1 L15,5 L8,9 z" fill="context-stroke" />
          </marker>
        </defs>
        <rect className="bg" width="100%" height="100%" fill="transparent" />
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {lay?.nodes.map((n) => <NodeView key={n.d.id} n={n} selected={selected} active={active} onSelect={onSelect} />)}
          {lay?.edges.map((e) => <EdgeView key={e.e.id} e={e} />)}
        </g>
      </svg>
      {model.empty && <div className="diagram-empty">{model.empty}</div>}
      {error && <div className="diagram-empty error">Layout failed: {error}</div>}
      <div className="zoom">
        <button onClick={() => zoom(1.2)} title="Zoom in">+</button>
        <button onClick={() => zoom(1 / 1.2)} title="Zoom out">−</button>
        <button onClick={fit} title="Fit to view">⤢</button>
      </div>
    </div>
  );
}

function NodeView({ n, selected, active, onSelect }: { n: LNode; selected: string | null; active: Set<string>; onSelect(id: string | null): void }) {
  const d = n.d;
  const isSel = !!d.nodeId && d.nodeId === selected;
  const isActive = !!d.nodeId && active.has(d.nodeId);
  const cls = `node shape-${d.shape}${isSel ? " selected" : ""}${isActive ? " active" : ""}${d.status ? " " + d.status : ""}`;
  const click = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (d.nodeId) onSelect(d.nodeId);
  };
  if (d.shape === "start" || d.shape === "done") {
    const cx = n.x + n.w / 2;
    const cy = n.y + n.h / 2;
    return (
      <g className={`pseudo${isActive ? " active" : ""}`}>
        {d.shape === "start" ? (
          <circle cx={cx} cy={cy} r={8} className="pseudo-fill" />
        ) : (
          <>
            <circle cx={cx} cy={cy} r={10} className="pseudo-ring" />
            <circle cx={cx} cy={cy} r={6} className="pseudo-fill" />
          </>
        )}
      </g>
    );
  }
  const rx = d.shape === "rounded" ? 10 : d.shape === "state" ? 16 : 3;
  const hh = headerHeight(d);
  let cy = n.y + hh;
  return (
    <g className={cls}>
      {d.shape === "package" && <path className="pkg-tab" d={`M${n.x},${n.y} h${Math.min(120, n.w / 3)} v-14 h-${Math.min(120, n.w / 3)} z`} transform={`translate(0,0)`} />}
      <rect className="body" x={n.x} y={n.y} width={n.w} height={n.h} rx={rx} onMouseDown={(e) => e.stopPropagation()} onClick={click} />
      {d.shape === "requirement" && <rect className="req-bar" x={n.x} y={n.y} width={5} height={n.h} rx={2} />}
      <g onClick={click} style={{ cursor: d.nodeId ? "pointer" : "default" }}>
        {d.stereo && (
          <text className="stereo" x={n.x + n.w / 2} y={n.y + 15} textAnchor="middle">
            {d.stereo}
          </text>
        )}
        <text className="label" x={n.x + n.w / 2} y={n.y + 32} textAnchor="middle">
          {d.label}
        </text>
        {d.status && (
          <text className={`verdict ${d.status}`} x={n.x + n.w - 10} y={n.y + 18} textAnchor="end">
            {d.status === "pass" ? "✓" : "✗"}
          </text>
        )}
        {d.badge && (
          <g>
            <rect className="badge" x={n.x + n.w / 2 - 70} y={n.y + 38} width={140} height={16} rx={8} />
            <text className="badge-text" x={n.x + n.w / 2} y={n.y + 50} textAnchor="middle">
              {d.badge}
            </text>
          </g>
        )}
      </g>
      {d.compartments.map((c, ci) => {
        const top = cy;
        cy += (c.title ? COMP_TITLE : 4) + c.lines.length * LINE + 6;
        return (
          <g key={ci}>
            <line className="divider" x1={n.x} x2={n.x + n.w} y1={top} y2={top} />
            {c.title && (
              <text className="comp-title" x={n.x + n.w / 2} y={top + 11} textAnchor="middle">
                {c.title}
              </text>
            )}
            {c.lines.map((l, li) => (
              <text
                key={li}
                className={`comp-line${l.nodeId && l.nodeId === selected ? " sel" : ""}${l.nodeId && active.has(l.nodeId) ? " act" : ""}`}
                x={n.x + 10}
                y={top + (c.title ? COMP_TITLE : 4) + li * LINE + 11}
                style={{ font: FONT_MONO }}
                onClick={(e) => {
                  e.stopPropagation();
                  if (l.nodeId) onSelect(l.nodeId);
                }}
              >
                {l.text}
              </text>
            ))}
          </g>
        );
      })}
      {n.children.map((c) => (
        <NodeView key={c.d.id} n={c} selected={selected} active={active} onSelect={onSelect} />
      ))}
      {n.ports.map((p) =>
        p.p ? (
          <g key={p.p.id} className={`port${p.p.nodeId === selected ? " selected" : ""}`} onClick={(e) => { e.stopPropagation(); onSelect(p.p.nodeId); }}>
            <rect x={p.x} y={p.y} width={10} height={10} />
            <text x={p.label ? p.label.x + 2 : p.x + 14} y={p.label ? p.label.y + 11 : p.y - 2}>
              {p.p.label}
            </text>
          </g>
        ) : null
      )}
    </g>
  );
}

const EDGE_STYLE: Record<string, { cls: string; start?: string; end?: string }> = {
  composition: { cls: "solid", start: "url(#m-diamond)" },
  specializes: { cls: "solid", end: "url(#m-triangle)" },
  definedBy: { cls: "dotted", end: "url(#m-arrow)" },
  connection: { cls: "conn" },
  interface: { cls: "conn bold" },
  binding: { cls: "dashed" },
  allocation: { cls: "dashed", end: "url(#m-arrow)" },
  flow: { cls: "conn", end: "url(#m-filled)" },
  satisfy: { cls: "dashed", end: "url(#m-arrow)" },
  verify: { cls: "dashed", end: "url(#m-arrow)" },
  succession: { cls: "solid", end: "url(#m-filled)" },
  transition: { cls: "solid", end: "url(#m-filled)" },
};

function EdgeView({ e }: { e: LEdge }) {
  if (!e.e || e.points.length < 2) return null;
  const st = EDGE_STYLE[e.e.kind] || { cls: "solid" };
  const d = e.points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
  return (
    <g className={`edge kind-${e.e.kind}`}>
      <path d={d} className={st.cls} markerStart={st.start} markerEnd={st.end} />
      {e.label && (
        <g>
          <rect className="edge-label-bg" x={e.label.x} y={e.label.y} width={e.label.w} height={e.label.h} rx={3} />
          <text className="edge-label" x={e.label.x + e.label.w / 2} y={e.label.y + 12} textAnchor="middle">
            {e.label.text}
          </text>
        </g>
      )}
    </g>
  );
}
