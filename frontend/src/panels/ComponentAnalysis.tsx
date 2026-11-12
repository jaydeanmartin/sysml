import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Catalog, CatalogChange, Component, ComponentAnalysisResult, ComponentInstance, Located, PropellerAeroOutputs } from "../types";
import { errorText, short } from "./catalogUtil";
import { useTheme } from "../theme";
import { SERIES as SERIES_LIGHT, SERIES_DARK } from "./MissionSimulation";

interface Props {
  projectId: string;
  catalog: Catalog;
  comp: Component;
  /** the instances of the component and its specializations */
  instances: ComponentInstance[];
  busy: boolean;
  onChange(fn: () => Promise<CatalogChange>): Promise<CatalogChange | null>;
  onOpen(el: Located): void;
}

const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : null;
};
const fmt = (v: number | null, digits = 4) => (v === null ? "—" : Math.abs(v) >= 1000 ? v.toFixed(0) : Number(v.toPrecision(digits)).toString());

/** Component analyses (tool-bound defs with the component as subject) run for
 *  one catalog instance: the results, the instance's current values of the
 *  outputs that are attributes, and writing the results back to the instance. */
export function ComponentAnalysisCard({ projectId, catalog, comp, instances, busy, onChange, onOpen }: Props) {
  const defs = catalog.componentAnalyses.filter((a) => comp.analyses.includes(a.qualifiedName));
  const [instQn, setInstQn] = useState<string>(instances[0]?.qualifiedName || "");
  const [defQn, setDefQn] = useState<string>(defs[0]?.qualifiedName || "");
  const [results, setResults] = useState<Record<string, ComponentAnalysisResult>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!instances.some((i) => i.qualifiedName === instQn)) setInstQn(instances[0]?.qualifiedName || "");
  }, [instances, instQn]);
  const inst = instances.find((i) => i.qualifiedName === instQn);
  const def = defs.find((d) => d.qualifiedName === defQn) || defs[0];
  const key = `${def?.qualifiedName}|${instQn}`;
  const res = results[key];
  if (!def) return null;

  const run = async () => {
    if (!inst) return;
    setRunning(true);
    setError(null);
    try {
      const r = await api.analyzeComponent(projectId, inst.qualifiedName, def.qualifiedName);
      setResults((m) => ({ ...m, [key]: r }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  };

  // the instance's values now (the catalog is re-read after a save), against the analysis
  const writable = res ? Object.keys(res.current) : [];
  const now = (k: string) => (inst ? inst.values[k] ?? null : null);
  const proposed = (k: string) => num(res?.outputs[k]);
  const changed = writable.filter((k) => proposed(k) !== null && num(now(k)) !== Number(fmt(proposed(k))));
  const apply = () =>
    inst &&
    res &&
    onChange(() =>
      api.saveInstance(projectId, {
        component: inst.component,
        name: inst.name,
        doc: inst.doc || "",
        values: { ...inst.ownValues, ...Object.fromEntries(changed.map((k) => [k, fmt(proposed(k))])) },
        replace: inst.qualifiedName,
      })
    );
  const aero = res && Array.isArray((res.outputs as unknown as PropellerAeroOutputs).curve) ? (res.outputs as unknown as PropellerAeroOutputs) : null;

  return (
    <div className="analysis-card">
      <div className="section-title row-between">
        <span>Analyses</span>
        <button className="link" onClick={() => onOpen(def)}>
          {short(def.qualifiedName)} source
        </button>
      </div>
      <div className="panel-bar">
        <label className="inline">
          Instance{" "}
          <select value={instQn} onChange={(e) => setInstQn(e.target.value)}>
            {instances.map((i) => (
              <option key={i.id} value={i.qualifiedName}>
                {i.name}
              </option>
            ))}
          </select>
        </label>
        {defs.length > 1 && (
          <select value={def.qualifiedName} onChange={(e) => setDefQn(e.target.value)}>
            {defs.map((d) => (
              <option key={d.id} value={d.qualifiedName}>
                {d.name}
              </option>
            ))}
          </select>
        )}
        <button className="primary" disabled={!inst || running} onClick={run} title={def.doc || undefined}>
          {running ? "Running…" : `▶ Run ${def.name}`}
        </button>
        <span className="hint">
          {def.toolName} · {def.doc}
        </span>
      </div>
      {error && <div className="error-box">{error}</div>}
      {res && inst && (
        <>
          {aero?.warnings?.length ? (
            <div className="warn-box small">
              {aero.warnings.map((w) => (
                <div key={w}>⚠ {w}</div>
              ))}
            </div>
          ) : null}
          <div className="analysis-head small muted">
            {inst.name} · run {res.runId.slice(0, 8)}
            {aero && (
              <>
                {" "}
                · {fmt(aero.referenceRpm, 4)} rpm ({fmt(num(res.inputs.tipSpeed))} m/s tip) · static thrust {fmt(aero.staticThrust, 3)} N for {fmt(aero.staticPower, 3)} W · figure of merit {fmt(aero.figureOfMerit, 2)} ·
                fitted over J 0–{fmt(aero.fitRange[1], 2)} (max error CT {fmt(aero.fitError.CT, 2)}, CP {fmt(aero.fitError.CP, 2)})
              </>
            )}
          </div>
          {writable.length > 0 && (
            <div className="row-between analysis-compare">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Attribute</th>
                    <th className="num">{inst.name} now</th>
                    <th className="num">Analysis</th>
                    <th className="num">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {writable.map((k) => {
                    const a = num(now(k));
                    const b = proposed(k);
                    const d = a !== null && b !== null && a !== 0 ? ((b - a) / Math.abs(a)) * 100 : null;
                    return (
                      <tr key={k}>
                        <td className="mono">{k}</td>
                        <td className="mono num">{now(k) ?? "—"}</td>
                        <td className="mono num strong">{fmt(b)}</td>
                        <td className={`mono num${d !== null && Math.abs(d) > 20 ? " warn-text" : " muted"}`}>{d === null ? "" : `${d > 0 ? "+" : ""}${d.toFixed(0)} %`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="analysis-apply">
                <button className="primary" disabled={busy || !changed.length} onClick={apply} title={`Save ${inst.name} with the analysis values of ${changed.join(", ")}: a new version of ${inst.modelName}`}>
                  {changed.length ? `Write ${changed.length} value${changed.length > 1 ? "s" : ""} to ${inst.name}` : `${inst.name} has these values`}
                </button>
                <div className="hint">Every analysis that uses them (the dynamic simulation) then runs with the new values.</div>
              </div>
            </div>
          )}
          {aero && <PropellerCharts aero={aero} current={inst.values} />}
          {aero && <SectionTable aero={aero} />}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ propeller results
const poly = (c: (number | null)[], x: number) => (c[0] ?? 0) + (c[1] ?? 0) * x + (c[2] ?? 0) * x * x;

function PropellerCharts({ aero, current }: { aero: PropellerAeroOutputs; current: Record<string, string> }) {
  const SERIES = useTheme().theme === "dark" ? SERIES_DARK : SERIES_LIGHT;
  const js = aero.curve.map((p) => p.J);
  const jMax = Math.max(...js);
  const grid = Array.from({ length: 41 }, (_, i) => (i / 40) * jMax);
  const cur = (p: "ct" | "cp") => [0, 1, 2].map((i) => num(current[`${p}${i}`]));
  const curCt = cur("ct");
  const curCp = cur("cp");
  const hasCurrent = curCt.every((v) => v !== null) && curCp.every((v) => v !== null);
  const fitCt = [aero.ct0, aero.ct1, aero.ct2];
  const fitCp = [aero.cp0, aero.cp1, aero.cp2];
  const within = (x: number) => x <= aero.fitRange[1] + 1e-9;
  const stallAt = (r: number) => {
    const s = aero.sections;
    const i = Math.max(0, s.findIndex((x) => x.station >= r));
    if (i === 0) return s[0].alphaStall;
    const a = s[i - 1], b = s[i];
    return a.alphaStall + ((r - a.station) / (b.station - a.station || 1)) * (b.alphaStall - a.alphaStall);
  };
  const legend = [
    { name: "blade-element analysis", kind: "points" as const, color: SERIES[0] },
    { name: "quadratic fit", kind: "line" as const, color: SERIES[0] },
    ...(hasCurrent ? [{ name: "current catalog values", kind: "dashed" as const, color: SERIES[1] }] : []),
  ];
  return (
    <>
      <Legend items={legend} />
      <div className="mini-grid">
        <XYChart
          title="Thrust coefficient CT"
          xLabel="advance ratio J"
          series={[
            { name: "analysis", kind: "points", color: SERIES[0], data: aero.curve.map((p) => ({ x: p.J, y: p.CT })) },
            { name: "fit", kind: "line", color: SERIES[0], data: grid.filter(within).map((x) => ({ x, y: poly(fitCt, x) })) },
            ...(hasCurrent ? [{ name: "current", kind: "dashed" as const, color: SERIES[1], data: grid.map((x) => ({ x, y: poly(curCt, x) })) }] : []),
          ]}
        />
        <XYChart
          title="Power coefficient CP"
          xLabel="advance ratio J"
          series={[
            { name: "analysis", kind: "points", color: SERIES[0], data: aero.curve.map((p) => ({ x: p.J, y: p.CP })) },
            { name: "fit", kind: "line", color: SERIES[0], data: grid.filter(within).map((x) => ({ x, y: poly(fitCp, x) })) },
            ...(hasCurrent ? [{ name: "current", kind: "dashed" as const, color: SERIES[1], data: grid.map((x) => ({ x, y: poly(curCp, x) })) }] : []),
          ]}
        />
        <XYChart
          title="Efficiency η = J CT / CP"
          xLabel="advance ratio J"
          yMin={0}
          yMax={1}
          series={[
            { name: "analysis", kind: "points", color: SERIES[0], data: aero.curve.filter((p) => p.CT > 0 && p.CP > 0).map((p) => ({ x: p.J, y: p.eta })) },
            ...(hasCurrent
              ? [{ name: "current", kind: "dashed" as const, color: SERIES[1], data: grid.filter((x) => poly(curCt, x) > 0 && poly(curCp, x) > 0).map((x) => ({ x, y: (x * poly(curCt, x)) / poly(curCp, x) })) }]
              : []),
          ]}
        />
        <XYChart
          title="Hover thrust along the blade, dT/dr"
          unit="N/m"
          xLabel="r/R"
          series={[{ name: "dT/dr", kind: "line", color: SERIES[0], data: aero.span.map((s) => ({ x: s.rR, y: s.dT })) }]}
        />
        <XYChart
          title="Angle of attack in hover"
          unit="deg"
          xLabel="r/R"
          series={[
            { name: "α", kind: "line", color: SERIES[0], data: aero.span.map((s) => ({ x: s.rR, y: s.alpha })) },
            { name: "stall", kind: "dashed", color: SERIES[7], data: aero.span.map((s) => ({ x: s.rR, y: stallAt(s.rR) })) },
          ]}
        />
      </div>
    </>
  );
}

function SectionTable({ aero }: { aero: PropellerAeroOutputs }) {
  return (
    <details className="section-polars">
      <summary className="small">Section polars ({aero.sections.length} stations)</summary>
      <table className="table compact">
        <thead>
          <tr>
            <th className="num">r/R</th>
            <th>Airfoil</th>
            <th className="num">Reynolds</th>
            <th className="num">Mach</th>
            <th className="num">Converged</th>
            <th className="num">α stall</th>
            <th className="num">cl max</th>
            <th className="num">cd min</th>
            <th>Polar</th>
          </tr>
        </thead>
        <tbody>
          {aero.sections.map((s) => (
            <tr key={s.station}>
              <td className="mono num">{s.station}</td>
              <td className="mono">{s.airfoil}</td>
              <td className="mono num">{s.reynolds.toLocaleString()}</td>
              <td className="mono num">{s.mach}</td>
              <td className="mono num">{s.source === "xfoil" ? `${s.converged} / ${s.requested}` : "—"}</td>
              <td className="mono num">{s.alphaStall}°</td>
              <td className="mono num">{s.clMax}</td>
              <td className="mono num">{s.cdMin}</td>
              <td className={s.warning ? "warn-text small" : "muted small"}>{s.warning || s.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

// ------------------------------------------------------------------ a small multi-series chart
interface Series {
  name: string;
  kind: "line" | "dashed" | "points";
  color: string;
  data: { x: number; y: number }[];
}

function Legend({ items }: { items: { name: string; kind: Series["kind"]; color: string }[] }) {
  return (
    <div className="chart-legend small">
      {items.map((i) => (
        <span key={i.name} className="legend-item">
          <svg width="22" height="10" aria-hidden="true">
            {i.kind === "points" ? (
              <circle cx="11" cy="5" r="3.5" fill={i.color} />
            ) : (
              <line x1="1" x2="21" y1="5" y2="5" stroke={i.color} strokeWidth="2" strokeDasharray={i.kind === "dashed" ? "4 3" : undefined} />
            )}
          </svg>
          {i.name}
        </span>
      ))}
    </div>
  );
}

const CW = 300;
const CH = 200;
const CM = { top: 10, right: 12, bottom: 34, left: 50 };

function XYChart({ title, unit, xLabel, series, yMin, yMax }: { title: string; unit?: string; xLabel: string; series: Series[]; yMin?: number; yMax?: number }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hx, setHx] = useState<number | null>(null);
  const all = series.flatMap((s) => s.data);
  if (!all.length) return null;
  const xs = all.map((p) => p.x);
  const ys = all.map((p) => p.y);
  const xt = ticks(Math.min(0, ...xs), Math.max(...xs));
  const yt = ticks(yMin ?? Math.min(0, ...ys), yMax ?? Math.max(...ys));
  const [x0, x1, y0, y1] = [xt[0], xt[xt.length - 1], yt[0], yt[yt.length - 1]];
  const iw = CW - CM.left - CM.right;
  const ih = CH - CM.top - CM.bottom;
  const X = (v: number) => CM.left + ((v - x0) / (x1 - x0 || 1)) * iw;
  const Y = (v: number) => CM.top + (1 - (Math.min(Math.max(v, y0), y1) - y0) / (y1 - y0 || 1)) * ih;
  const at = (s: Series, x: number) => {
    const d = s.data;
    if (!d.length || x < d[0].x || x > d[d.length - 1].x) return null;
    const i = d.findIndex((p) => p.x >= x);
    if (i <= 0) return d[0].y;
    const a = d[i - 1], b = d[i];
    return a.y + ((x - a.x) / (b.x - a.x || 1)) * (b.y - a.y);
  };
  const move = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * CW;
    setHx(px < CM.left || px > CW - CM.right ? null : x0 + ((px - CM.left) / iw) * (x1 - x0));
  };
  const f = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString());
  return (
    <div className="mini">
      <div className="mini-title">
        <span className="strong small">{title}</span> {unit && <span className="muted small">{unit}</span>}
      </div>
      <div className="chart-wrap">
        <svg ref={svg} viewBox={`0 0 ${CW} ${CH}`} className="chart" onPointerMove={move} onPointerLeave={() => setHx(null)} role="img" aria-label={`${title} against ${xLabel}`}>
          {yt.map((v) => (
            <g key={v}>
              <line x1={CM.left} x2={CW - CM.right} y1={Y(v)} y2={Y(v)} className={v === 0 ? "ref" : "grid"} />
              <text x={CM.left - 5} y={Y(v) + 4} className="tick" textAnchor="end">
                {f(v)}
              </text>
            </g>
          ))}
          {xt.map((v) => (
            <text key={v} x={X(v)} y={CH - CM.bottom + 14} className="tick" textAnchor="middle">
              {f(v)}
            </text>
          ))}
          <text x={CM.left + iw / 2} y={CH - 4} className="tick" textAnchor="middle">
            {xLabel}
          </text>
          {series.map((s) =>
            s.kind === "points" ? (
              <g key={s.name}>
                {s.data.map((p, i) => (
                  <circle key={i} cx={X(p.x)} cy={Y(p.y)} r={2.6} fill={s.color} />
                ))}
              </g>
            ) : (
              <polyline
                key={s.name}
                points={s.data.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ")}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeDasharray={s.kind === "dashed" ? "5 4" : undefined}
                strokeLinejoin="round"
              />
            )
          )}
          {hx !== null && <line x1={X(hx)} x2={X(hx)} y1={CM.top} y2={CH - CM.bottom} className="crosshair" />}
        </svg>
        {hx !== null && (
          <div className="chart-tip mini-tip" style={{ left: `${(X(hx) / CW) * 100}%`, transform: X(hx) > CW * 0.5 ? "translateX(calc(-100% - 12px))" : undefined }}>
            <div className="small mono">
              {xLabel} {f(hx)}
            </div>
            {series.map((s) => {
              const v = at(s, hx);
              return v === null ? null : (
                <div key={s.name} className="small mono">
                  <span style={{ color: s.color }}>●</span> {s.name} <span className="strong">{f(v)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** Round axis ticks spanning [lo, hi], about five of them. */
function ticks(lo: number, hi: number): number[] {
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const start = Math.floor(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v < hi + step * 0.999; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}
