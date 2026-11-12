import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Catalog, Component, DynamicsOutputs } from "../types";
import { errorText, short } from "./catalogUtil";
import { MiniChart } from "./DynamicsResults";

/** Components whose attributes are the parameters of a performance model of the
 *  dynamic simulation: their curves come from the componentCurves analysis. */
const KINDS = {
  propeller: ["ct0", "ct1", "ct2", "cp0", "cp1", "cp2"],
  motor: ["kv", "resistance", "noLoadCurrent", "maxCurrent"],
} as const;
type Kind = keyof typeof KINDS;

export function curveKind(c: Component): Kind | null {
  const names = new Set(c.attributes.map((a) => a.name));
  return (Object.keys(KINDS) as Kind[]).find((k) => KINDS[k].every((p) => names.has(p))) || null;
}

/** The first number of a SysML value ('0.11 [SI::kg]', '[12]', '900'). */
function num(v: string | undefined): number | null {
  const m = /-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?/.exec(v || "");
  return m ? Number(m[0]) : null;
}

type PropCurve = DynamicsOutputs["propellerCurves"][number];
type MotorCurve = DynamicsOutputs["motorCurves"][number];
interface Result {
  curves: (PropCurve | MotorCurve)[];
  summary: Record<string, number | null>;
}

interface Props {
  comp: Component;
  catalog: Catalog;
  /** instance -> its values; the first is shown unless one is picked */
  instances: { qualifiedName: string; name: string; values: Record<string, string> }[];
  /** values of the instance being edited (component defaults + form), shown live */
  editing: { label: string; values: Record<string, string> } | null;
}

/** Performance curves of a propeller or motor instance, computed by the same
 *  component models the dynamic mission simulation flies. */
