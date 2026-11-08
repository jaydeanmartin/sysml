import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Architecture, Catalog, CatalogChange, ControlOutputs, ControlScenario, EvidenceRecord, Located, SystemRun, SystemSimulationResult, TuneResult } from "../types";
import { NAME_RE, errorText, short } from "./catalogUtil";
import { MiniChart } from "./DynamicsResults";

/** units of the FlightController gains, for the tuning table */
const GAIN_UNITS: Record<string, string> = {
  rpmKp: "V·s/rad", rpmKi: "V/rad", rateKp: "N·m·s/rad", rateKi: "N·m/rad", rateKd: "N·m·s²/rad", yawRateKp: "N·m·s/rad", yawRateKi: "N·m/rad",
  attitudeKp: "1/s", headingKp: "1/s", velocityKp: "1/s", velocityKi: "1/s²", verticalKp: "1/s", verticalKi: "1/s²", positionKp: "1/s",
};
const LOOPS: [string, string][] = [
  ["rpm", "ESC speed"], ["roll", "roll rate"], ["pitch", "pitch rate"], ["yaw", "yaw rate"],
  ["attitude", "attitude"], ["heading", "heading"], ["velocity", "velocity"], ["vertical", "vertical speed"],
];

interface Props {
  projectId: string;
  catalog: Catalog;
  arch: Architecture;
  /** the system instance selected in the Configurator */
  selected: string | null;
  onSelect(qn: string): void;
  ensureSaved(): Promise<boolean>;
  onChange(fn: () => Promise<CatalogChange>): Promise<CatalogChange | null>;
  onOpen(el: Located): void;
}

const fmt = (v: unknown, digits = 2) => (typeof v === "number" && Number.isFinite(v) ? (Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString()) : "—");
/** a gain as an instance value: four significant digits, never in exponent form */
const gainText = (v: number) => {
  const s = Number(v.toPrecision(4)).toString();
  return s.includes("e") ? v.toFixed(8).replace(/0+$/, "") : s;
};

/** Controllability of the architecture's system instances: the control
 *  simulation (a mixer and cascaded PID loops down to each ESC, flown in a 6-DOF
 *  model) verifies the control requirements; the tuning analysis proposes the
 *  flight controller's gains from open-loop tests. */
