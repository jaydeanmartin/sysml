import { useMemo, useRef, useState } from "react";
import type { DynamicsOutputs, OperatingPoint, SimulationRun } from "../types";
import { short } from "./catalogUtil";
import { clock, niceStep } from "./MissionSimulation";

type Run = [string, SimulationRun & { outputs: DynamicsOutputs }];

interface Props {
  runs: Run[];
  color(qn: string): string;
  focus: string | null;
  setFocus(qn: string | null): void;
}

/** What the dynamic simulation adds to the mission summary: energy and power of
 *  each behavior, electrical power over time, and the propeller and motor
 *  performance curves with each behavior's operating point on them. */
export function DynamicsDetails({ runs, color, focus, setFocus }: Props) {
  const [sel, setSel] = useState<string | null>(null);
  const run = runs.find(([qn]) => qn === sel) || runs[0];
  // behaviors in the order the mission first performs them
  const behaviors = useMemo(() => [...new Set(runs.flatMap(([, r]) => r.outputs.flownSteps.map((s) => s.behavior)))], [runs]);

  return (
    <>
      <div className="section-title">Energy and power by behavior</div>
      <div className="hint small">Energy drawn from the battery in each behavior, summed over the mission's steps; the bar is the behavior's share of that system's mission energy.</div>
      <div className="table-scroll">
        <table className="table compact behavior-table">
          <thead>
            <tr>
              <th>System</th>
              {behaviors.map((b) => (
                <th key={b} className="num">
                  {b}
                </th>
              ))}
              <th className="num">Mission</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(([qn, r]) => {
              const by = new Map(r.outputs.behaviors.map((b) => [b.behavior, b]));
              return (
                <tr key={qn} className={focus === qn ? "row-on" : ""} onMouseEnter={() => setFocus(qn)} onMouseLeave={() => setFocus(null)}>
                  <td className="nowrap">
                    <span className="legend-key" style={{ background: color(qn) }} /> <span className="mono strong">{short(qn)}</span>
                  </td>
                  {behaviors.map((name) => {
                    const b = by.get(name);
                    if (!b) return <td key={name} className="muted num">—</td>;
                    const op = b.operatingPoint;
                    const tip = [
                      `${name}: ${clock(b.duration)} · ${b.energy.toFixed(1)} Wh (${((100 * b.energy) / r.outputs.energyUsed).toFixed(0)} % of the mission)`,
                      `power ${b.averagePower.toFixed(0)} W average, ${b.peakPower.toFixed(0)} W peak`,
                      op ? `${op.rpm.toFixed(0)} rpm · J ${op.J.toFixed(3)} · ${(100 * op.motorEfficiency).toFixed(0)} % motor efficiency · max throttle ${(100 * b.maxThrottle).toFixed(0)} %` : "on the ground: avionics only",
                    ].join("\n");
                    return (
                      <td key={name} className="num" title={tip}>
                        <div className="mono strong">{b.energy.toFixed(1)} Wh</div>
                        {/* the behavior's share of this system's mission energy */}
                        <div className="energy-bar" aria-hidden>
                          <div style={{ width: `${(100 * b.energy) / (r.outputs.energyUsed || 1)}%` }} />
                        </div>
                        <div className="mono small muted">{powerText(b.averagePower, b.peakPower)}</div>
                      </td>
                    );
                  })}
                  <td className="num">
                    <div className="mono strong">{r.outputs.energyUsed.toFixed(1)} Wh</div>
                    <div className="mono small muted">{powerText(r.outputs.averagePower, r.outputs.peakPower)}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="section-title">Electrical power over time</div>
      <PowerChart runs={runs} color={color} focus={focus} setFocus={setFocus} />

      <div className="section-title row-between">
        <span>Propeller and motor performance</span>
        {runs.length > 1 && (
          <label className="inline-label">
            <span>for</span>
            <select value={run[0]} onChange={(e) => setSel(e.target.value)}>
              {runs.map(([qn]) => (
                <option key={qn} value={qn}>
                  {short(qn)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <ComponentCurves run={run} />
    </>
  );
}

/** "1.16 kW avg · 1.77 kW peak", or one value when the power is steady */
function powerText(avg: number, peak: number): string {
  return peak - avg < 0.01 * peak ? `${kw(avg)} steady` : `${kw(avg)} avg · ${kw(peak)} peak`;
}

function kw(w: number): string {
  return w >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${w.toFixed(0)} W`;
}

// ------------------------------------------------------------------ power over time
const PW = 720;
const PH = 220;
const PM = { top: 12, right: 16, bottom: 30, left: 52 };

function PowerChart({ runs, color, focus, setFocus }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [hoverT, setHoverT] = useState<number | null>(null);
  const tMax = Math.max(...runs.map(([, r]) => r.outputs.missionTime), 1);
  const pMax = Math.max(...runs.flatMap(([, r]) => r.outputs.timeline.map((p) => p.power)), 1);
  const yt = ticks(0, pMax);
  const top = yt[yt.length - 1];
  const iw = PW - PM.left - PM.right;
  const ih = PH - PM.top - PM.bottom;
  const x = (t: number) => PM.left + (t / tMax) * iw;
  const y = (p: number) => PM.top + (1 - p / top) * ih;
  const minuteStep = niceStep(tMax / 60);
  const xt = Array.from({ length: Math.floor(tMax / 60 / minuteStep) + 1 }, (_, i) => i * minuteStep);

  const at = (r: Run[1], t: number) => {
    const tl = r.outputs.timeline;
    if (t > tl[tl.length - 1].t) return null;
    const i = tl.findIndex((p) => p.t >= t);
    return i < 0 ? null : tl[i];
  };
  const move = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * PW;
    setHoverT(px < PM.left || px > PW - PM.right ? null : ((px - PM.left) / iw) * tMax);
  };

  return (
    <div className="chart-wrap">
      <svg ref={svg} viewBox={`0 0 ${PW} ${PH}`} className="chart" onPointerMove={move} onPointerLeave={() => setHoverT(null)} role="img" aria-label="Electrical power drawn from the battery over mission time, per system">
        {yt.map((v) => (
          <g key={v}>
            <line x1={PM.left} x2={PW - PM.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={PM.left - 6} y={y(v) + 4} className="tick" textAnchor="end">
              {kw(v)}
            </text>
          </g>
        ))}
        {xt.map((m) => (
          <text key={m} x={x(m * 60)} y={PH - PM.bottom + 16} className="tick" textAnchor={x(m * 60) > PW - PM.right - 24 ? "end" : "middle"}>
            {m} min
          </text>
        ))}
        {runs.map(([qn, r]) => (
          <polyline
            key={qn}
            points={r.outputs.timeline.map((p) => `${x(p.t).toFixed(1)},${y(p.power).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={color(qn)}
            strokeWidth={2}
            strokeLinejoin="round"
            opacity={focus && focus !== qn ? 0.15 : 1}
            onPointerEnter={() => setFocus(qn)}
            onPointerLeave={() => setFocus(null)}
          />
        ))}
        {hoverT !== null && <line x1={x(hoverT)} x2={x(hoverT)} y1={PM.top} y2={PH - PM.bottom} className="crosshair" />}
      </svg>
      {hoverT !== null && (
        <div className="chart-tip" style={{ left: `${(x(hoverT) / PW) * 100}%`, transform: x(hoverT) > PW * 0.6 ? "translateX(calc(-100% - 12px))" : undefined }}>
          <div className="strong small">{clock(hoverT)}</div>
          {runs.map(([qn, r]) => {
            const p = at(r, hoverT);
            return (
              <div key={qn} className="tip-row small">
                <span className="legend-key" style={{ background: color(qn) }} />
                <span className="mono">{short(qn)}</span>
                <span className="mono num">{p ? kw(p.power) : "landed"}</span>
                <span className="muted">{p ? `${p.step || ""}${p.rpm ? ` · ${p.rpm.toFixed(0)} rpm` : ""}` : ""}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ component curves
interface Point {
  label: string;
  x: number;
  y: number;
  detail: string[];
}

/** The propeller's coefficients against advance ratio and the motor's
 *  characteristics against current, with every behavior's operating point. */
function ComponentCurves({ run }: { run: Run }) {
  const [, r] = run;
  const o = r.outputs;
  const ops = o.behaviors.filter((b): b is typeof b & { operatingPoint: OperatingPoint } => !!b.operatingPoint);
  const detail = (b: (typeof ops)[number]) => {
    const p = b.operatingPoint;
    return [
      `J ${p.J.toFixed(3)} · CT ${p.CT.toFixed(4)} · CP ${p.CP.toFixed(4)}`,
      `${p.rpm.toFixed(0)} rpm · ${p.thrust.toFixed(1)} N thrust · ${p.torque.toFixed(3)} N·m per rotor`,
      `${p.shaftPower.toFixed(0)} W shaft · ${p.thrustPerPower.toFixed(3)} N/W · η ${(100 * p.propEfficiency).toFixed(1)} %`,
      `${p.current.toFixed(1)} A · ${p.motorVoltage.toFixed(1)} V · motor ${(100 * p.motorEfficiency).toFixed(1)} % · throttle ${(100 * p.throttle).toFixed(0)} %`,
      `tilt ${p.tilt.toFixed(1)}° · ${p.speed.toFixed(1)} m/s · ${b.averagePower.toFixed(0)} W from the battery`,
    ];
  };
  const pts = (fx: (p: OperatingPoint) => number, fy: (p: OperatingPoint) => number): Point[] =>
    ops.map((b) => ({ label: b.behavior, x: fx(b.operatingPoint), y: fy(b.operatingPoint), detail: detail(b) }));
  const pc = o.propellerCurves;
  const mc = o.motorCurves;
  const inches = o.reference.propDiameter / 0.0254;
  return (
    <>
      <div className="hint small">
        Propeller {inches.toFixed(0)} in, against the advance ratio J = V / (n D), where V is the airspeed along the rotor axis. The coefficients do not depend on rotor speed, so every operating point lies on its curve. Hover is J = 0, where efficiency J·CT/CP is zero by definition and thrust/power is what counts. Points are each behavior's time-averaged operating point while airborne. Hover rotor speed at takeoff mass: {o.reference.hoverRpm.toFixed(0)} rpm.
      </div>
      <div className="multiples">
        <MiniChart title="Torque coefficient" unit="CQ = CP / 2π" xLabel="advance ratio J" curve={pc.map((p) => ({ x: p.J, y: p.CQ }))} points={pts((p) => p.J, (p) => p.CQ)} fmtY={(v) => v.toFixed(4)} fmtX={(v) => v.toFixed(2)} />
        <MiniChart title="Thrust / power" unit="CT / CP" xLabel="advance ratio J" curve={pc.map((p) => ({ x: p.J, y: p.CTCP }))} points={pts((p) => p.J, (p) => p.CTCP)} fmtY={(v) => v.toFixed(2)} fmtX={(v) => v.toFixed(2)} />
        <MiniChart title="Propeller efficiency" unit="η = J CT / CP" xLabel="advance ratio J" curve={pc.map((p) => ({ x: p.J, y: 100 * p.efficiency }))} points={pts((p) => p.J, (p) => 100 * p.propEfficiency)} fmtY={(v) => `${v.toFixed(0)} %`} fmtX={(v) => v.toFixed(2)} />
      </div>
      <div className="hint small">
        Motor at full throttle on the battery's nominal {o.reference.batteryVoltage.toFixed(1)} V, against the current per motor (up to its {o.reference.motorMaxCurrent.toFixed(0)} A maximum). Torque follows the current at any throttle, so those points lie on the line. Speed, power and efficiency are drawn at full voltage, so points at part throttle sit below those curves.
      </div>
      <div className="multiples">
        <MiniChart title="Torque" unit="N·m" xLabel="current (A)" curve={mc.map((m) => ({ x: m.current, y: m.torque }))} points={pts((p) => p.current, (p) => p.torque)} fmtY={(v) => v.toFixed(2)} fmtX={(v) => v.toFixed(0)} />
        <MiniChart title="Speed" unit="rpm" xLabel="current (A)" curve={mc.map((m) => ({ x: m.current, y: m.speed }))} points={pts((p) => p.current, (p) => p.rpm)} fmtY={(v) => v.toFixed(0)} fmtX={(v) => v.toFixed(0)} />
        <MiniChart title="Shaft power" unit="W" xLabel="current (A)" curve={mc.map((m) => ({ x: m.current, y: m.power }))} points={pts((p) => p.current, (p) => p.shaftPower)} fmtY={(v) => v.toFixed(0)} fmtX={(v) => v.toFixed(0)} />
        <MiniChart title="Motor efficiency" unit="%" xLabel="current (A)" curve={mc.map((m) => ({ x: m.current, y: 100 * m.efficiency }))} points={pts((p) => p.current, (p) => 100 * p.motorEfficiency)} fmtY={(v) => `${v.toFixed(0)} %`} fmtX={(v) => v.toFixed(0)} />
      </div>
    </>
  );
}

const MW = 280;
const MH = 190;
const MM = { top: 10, right: 12, bottom: 34, left: 50 };

/** One measure against one x: the curve as a 2px line, operating points as
 *  ringed markers labelled with their behaviors, a crosshair that reads the
 *  curve, and a tooltip with the full operating point under the pointer. */
function MiniChart({ title, unit, xLabel, curve, points, fmtX, fmtY }: { title: string; unit: string; xLabel: string; curve: { x: number; y: number }[]; points: Point[]; fmtX(v: number): string; fmtY(v: number): string }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ px: number; x: number; point: Point[] | null } | null>(null);
  const xs = [...curve.map((c) => c.x), ...points.map((p) => p.x)];
  const ys = [...curve.map((c) => c.y), ...points.map((p) => p.y)];
  const xt = ticks(floor0(xs), Math.max(...xs));
  const yt = ticks(floor0(ys), Math.max(...ys));
  const [x0, x1, y0, y1] = [xt[0], xt[xt.length - 1], yt[0], yt[yt.length - 1]];
  const iw = MW - MM.left - MM.right;
  const ih = MH - MM.top - MM.bottom;
  const X = (v: number) => MM.left + ((v - x0) / (x1 - x0 || 1)) * iw;
  const Y = (v: number) => MM.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;

  // points closer than a label's height share one label
  const groups: Point[][] = [];
  for (const p of [...points].sort((a, b) => X(a.x) - X(b.x))) {
    const g = groups.find((gr) => Math.abs(X(gr[0].x) - X(p.x)) < 14 && Math.abs(Y(gr[0].y) - Y(p.y)) < 12);
    if (g) g.push(p);
    else groups.push([p]);
  }
  // labels: to the right of the point (left near the right edge), moved down
  // a line at a time until they clear the labels already placed
  const placed: { x0: number; x1: number; y: number }[] = [];
  const labels = groups.map((g) => {
    const text = g.map((p) => p.label).join(", ");
    const px = X(g[0].x), py = Y(g[0].y);
    const w = text.length * 5.8;
    const right = px < MW - MM.right - w - 8;
    const x0 = right ? px + 7 : px - 7 - w;
    let y = py - 6;
    while (placed.some((b) => b.x0 < x0 + w && x0 < b.x1 && Math.abs(b.y - y) < 12)) y += 12;
    placed.push({ x0, x1: x0 + w, y });
    return { text, x: right ? px + 7 : px - 7, y, anchor: right ? "start" : "end" };
  });
  const curveAt = (v: number) => {
    const i = curve.findIndex((c) => c.x >= v);
    if (i <= 0) return curve[Math.max(i, 0)]?.y ?? null;
    const a = curve[i - 1], b = curve[i];
    return a.y + ((v - a.x) / (b.x - a.x || 1)) * (b.y - a.y);
  };
  const move = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * MW;
    const py = ((e.clientY - r.top) / r.height) * MH;
    if (px < MM.left || px > MW - MM.right) return setHover(null);
    const near = groups.find((g) => Math.hypot(X(g[0].x) - px, Y(g[0].y) - py) < 14) || null;
    setHover({ px, x: x0 + ((px - MM.left) / iw) * (x1 - x0), point: near });
  };
  const cy = hover ? curveAt(hover.x) : null;

  return (
    <div className="mini">
      <div className="mini-title">
        <span className="strong small">{title}</span> <span className="muted small">{unit}</span>
      </div>
      <div className="chart-wrap">
        <svg ref={svg} viewBox={`0 0 ${MW} ${MH}`} className="chart" onPointerMove={move} onPointerLeave={() => setHover(null)} role="img" aria-label={`${title} against ${xLabel}, with operating points ${points.map((p) => p.label).join(", ")}`}>
          {yt.map((v) => (
            <g key={v}>
              <line x1={MM.left} x2={MW - MM.right} y1={Y(v)} y2={Y(v)} className="grid" />
              <text x={MM.left - 5} y={Y(v) + 4} className="tick" textAnchor="end">
                {fmtY(v)}
              </text>
            </g>
          ))}
          {xt.map((v) => (
            <text key={v} x={X(v)} y={MH - MM.bottom + 14} className="tick" textAnchor="middle">
              {fmtX(v)}
            </text>
          ))}
          <text x={MM.left + iw / 2} y={MH - 4} className="tick" textAnchor="middle">
            {xLabel}
          </text>
          <polyline points={curve.map((c) => `${X(c.x).toFixed(1)},${Y(c.y).toFixed(1)}`).join(" ")} fill="none" className="curve" strokeWidth={2} strokeLinejoin="round" />
          {hover && <line x1={X(hover.x)} x2={X(hover.x)} y1={MM.top} y2={MH - MM.bottom} className="crosshair" />}
          {groups.map((g, k) => (
            <g key={labels[k].text}>
              <circle cx={X(g[0].x)} cy={Y(g[0].y)} r={4.5} className={`op-point${hover?.point === g ? " on" : ""}`} />
              <text x={labels[k].x} y={labels[k].y} className="op-label" textAnchor={labels[k].anchor}>
                {labels[k].text}
              </text>
            </g>
          ))}
        </svg>
        {hover && (
          <div className="chart-tip mini-tip" style={{ left: `${(hover.px / MW) * 100}%`, transform: hover.px > MW * 0.5 ? "translateX(calc(-100% - 12px))" : undefined }}>
            {hover.point ? (
              hover.point.map((p) => (
                <div key={p.label} className="small">
                  <div className="strong">{p.label}</div>
                  {p.detail.map((d) => (
                    <div key={d} className="mono muted">
                      {d}
                    </div>
                  ))}
                </div>
              ))
            ) : (
              <div className="small mono">
                {xLabel.split(" (")[0]} {fmtX(hover.x)} → <span className="strong">{cy === null ? "—" : fmtY(cy)}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** The axis floor: 0, unless values go meaningfully below it (not rounding noise). */
function floor0(vs: number[]): number {
  const lo = Math.min(0, ...vs);
  const span = Math.max(...vs.map(Math.abs), 1e-12);
  return lo > -1e-6 * span ? 0 : lo;
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