export function ComponentPerformance({ comp, catalog, instances, editing }: Props) {
  const kind = curveKind(comp)!;
  const [pick, setPick] = useState<string | null>(null);
  const [battery, setBattery] = useState<string | null>(null);
  const [res, setRes] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shown = editing ? null : instances.find((i) => i.qualifiedName === pick) || instances[0];
  const values = editing?.values || shown?.values || {};
  const params = Object.fromEntries(KINDS[kind].map((p) => [p, num(values[p])]));
  const missing = KINDS[kind].filter((p) => params[p] === null);

  // a motor's full-throttle curves need a supply: batteries of the catalog, by default one a system pairs with this motor
  const batteries = useMemo(
    () => catalog.instances.filter((i) => i.kind === "part" && num(i.values.voltage) !== null && num(i.values.energy) !== null),
    [catalog]
  );
  const paired = useMemo(() => {
    if (kind !== "motor" || !shown) return null;
    for (const cfg of catalog.configurations) {
      const vals = Object.values(cfg.assignments);
      if (vals.includes(shown.qualifiedName)) {
        const b = batteries.find((x) => vals.includes(x.qualifiedName));
        if (b) return b.qualifiedName;
      }
    }
    return null;
  }, [kind, shown, catalog, batteries]);
  const bat = batteries.find((b) => b.qualifiedName === battery) || batteries.find((b) => b.qualifiedName === paired) || batteries[0];
  const voltage = kind === "motor" ? num(bat?.values.voltage) : null;

  const key = JSON.stringify({ kind, params, voltage });
  useEffect(() => {
    if (missing.length || (kind === "motor" && voltage === null)) {
      setRes(null);
      return;
    }
    let stale = false;
    // debounced: the form re-renders on every keystroke
    const t = setTimeout(() => {
      api
        .invoke("componentCurves", { component: kind, ...params, ...(voltage !== null ? { voltage } : {}) })
        .then((r) => {
          if (stale) return;
          setRes({ curves: r.outputs.curves.value as Result["curves"], summary: r.outputs.summary.value as Result["summary"] });
          setError(null);
        })
        .catch((e) => !stale && setError(errorText(e)));
    }, 300);
    return () => {
      stale = true;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const s = res?.summary || {};
  const pct = (v: number | null | undefined) => (typeof v === "number" ? `${(100 * v).toFixed(0)} %` : "—");

  return (
    <div className="perf">
      <div className="section-title row-between">
        <span>
          Performance curves <span className="muted small">· as the dynamic mission simulation models it</span>
        </span>
        <span className="row-end">
          {editing ? (
            <span className="muted small">editing {editing.label}</span>
          ) : (
            instances.length > 1 && (
              <label className="inline-label">
                <span>instance</span>
                <select value={shown?.qualifiedName} onChange={(e) => setPick(e.target.value)}>
                  {instances.map((i) => (
                    <option key={i.qualifiedName} value={i.qualifiedName}>
                      {i.name}
                    </option>
                  ))}
                </select>
              </label>
            )
          )}
          {kind === "motor" && batteries.length > 0 && (
            <label className="inline-label">
              <span>on</span>
              <select value={bat?.qualifiedName} onChange={(e) => setBattery(e.target.value)} title="The battery whose nominal voltage drives the motor at full throttle">
                {batteries.map((b) => (
                  <option key={b.qualifiedName} value={b.qualifiedName}>
                    {b.name} · {num(b.values.voltage)} V{b.qualifiedName === paired ? " (paired)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </span>
      </div>
      {!shown && !editing ? (
        <div className="muted small">No instances yet: add one to see its curves.</div>
      ) : missing.length ? (
        <div className="muted small">
          {editing ? "Set" : `${shown?.name} does not set`} {missing.join(", ")} to see its curves.
        </div>
      ) : kind === "motor" && voltage === null ? (
        <div className="muted small">No battery in the catalog to supply the motor.</div>
      ) : error ? (
        <div className="error-box">{error}</div>
      ) : !res ? (
        <div className="muted small">Computing…</div>
      ) : kind === "propeller" ? (
        <>
          <div className="hint small">
            Against the advance ratio J = V / (n D), up to zero thrust at J {s.zeroThrustJ?.toFixed(2)}. Static (J = 0): CT {s.staticCT?.toFixed(3)}, CP {s.staticCP?.toFixed(3)}, thrust/power {s.staticCTCP?.toFixed(2)}. Best efficiency {pct(s.maxEfficiency)} at J {s.maxEfficiencyJ?.toFixed(2)}.
          </div>
          <div className="multiples">
            <MiniChart title="Torque coefficient" unit="CQ = CP / 2π" xLabel="advance ratio J" curve={(res.curves as PropCurve[]).map((p) => ({ x: p.J, y: p.CQ }))} points={[]} fmtY={(v) => v.toFixed(4)} fmtX={(v) => v.toFixed(2)} />
            <MiniChart title="Thrust / power" unit="CT / CP" xLabel="advance ratio J" curve={(res.curves as PropCurve[]).map((p) => ({ x: p.J, y: p.CTCP }))} points={[]} fmtY={(v) => v.toFixed(2)} fmtX={(v) => v.toFixed(2)} />
            <MiniChart title="Propeller efficiency" unit="η = J CT / CP" xLabel="advance ratio J" curve={(res.curves as PropCurve[]).map((p) => ({ x: p.J, y: 100 * p.efficiency }))} points={[]} fmtY={(v) => `${v.toFixed(0)} %`} fmtX={(v) => v.toFixed(2)} />
          </div>
        </>
      ) : (
        <>
          <div className="hint small">
            Full throttle on {short(bat?.qualifiedName)}'s nominal {s.voltage?.toFixed(1)} V, against the current, up to {(res.curves as MotorCurve[]).at(-1)?.current.toFixed(0)} A. No-load speed {s.noLoadSpeed?.toFixed(0)} rpm
            {typeof s.stallCurrent === "number" ? `, stall current ${s.stallCurrent.toFixed(0)} A` : ""}. Best efficiency {pct(s.maxEfficiency)} at {s.maxEfficiencyCurrent?.toFixed(1)} A; up to {s.maxShaftPower?.toFixed(0)} W of shaft power.
          </div>
          <div className="multiples">
            <MiniChart title="Torque" unit="N·m" xLabel="current (A)" curve={(res.curves as MotorCurve[]).map((m) => ({ x: m.current, y: m.torque }))} points={[]} fmtY={(v) => v.toFixed(2)} fmtX={(v) => v.toFixed(0)} />
            <MiniChart title="Speed" unit="rpm" xLabel="current (A)" curve={(res.curves as MotorCurve[]).map((m) => ({ x: m.current, y: m.speed }))} points={[]} fmtY={(v) => v.toFixed(0)} fmtX={(v) => v.toFixed(0)} />
            <MiniChart title="Shaft power" unit="W" xLabel="current (A)" curve={(res.curves as MotorCurve[]).map((m) => ({ x: m.current, y: m.power }))} points={[]} fmtY={(v) => v.toFixed(0)} fmtX={(v) => v.toFixed(0)} />
            <MiniChart title="Motor efficiency" unit="%" xLabel="current (A)" curve={(res.curves as MotorCurve[]).map((m) => ({ x: m.current, y: 100 * m.efficiency }))} points={[]} fmtY={(v) => `${v.toFixed(0)} %`} fmtX={(v) => v.toFixed(0)} />
          </div>
        </>
      )}
    </div>
  );
}