export function ControlPanel({ projectId, catalog, arch, selected, onSelect, ensureSaved, onChange, onOpen }: Props) {
  const sims = useMemo(() => new Map(catalog.simulations.map((s) => [s.qualifiedName, s])), [catalog]);
  const verifyDefs = arch.systemSimulations.filter((q) => sims.get(q)?.kind === "verification def");
  const tuneDefs = arch.systemSimulations.filter((q) => sims.get(q)?.kind === "analysis def");
  const configs = catalog.configurations.filter((c) => c.architectureId === arch.id);
  const [res, setRes] = useState<SystemSimulationResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tune, setTune] = useState<TuneResult | null>(null);
  const [tuning, setTuning] = useState(false);
  const [fcName, setFcName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);

  const sel = configs.find((c) => c.qualifiedName === selected) || null;
  const reqs = arch.systemRequirements;
  const docs = Object.fromEntries(catalog.requirements.map((r) => [r.qualifiedName, r.doc || ""]));

  const simulate = async () => {
    if (!(await ensureSaved())) return;
    setRunning(true);
    setError(null);
    try {
      setRes(await api.simulateSystems(projectId, { architecture: arch.qualifiedName }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  };
  // results follow the model: re-run after every save while some are on screen
  useEffect(() => {
    if (res) simulate();
    api.evidence(projectId).then(setEvidence).catch(() => setEvidence([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);
  useEffect(() => {
    setRes(null);
    setTune(null);
  }, [arch.id]);

  const runTune = async () => {
    if (!sel || !(await ensureSaved())) return;
    setTuning(true);
    setError(null);
    setNote(null);
    try {
      const t = await api.tuneSystem(projectId, sel.qualifiedName);
      setTune(t);
      const cur = t.current?.qualifiedName;
      setFcName(cur ? short(cur) : `fc${sel.name[0].toUpperCase()}${sel.name.slice(1)}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setTuning(false);
    }
  };

  /** the proposed gains as a FlightController instance, assigned to the system's slot */
  const saveGains = async () => {
    if (!tune || !sel || !tune.slot || !tune.component) return;
    setBusy(true);
    try {
      const existing = catalog.instances.find((i) => i.component === tune.component && i.name === fcName);
      const inst = await onChange(() =>
        api.saveInstance(projectId, {
          component: tune.component!,
          name: fcName,
          doc: `Tuned for ${sel.name} (result ${tune.result.resultId?.slice(0, 8)})`,
          values: Object.fromEntries(Object.entries(tune.proposals).map(([k, v]) => [k, gainText(v)])),
          replace: existing?.qualifiedName ?? null,
        })
      );
      if (!inst) return;
      if (sel.assignments[tune.slot] !== inst.qualifiedName) {
        await onChange(() =>
          api.saveConfiguration(projectId, {
            architecture: arch.qualifiedName,
            name: sel.name,
            doc: sel.doc || "",
            assignments: { ...sel.assignments, [tune.slot!]: inst.qualifiedName },
            behaviors: sel.behaviors,
            replace: sel.qualifiedName,
          })
        );
      }
      setNote(`${existing ? "Updated" : "Created"} ${inst.qualifiedName} and assigned it to ${sel.name}.${tune.slot}.`);
    } finally {
      setBusy(false);
    }
  };

  const record = async (resultId: string) => {
    setBusy(true);
    try {
      const r = await onChange(() => api.recordEvidence(resultId, false));
      if (r) setNote(`Recorded ${short(r.qualifiedName)} in ${r.modelName}.`);
    } finally {
      setBusy(false);
    }
  };

  if (!verifyDefs.length && !tuneDefs.length) return null;
  const runOf = (qn: string): SystemRun | undefined => (res?.systems[qn] ? res.systems[qn][verifyDefs[0]] : undefined);
  const selRun = sel ? runOf(sel.qualifiedName) : undefined;
  const controllerSlot = tune?.slot || arch.slots.find((s) => catalog.components.find((c) => c.qualifiedName === s.type)?.attributes.some((a) => a.name === "rateKp"))?.name;

  return (
    <div className="control">
      <div className="section-title row-between">
        <span>
          Controllability <span className="muted small">· mixer, cascaded PID loops to each ESC, 6-DOF with rotor dynamics</span>
        </span>
        <span className="row-end">
          {verifyDefs.length > 0 && (
            <button disabled={running || !configs.length} onClick={simulate} title="Fly each system's behaviors closed-loop with its flight controller and check the control requirements">
              {running ? "Simulating…" : "▶ Simulate controllability"}
            </button>
          )}
          {tuneDefs.length > 0 && (
            <button disabled={tuning || !sel} onClick={runTune} title={sel ? `Identify ${sel.name}'s dynamics from open-loop tests and propose gains` : "Select a system instance first"}>
              {tuning ? "Tuning…" : `Tune controller${sel ? ` for ${sel.name}` : ""}`}
            </button>
          )}
        </span>
      </div>
      {error && <div className="error-box">{error}</div>}
      {note && <div className="hint">{note}</div>}
      {!res && !tune && (
        <div className="hint">
          Simulate to check {reqs.map(short).join(", ") || "the control requirements"} for every system instance; each needs a {controllerSlot ? <code>{controllerSlot}</code> : "controller"} with gains. Select a system and tune its controller to propose them.
        </div>
      )}

      {res && (
        <div className="table-scroll">
          <table className="table compact">
            <thead>
              <tr>
                <th>System</th>
                <th>Controller</th>
                {reqs.map((q) => (
                  <th key={q} title={docs[q]}>
                    {short(q).replace(/Requirement$/, "")}
                    <div className="muted small">◆ simulated</div>
                  </th>
                ))}
                <th className="num" title="Smallest phase margin over the loops">Phase margin</th>
                <th className="num" title="Maximum thrust over hover thrust">Thrust margin</th>
                <th className="num" title="Speed step: settling time">Speed settles</th>
                <th className="num" title="90° turn: settling time after the turn">Turn settles</th>
                <th className="num" title="Largest position error in the gust">Gust</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => {
                const r = runOf(c.qualifiedName);
                const o = r?.outputs;
                const ev = evidence.find((e) => e.values.mission === "system" && e.system === c.qualifiedName && e.simulation === verifyDefs[0]);
                return (
                  <tr key={c.id} className={`clickable${selected === c.qualifiedName ? " row-on" : ""}`} onClick={() => onSelect(c.qualifiedName)}>
                    <td className="mono strong">{c.name}</td>
                    <td className="mono small">{controllerSlot && c.assignments[controllerSlot] ? short(c.assignments[controllerSlot]) : <span className="muted">— none —</span>}</td>
                    {r?.error ? (
                      <td colSpan={reqs.length + 5} className="muted small">
                        ⊘ {r.error}
                      </td>
                    ) : (
                      <>
                        {reqs.map((q) => {
                          const v = r?.verdicts[q];
                          const ver = r?.verification[q];
                          return (
                            <td key={q} title={ver?.note || Object.entries(ver?.values || {}).map(([k, x]) => `${k} ${fmt(x)}`).join("\n")}>
                              {v ? <span className={`pill ${v === "holds" ? "ok" : "bad"}`}>{v === "holds" ? "✓ holds" : v === "fails" ? "✗ fails" : "? error"}</span> : <span className="muted">—</span>}
                            </td>
                          );
                        })}
                        <td className="mono num">{o ? `${fmt(o.phaseMargin)}°` : "—"}</td>
                        <td className="mono num">{o ? fmt(o.thrustMargin) : "—"}</td>
                        <td className="mono num">{o?.speedSettlingTime != null ? `${fmt(o.speedSettlingTime)} s` : "—"}</td>
                        <td className="mono num">{o?.headingSettlingTime != null ? `${fmt(o.headingSettlingTime)} s` : "—"}</td>
                        <td className="mono num">{o?.gustDeviation != null ? `${fmt(o.gustDeviation)} m` : "—"}</td>
                      </>
                    )}
                    <td className="nowrap small" onClick={(e) => e.stopPropagation()}>
                      {ev && (
                        <button className="link" onClick={() => onOpen(ev)} title={`${ev.qualifiedName}: ${ev.status}`}>
                          <span className={`pill ${ev.status === "current" ? "ok" : "warn"}`}>◆ {ev.status === "current" ? "recorded" : "outdated"}</span>
                        </button>
                      )}
                      {r?.resultId && r.outputs && (!ev || ev.values.resultId !== r.resultId) && (
                        <button className="link" disabled={busy} onClick={() => record(r.resultId!)}>
                          {ev ? "Re-record" : "Record"}
                        </button>
                      )}
                      {r?.resultId && (
                        <a href={api.resultUrl(r.resultId)} target="_blank" rel="noreferrer">
                          data ↗
                        </a>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {res && res.errors.length > 0 && <div className="error-box">{res.errors.join("\n")}</div>}

      {sel && selRun?.outputs && (
        <>
          <div className="section-title">
            {sel.name}: manoeuvres with {controllerSlot && sel.assignments[controllerSlot] ? short(sel.assignments[controllerSlot]) : "its controller"}
          </div>
          <Scenarios o={selRun.outputs} />
          <LoopDetails o={selRun.outputs} />
        </>
      )}

      {tune && sel && tune.system === sel.qualifiedName && (
        <Tuning tune={tune} name={fcName} setName={setFcName} busy={busy} onSave={saveGains} />
      )}
    </div>
  );
}

/** Each manoeuvre: the response against the command, and its step metrics. */
function Scenarios({ o }: { o: ControlOutputs }) {
  const line = (s: ControlScenario, k: string) => s.series.map((p) => ({ x: p.t, y: p[k] }));
  const metric = (s: ControlScenario) =>
    s.parameter === "gustSpeed"
      ? `largest error ${fmt(s.maxDeviation)} m · back to ${fmt(s.finalError)} m`
      : [
          s.riseTime != null ? `rise ${fmt(s.riseTime)} s` : null,
          s.settlingTime != null ? `settles ${fmt(s.settlingTime)} s` : "does not settle",
          s.overshoot != null ? `overshoot ${fmt(s.overshoot)} %` : null,
          s.maxTrackingError != null ? `lags ${fmt(s.maxTrackingError)}° at most` : null,
        ]
          .filter(Boolean)
          .join(" · ");
  return (
    <>
      <div className="multiples">
        {o.scenarios.map((s) => (
          <div key={s.name}>
            <MiniChart
              title={s.name}
              unit={s.unit}
              xLabel="time (s)"
              curve={line(s, "actual")}
              reference={line(s, "reference")}
              names={["actual", "command"]}
              points={[]}
              fmtX={(v) => v.toFixed(0)}
              fmtY={(v) => fmt(v)}
            />
            <div className="small muted">
              {metric(s)} · tilt {fmt(s.maxTilt)}° · throttle {fmt(100 * s.maxThrottle)} %{s.saturation > 0 ? ` · at a limit ${fmt(s.saturation)} %` : ""}
            </div>
          </div>
        ))}
      </div>
      {o.issues.length > 0 && <div className="error-box">{o.issues.join("\n")}</div>}
    </>
  );
}

/** Loop margins, hover trim and the mixer. */
function LoopDetails({ o }: { o: ControlOutputs }) {
  return (
    <div className="lib-cols">
      <div>
        <div className="section-title">Loop margins</div>
        <table className="table compact">
          <thead>
            <tr>
              <th>Loop</th>
              <th className="num">Phase</th>
              <th className="num">Gain</th>
              <th className="num" title="Gain crossover frequency">Crossover</th>
            </tr>
          </thead>
          <tbody>
            {LOOPS.filter(([k]) => o.margins[k]).map(([k, label]) => {
              const m = o.margins[k];
              return (
                <tr key={k}>
                  <td>{label}</td>
                  <td className={`mono num${m.phaseMargin != null && m.phaseMargin < 45 ? " error-text" : ""}`}>{m.phaseMargin != null ? `${fmt(m.phaseMargin)}°` : "—"}</td>
                  <td className="mono num">{m.gainMargin != null ? `${fmt(m.gainMargin)} dB` : "∞"}</td>
                  <td className="mono num">{m.crossover != null ? `${fmt(m.crossover)} rad/s` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="small muted">
          Hover: {fmt(o.hover.rpm)} of {fmt(o.hover.maxRpm)} rpm, throttle {fmt(100 * o.hover.throttle)} %, thrust margin {fmt(o.thrustMargin)}, yaw authority {fmt(o.yawAuthority)}.
        </div>
      </div>
      <div>
        <div className="section-title">Mixer</div>
        <table className="table compact">
          <thead>
            <tr>
              <th>Rotor</th>
              <th className="num">Angle</th>
              <th>Spin</th>
              <th className="num" title="Thrust per unit of collective thrust, roll, pitch and yaw torque">Collective · roll · pitch · yaw</th>
            </tr>
          </thead>
          <tbody>
            {o.mixer.rotors.map((r, i) => (
              <tr key={r.index}>
                <td className="mono">{r.index}</td>
                <td className="mono num">{fmt(r.angle)}°</td>
                <td>{r.spin === "ccw" ? "↺ ccw" : "↻ cw"}</td>
                <td className="mono num small">{o.mixer.mixer[i].map((v) => fmt(v)).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Open-loop tests with the fitted models, the proposed gains, and saving them. */
function Tuning({ tune, name, setName, busy, onSave }: { tune: TuneResult; name: string; setName(v: string): void; busy: boolean; onSave(): void }) {
  const r = tune.result;
  const o = r.outputs;
  if (!o) return <div className="error-box">Tuning failed: {r.error}</div>;
  const id = o.identification;
  const test = (t: { t: number; measured: number; model: number }[], k: "measured" | "model") => t.map((p) => ({ x: p.t, y: p[k] }));
  const current = tune.current?.values || {};
  return (
    <div className="form-card">
      <div className="row-between">
        <span className="strong">Controller tuning for {short(tune.system)}</span>
        {r.resultId && (
          <a className="small" href={api.resultUrl(r.resultId)} target="_blank" rel="noreferrer">
            data ↗
          </a>
        )}
      </div>
      <div className="hint small">
        Open-loop tests, inside out: a {fmt(id.rotor.step)} V step on one motor with the body held (time constant {fmt(1000 * id.rotor.timeConstant)} ms), then torque steps on a gimbal with the speed loops closed. The fitted
        integrator-with-lag models give 1/inertia {fmt(id.roll.b)} roll (lag {fmt(1000 * id.roll.lag)} ms) and {fmt(id.yaw.b)} yaw (lag {fmt(1000 * id.yaw.lag)} ms). Each loop is tuned for at least 45° of phase margin, a quarter of
        the bandwidth of the loop inside it.
      </div>
      <div className="multiples">
        <MiniChart title="Rotor speed step" unit="rpm" xLabel="time (s)" curve={test(id.rotor.test, "measured")} reference={test(id.rotor.test, "model")} names={["measured", "model"]} points={[]} fmtX={(v) => v.toFixed(1)} fmtY={(v) => v.toFixed(0)} />
        <MiniChart title="Roll torque step" unit="rad/s" xLabel="time (s)" curve={test(id.roll.test, "measured")} reference={test(id.roll.test, "model")} names={["measured", "model"]} points={[]} fmtX={(v) => v.toFixed(2)} fmtY={(v) => fmt(v)} />
        <MiniChart title="Yaw torque step" unit="rad/s" xLabel="time (s)" curve={test(id.yaw.test, "measured")} reference={test(id.yaw.test, "model")} names={["measured", "model"]} points={[]} fmtX={(v) => v.toFixed(2)} fmtY={(v) => fmt(v)} />
      </div>
      <div className="lib-cols">
        <div>
          <div className="section-title">Proposed gains</div>
          <table className="table compact">
            <thead>
              <tr>
                <th>Gain</th>
                <th className="num">Proposed</th>
                <th className="num">Current</th>
                <th>Unit</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tune.proposals).map(([k, v]) => (
                <tr key={k}>
                  <td className="mono">{k}</td>
                  <td className="mono num strong">{gainText(v)}</td>
                  <td className="mono num muted">{current[k] ?? "—"}</td>
                  <td className="small muted">{GAIN_UNITS[k] || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="section-title">With these gains</div>
          <Scenarios o={o} />
        </div>
      </div>
      {tune.slot && tune.component ? (
        <div className="row-end">
          <label className="inline small">
            Save as {short(tune.component)}
            <input className={`mono${NAME_RE.test(name) ? "" : " invalid"}`} value={name} onChange={(e) => setName(e.target.value)} style={{ width: 180 }} />
          </label>
          <button className="primary" disabled={busy || !NAME_RE.test(name)} onClick={onSave} title={`Create (or update) the instance and assign it to ${short(tune.system)}.${tune.slot}`}>
            {busy ? "Saving…" : `Save & assign to ${tune.slot}`}
          </button>
        </div>
      ) : (
        <div className="hint">No slot of {short(tune.system)} takes these gains: add a part typed by a component with these attributes (e.g. FlightController).</div>
      )}
    </div>
  );
}
