import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { isDynamics, type DynamicsOutputs, type MissionSimulationDef, type SimulationOutputs, type SimulationResult, type SimulationRun, type StepRequest, type SystemConfiguration } from "../types";
import { errorText, short } from "./catalogUtil";
import { DynamicsDetails } from "./DynamicsResults";

/** Categorical slots in fixed order (validated: adjacent CVD ΔE ≥ 9.1 on white).
 *  Three sit below 3:1 contrast, so identity is also carried by the legend,
 *  the table and the tooltip. A system keeps its slot whatever is shown. */
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];

const W = 720;
const H = 260;
const M = { top: 12, right: 16, bottom: 30, left: 40 };

interface Props {
  projectId: string;
  missionType: string;
  /** a saved mission, or null to simulate the draft steps */
  mission: string | null;
  draft: StepRequest[] | null;
  label: string;
  /** every system instance of the project, in catalog order (fixes each one's color) */
  systems: SystemConfiguration[];
  /** the project's simulation defs (mission simulator, dynamic simulator...) */
  simulations: MissionSimulationDef[];
  ensureSaved(): Promise<boolean>;
  /** re-simulate when this changes (the catalog after a save) */
  refreshKey: unknown;
  disabled?: boolean;
}

/** Runs the mission simulation (an external analysis, through the model service)
 *  for every system instance that can fly the mission and shows battery energy,
 *  percent of the pack, mission time and the state of charge over time. */
export function MissionSimulation({ projectId, missionType, mission, draft, label, systems, simulations, ensureSaved, refreshKey, disabled }: Props) {
  const [simQn, setSimQn] = useState<string | null>(simulations[0]?.qualifiedName ?? null);
  const simDef = simulations.find((s) => s.qualifiedName === simQn) || simulations[0];
  const [res, setRes] = useState<SimulationResult | null>(null);
  const [forKey, setForKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const key = `${simDef?.qualifiedName}|${mission || `draft:${JSON.stringify(draft)}`}`;
  const color = (qn: string) => SERIES[Math.max(0, systems.findIndex((s) => s.qualifiedName === qn)) % SERIES.length];

  const run = async () => {
    if (!(await ensureSaved())) return;
    setBusy(true);
    setError(null);
    try {
      setRes(await api.simulateMission(projectId, { missionType, simulation: simDef?.qualifiedName ?? null, mission, draft: mission ? null : draft, systems: null }));
      setForKey(key);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  // a result on screen follows the model: re-run after every save
  useEffect(() => {
    if (res && forKey === key) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  // switching simulations re-runs the one picked when a result was on screen
  const pick = (qn: string) => {
    setSimQn(qn);
    if (res) setForKey(null);
  };

  const current = res && forKey === key ? res : null;
  const flown = useMemo(
    () => Object.entries(current?.systems || {}).filter((e): e is Flown => !!e[1].outputs),
    [current]
  );
  const dynamic = flown.filter((e): e is [string, SimulationRun & { outputs: DynamicsOutputs }] => isDynamics(e[1].outputs));
  const others = Object.entries(current?.systems || {}).filter(([, r]) => !r.outputs);

  return (
    <div className="sim">
      <div className="section-title row-between">
        <span className="row-end">
          <span>Mission simulation</span>
          {simulations.length > 1 && (
            <span className="seg small-seg" role="group" aria-label="Simulation">
              {simulations.map((s) => (
                <button key={s.qualifiedName} className={s.qualifiedName === simDef?.qualifiedName ? "on" : ""} title={`${s.doc || ""}\nanalysis: ${s.toolName}${s.verifies.length ? `\nverifies ${s.verifies.map(short).join(", ")}` : ""}`} onClick={() => pick(s.qualifiedName)}>
                  {s.name}
                </button>
              ))}
            </span>
          )}
        </span>
        <span className="row-end">
          {current && <span className="muted small">run {current.runId.slice(0, 8)} · logged by the analysis service</span>}
          <button className="primary" disabled={busy || disabled || !simDef} onClick={run} title={`Fly the mission with every system instance in ${simDef?.name} (external analysis ${simDef?.toolName})`}>
            {busy ? "Simulating…" : `▶ Simulate ${label}`}
          </button>
        </span>
      </div>
      {error && <div className="error-box">{error}</div>}
      {!current && !error && (
        <div className="hint">
          {simDef?.doc ? `${simDef.name}: ${simDef.doc} ` : ""}
          Simulates the mission step by step for every system instance, with each one's own behaviors: battery energy drawn, its share of the pack, the mission time and the state of charge over time
          {simDef?.toolName === "dynamicsSimulator" ? ", plus the energy and power of every behavior and where the propellers and motors operate on their performance curves." : "."}
        </div>
      )}
      {current && flown.length > 0 && (
        <>
          <div className="legend">
            {flown.map(([qn]) => (
              <span key={qn} className={`legend-item${focus && focus !== qn ? " dim" : ""}`} onMouseEnter={() => setFocus(qn)} onMouseLeave={() => setFocus(null)}>
                <span className="legend-key" style={{ background: color(qn) }} />
                {short(qn)}
              </span>
            ))}
          </div>
          <SocChart runs={flown} color={color} focus={focus} setFocus={setFocus} reserve={(flown[0][1].inputs?.reserve as number | undefined) ?? null} />
          <div className="table-scroll">
            <table className="table compact">
              <thead>
                <tr>
                  <th>System</th>
                  <th className="num">Energy used</th>
                  <th>Share of battery</th>
                  <th className="num">Mission time</th>
                  <th className="num">Charge left</th>
                  <th className="num">Peak power</th>
                  {dynamic.length > 0 && <th className="num" title="Highest motor voltage / battery bus voltage during the mission; above 100 % the motors cannot follow">Max throttle</th>}
                  <th title="The requirements this simulation verifies, checked with its results">Verifies</th>
                </tr>
              </thead>
              <tbody>
                {flown.map(([qn, r]) => {
                  const o = r.outputs;
                  const reserve = 100 * ((r.inputs?.reserve as number) ?? 0);
                  return (
                    <tr key={qn} className={focus === qn ? "row-on" : ""} onMouseEnter={() => setFocus(qn)} onMouseLeave={() => setFocus(null)}>
                      <td className="nowrap">
                        <span className="legend-key" style={{ background: color(qn) }} /> <span className="mono strong">{short(qn)}</span>
                        {r.resultId && (
                          <div className="small">
                            <a href={api.resultUrl(r.resultId)} target="_blank" rel="noreferrer" title={`Stored result ${r.resultId}${r.reused ? ": outputs reused from an earlier result with the same inputs" : ""}`}>
                              data ↗
                            </a>
                            {r.reused && <span className="muted"> · reused</span>}
                          </div>
                        )}
                      </td>
                      <td className="mono num">
                        {o.energyUsed.toFixed(0)} <span className="muted">/ {Number(r.inputs?.batteryEnergy ?? 0).toFixed(0)} Wh</span>
                      </td>
                      <td>
                        <div className="share" title={`${o.energyPercent.toFixed(1)} % of the pack; the tick is the ${reserve.toFixed(0)} % reserve line`}>
                          <div className="share-bar">
                            <div className="share-fill" style={{ width: `${Math.min(o.energyPercent, 100)}%` }} />
                            {reserve > 0 && <div className="share-tick" style={{ left: `${100 - reserve}%` }} />}
                          </div>
                          <span className="mono small">{o.energyPercent.toFixed(1)} %</span>
                        </div>
                      </td>
                      <td className="mono num">{clock(o.missionTime)}</td>
                      <td className="mono num">{o.endStateOfCharge.toFixed(1)} %</td>
                      <td className="mono num">
                        {o.peakPower.toFixed(0)} <span className="muted">/ {Number(r.inputs?.batteryMaxPower ?? 0).toFixed(0)} W</span>
                      </td>
                      {dynamic.length > 0 && <td className="mono num">{isDynamics(o) ? `${(100 * o.maxThrottle).toFixed(0)} %` : "—"}</td>}
                      <td>
                        <div className="chips">
                          {Object.entries(r.verdicts || {}).map(([q, v]) => (
                            <span key={q} className={`pill ${v === "holds" ? "ok" : "bad"}`} title={r.verification?.[q]?.note || `verified with ${Object.keys(r.verification?.[q]?.values || {}).join(", ")}`}>
                              {v === "holds" ? "✓" : v === "fails" ? "✗" : "?"} {short(q)}
                            </span>
                          ))}
                          {!Object.keys(r.verdicts || {}).length && (
                            <span className={`pill ${o.feasible ? "ok" : "bad"}`}>{o.feasible ? "✓ within limits" : o.completed ? "✗ below reserve" : "✗ battery empty"}</span>
                          )}
                        </div>
                        {o.issues.length > 0 && <div className="muted small">{o.issues.join("; ")}</div>}
                      </td>
                    </tr>
                  );
                })}
                {others.map(([qn, r]) => (
                  <tr key={qn}>
                    <td className="nowrap">
                      <span className="legend-key" style={{ background: color(qn) }} /> <span className="mono strong">{short(qn)}</span>
                    </td>
                    <td colSpan={dynamic.length ? 7 : 6} className="muted small">
                      ⊘ {r.error || r.problems.join("; ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {dynamic.length > 0 && <DynamicsDetails runs={dynamic} color={color} focus={focus} setFocus={setFocus} />}
        </>
      )}
      {current && !flown.length && <div className="muted small">No system instance can fly it: {others.map(([qn, r]) => `${short(qn)}: ${r.error || r.problems.join("; ")}`).join(" · ")}</div>}
      {current && current.errors.length > 0 && <div className="error-box">{current.errors.join("\n")}</div>}
    </div>
  );
}

type Flown = [string, SimulationRun & { outputs: SimulationOutputs | DynamicsOutputs }];

/** State of charge (%) over mission time, one 2px line per system, the reserve
 *  as a labelled hairline, and a crosshair tooltip with every system's charge. */
export function SocChart({ runs, color, focus, setFocus, reserve }: { runs: Flown[]; color(qn: string): string; focus: string | null; setFocus(qn: string | null): void; reserve: number | null }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hoverT, setHoverT] = useState<number | null>(null);
  const tMax = Math.max(...runs.map(([, r]) => r.outputs.missionTime), 1);
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (t: number) => M.left + (t / tMax) * iw;
  const y = (soc: number) => M.top + (1 - soc / 100) * ih;
  const minuteStep = niceStep(tMax / 60);
  const ticks = Array.from({ length: Math.floor(tMax / 60 / minuteStep) + 1 }, (_, i) => i * minuteStep);

  const at = (r: Flown[1], t: number) => {
    const tl = timeline(r);
    if (t > tl[tl.length - 1].t) return null; // the flight is over (or the pack was empty)
    let i = tl.findIndex((p) => p.t >= t);
    if (i <= 0) return { soc: tl[0].soc, step: tl[Math.max(i, 0)].step };
    const a = tl[i - 1], b = tl[i];
    const f = (t - a.t) / (b.t - a.t || 1);
    return { soc: a.soc + f * (b.soc - a.soc), step: b.step };
  };
  const move = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    setHoverT(px < M.left || px > W - M.right ? null : ((px - M.left) / iw) * tMax);
  };
  const tip = hoverT !== null ? runs.map(([qn, r]) => ({ qn, v: at(r, hoverT) })).sort((a, b) => (b.v?.soc ?? -1) - (a.v?.soc ?? -1)) : [];

  return (
    <div className="chart-wrap">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="chart" onPointerMove={move} onPointerLeave={() => setHoverT(null)} role="img" aria-label="Battery state of charge over mission time, per system">
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={M.left - 6} y={y(v) + 4} className="tick" textAnchor="end">
              {v}%
            </text>
          </g>
        ))}
        {ticks.map((m) => (
          <text key={m} x={x(m * 60)} y={H - M.bottom + 16} className="tick" textAnchor={x(m * 60) > W - M.right - 24 ? "end" : "middle"}>
            {m} min
          </text>
        ))}
        {reserve !== null && reserve > 0 && (
          <g>
            <line x1={M.left} x2={W - M.right} y1={y(100 * reserve)} y2={y(100 * reserve)} className="ref" />
            <text x={W - M.right} y={y(100 * reserve) - 4} className="tick" textAnchor="end">
              {(100 * reserve).toFixed(0)} % reserve
            </text>
          </g>
        )}
        {runs.map(([qn, r]) => {
          const tl = timeline(r);
          const last = tl[tl.length - 1];
          return (
            <g key={qn} opacity={focus && focus !== qn ? 0.2 : 1} onPointerEnter={() => setFocus(qn)} onPointerLeave={() => setFocus(null)}>
              <polyline points={tl.map((p) => `${x(p.t).toFixed(1)},${y(p.soc).toFixed(1)}`).join(" ")} fill="none" stroke={color(qn)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={x(last.t)} cy={y(last.soc)} r={4} fill={color(qn)} stroke="var(--surface)" strokeWidth={2} />
            </g>
          );
        })}
        {hoverT !== null && <line x1={x(hoverT)} x2={x(hoverT)} y1={M.top} y2={H - M.bottom} className="crosshair" />}
      </svg>
      {hoverT !== null && (
        <div className="chart-tip" style={{ left: `${(x(hoverT) / W) * 100}%`, transform: x(hoverT) > W * 0.6 ? "translateX(calc(-100% - 12px))" : undefined }}>
          <div className="strong small">{clock(hoverT)}</div>
          {tip.map(({ qn, v }) => (
            <div key={qn} className="tip-row small">
              <span className="legend-key" style={{ background: color(qn) }} />
              <span className="mono">{short(qn)}</span>
              <span className="mono num">{v ? `${v.soc.toFixed(1)} %` : "landed"}</span>
              <span className="muted">{v?.step || ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function timeline(r: Flown[1]): { t: number; soc: number; step: string | null }[] {
  return r.outputs.timeline;
}

export function niceStep(minutes: number): number {
  for (const s of [1, 2, 5, 10, 15, 30, 60]) if (minutes / s <= 8) return s;
  return 120;
}

/** seconds -> "m:ss" */
export function clock(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
