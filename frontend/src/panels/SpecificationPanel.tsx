import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { RequirementCheck, SpecRequirement, SpecVerdict } from "../types";
import { errorText, short, type CatalogPanelProps } from "./catalogUtil";

type View = "system" | "component";

const PILL: Record<SpecVerdict, [string, string]> = {
  holds: ["ok", "✓"],
  fails: ["bad", "✗"],
  error: ["bad", "?"],
  unverified: ["", "◇"],
};

/** The requirement hierarchy (statements and instances of requirement types)
 *  against the system or component instances it applies to, with verdicts. */
export function SpecificationPanel({ projectId, catalog, loading, error, ensureSaved, onOpen }: CatalogPanelProps) {
  const [check, setCheck] = useState<RequirementCheck | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [view, setView] = useState<View>("system");
  const [sel, setSel] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const spec = catalog?.specification || [];
  const byQn = useMemo(() => new Map(spec.map((r) => [r.qualifiedName, r])), [spec]);
  const children = useMemo(() => {
    const m = new Map<string, SpecRequirement[]>();
    for (const r of spec) if (r.parent) m.set(r.parent, [...(m.get(r.parent) || []), r]);
    return m;
  }, [spec]);
  // the levels under each requirement (itself included), to show only what the view concerns
  const levels = useMemo(() => {
    const m = new Map<string, Set<string>>();
    const walk = (r: SpecRequirement): Set<string> => {
      if (m.has(r.qualifiedName)) return m.get(r.qualifiedName)!;
      const out = new Set<string>(r.type ? [r.level] : []);
      for (const c of children.get(r.qualifiedName) || []) walk(c).forEach((l) => out.add(l));
      m.set(r.qualifiedName, out);
      return out;
    };
    spec.forEach(walk);
    return m;
  }, [spec, children]);

  // the columns: system instances, or the component instances some requirement applies to
  const columns = useMemo(() => {
    if (!catalog) return [];
    if (view === "system") return catalog.configurations.map((c) => ({ qn: c.qualifiedName, name: c.name, group: short(c.architecture) }));
    const used = new Set(spec.filter((r) => r.level === "component").flatMap((r) => r.appliesTo));
    return catalog.instances
      .filter((i) => used.has(i.qualifiedName))
      .sort((a, b) => a.component.localeCompare(b.component) || a.name.localeCompare(b.name))
      .map((i) => ({ qn: i.qualifiedName, name: i.name, group: short(i.component) }));
  }, [catalog, spec, view]);

  // tree rows in document order, with their depth; collapsed statements hide what is under them
  const rows = useMemo(() => {
    const out: { r: SpecRequirement; depth: number }[] = [];
    const walk = (r: SpecRequirement, depth: number) => {
      if (!levels.get(r.qualifiedName)?.has(view)) return;
      out.push({ r, depth });
      if (collapsed.has(r.qualifiedName)) return;
      for (const c of children.get(r.qualifiedName) || []) walk(c, depth + 1);
    };
    spec.filter((r) => !r.parent).forEach((r) => walk(r, 0));
    return out;
  }, [spec, children, levels, view, collapsed]);

  const run = async () => {
    if (!(await ensureSaved())) return;
    setRunning(true);
    setRunError(null);
    try {
      setCheck(await api.checkRequirements(projectId));
    } catch (e) {
      setRunError(errorText(e));
    } finally {
      setRunning(false);
    }
  };
  // a check on screen follows the model
  useEffect(() => {
    if (check) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);

  /** where a requirement applies: its own subjects, or those of the instances under it */
  const applies = (r: SpecRequirement): Set<string> => {
    if (r.type) return new Set(r.appliesTo);
    const out = new Set<string>();
    for (const c of children.get(r.qualifiedName) || []) applies(c).forEach((q) => out.add(q));
    return out;
  };

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;
  if (!spec.length)
    return (
      <div className="panel">
        <div className="empty-card">
          <div className="strong">No specification in this project yet</div>
          <p>
            Write requirement types as abstract <code>requirement def</code>s with a subject and parameters (attributes), then the specification as requirement usages in a package:
            statements that hold instances of the types with their parameters set.
          </p>
          <code className="block">{`abstract requirement def ThrustToWeight {\n    subject drone : DeliveryDrone;\n    attribute minRatio : Real;\n    require constraint { drone.thrustToWeight >= minRatio }\n}\npackage Specification {\n    requirement <'SYS-1'> flightPerformance {\n        doc /* The drone shall have the lift a delivery needs. */\n        requirement <'SYS-1.1'> liftMargin : ThrustToWeight {\n            attribute :>> minRatio = 2.0;\n        }\n    }\n}`}</code>
        </div>
      </div>
    );

  const leaves = spec.filter((r) => r.type && r.level === view);
  const tally = { holds: 0, fails: 0, error: 0, unverified: 0 } as Record<SpecVerdict, number>;
  if (check) for (const r of leaves) for (const v of Object.values(check.verdicts[r.qualifiedName] || {})) tally[v]++;
  const selected = sel ? byQn.get(sel) : undefined;

  return (
    <div className="panel lib">
      <div className="panel-bar">
        <button className="primary" disabled={running} onClick={run} title="Evaluate every requirement instance for each system or component instance it applies to">
          {running ? "Checking…" : "▶ Check requirements"}
        </button>
        <div className="seg small-seg" role="radiogroup" aria-label="Columns">
          {(["system", "component"] as View[]).map((v) => (
            <button key={v} role="radio" aria-checked={view === v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
              {v === "system" ? "Systems" : "Components"}
            </button>
          ))}
        </div>
        {check && (
          <span className="chips">
            <span className="pill ok">✓ {tally.holds}</span>
            <span className="pill bad">✗ {tally.fails}</span>
            {tally.error > 0 && <span className="pill bad">? {tally.error}</span>}
            {tally.unverified > 0 && <span className="pill">◇ {tally.unverified} by simulation</span>}
          </span>
        )}
        {(loading || running) && <span className="hint">{running ? "Evaluating…" : "Refreshing…"}</span>}
      </div>
      {(error || runError) && <div className="error-box">{error || runError}</div>}
      {check && check.errors.length > 0 && <div className="error-box">{check.errors.join("\n")}</div>}
      {!check && <div className="hint">Dots mark where each requirement applies: what its satisfy relations name, else every {view} instance its subject type fits. Check to evaluate them; statements roll up what is under them.</div>}

      <div className="table-scroll spec-scroll">
        <table className="table compact spec-table">
          <thead>
            <tr>
              <th className="spec-id">ID</th>
              <th className="spec-name">Requirement</th>
              {columns.map((c) => (
                <th key={c.qn} className="spec-col" title={`${c.qn} (${c.group})`}>
                  <div className="mono">{c.name}</div>
                  <div className="muted small">{c.group}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ r, depth }) => {
              const here = applies(r);
              const kids = (children.get(r.qualifiedName) || []).length;
              const verdicts = check ? (r.type ? check.verdicts[r.qualifiedName] : check.rollup[r.qualifiedName]) || {} : {};
              return (
                <tr key={r.id} className={`clickable${sel === r.qualifiedName ? " row-on" : ""}${r.type ? "" : " spec-statement"}`} onClick={() => setSel(r.qualifiedName)}>
                  <td className="mono small spec-id">{r.shortName || ""}</td>
                  <td className="spec-name" style={{ paddingLeft: 6 + depth * 16 }}>
                    {kids > 0 && (
                      <button
                        className="link spec-toggle"
                        aria-label={collapsed.has(r.qualifiedName) ? "Expand" : "Collapse"}
                        onClick={(e) => {
                          e.stopPropagation();
                          const next = new Set(collapsed);
                          if (next.has(r.qualifiedName)) next.delete(r.qualifiedName);
                          else next.add(r.qualifiedName);
                          setCollapsed(next);
                        }}
                      >
                        {collapsed.has(r.qualifiedName) ? "▸" : "▾"}
                      </button>
                    )}
                    <span className={r.type ? "mono" : "mono strong"}>{r.name}</span>
                    {r.type && (
                      <span className="muted small">
                        {" "}
                        : {short(r.type)}
                        {r.parameters.filter((p) => p.bound).map((p) => ` · ${p.name} ${p.value}`)}
                      </span>
                    )}
                    {r.explicit && <span className="pill spec-explicit" title={`satisfy: ${r.satisfiedBy.map(short).join(", ")}`}>satisfy</span>}
                    {!r.type && r.doc && <div className="muted small spec-doc">{r.doc}</div>}
                  </td>
                  {columns.map((c) => {
                    if (!here.has(c.qn)) return <td key={c.qn} className="spec-col" />;
                    const v = verdicts[c.qn];
                    if (!v) return <td key={c.qn} className="spec-col muted" title="applies">·</td>;
                    const [cls, sym] = PILL[v];
                    return (
                      <td key={c.qn} className="spec-col" title={`${r.shortName || r.name} for ${c.name}: ${v}`}>
                        <span className={`pill ${cls}`}>{sym}</span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && <Detail r={selected} kids={children.get(selected.qualifiedName) || []} check={check} onOpen={onOpen} typeOf={(q) => catalog.requirements.find((x) => x.qualifiedName === q)} />}
    </div>
  );
}

/** One requirement: its statement, type, parameters, constraint and applicability. */
function Detail({ r, kids, check, onOpen, typeOf }: { r: SpecRequirement; kids: SpecRequirement[]; check: RequirementCheck | null; onOpen: CatalogPanelProps["onOpen"]; typeOf(q: string): import("../types").CatalogRequirement | undefined }) {
  const type = r.type ? typeOf(r.type) : undefined;
  const v = check ? (r.type ? check.verdicts[r.qualifiedName] : check.rollup[r.qualifiedName]) || {} : null;
  const failing = v ? Object.entries(v).filter(([, x]) => x === "fails" || x === "error") : [];
  return (
    <div className="form-card">
      <div className="row-between">
        <span>
          <span className="mono strong">{r.shortName ? `${r.shortName} ` : ""}</span>
          <span className="strong">{r.name}</span>
          <span className="muted small"> · {r.type ? `${r.level} requirement` : "statement"}</span>
        </span>
        <span>
          <button className="link" onClick={() => onOpen(r)}>
            Show in editor
          </button>
          {type && (
            <button className="link" onClick={() => onOpen(type)}>
              Show type
            </button>
          )}
        </span>
      </div>
      {r.doc && <div>{r.doc}</div>}
      {r.type ? (
        <div className="lib-cols">
          <div>
            <div className="section-title">Type</div>
            <div className="small">
              <span className="mono">{short(r.type)}</span>
              {r.isAbstractType ? " (abstract)" : ""} on <span className="mono">{short(r.subjectType)}</span>
              {type?.doc && <div className="muted">{type.doc}</div>}
            </div>
            {r.constraints.map((c, i) => (
              <code key={i} className="block">
                {c.kind} {c.text}
              </code>
            ))}
            {r.measured.length > 0 && <div className="hint small">Checked with measured values ({r.measured.join(", ")}) that a simulation supplies.</div>}
          </div>
          <div>
            <div className="section-title">Parameters</div>
            <table className="table compact">
              <tbody>
                {r.parameters.map((p) => (
                  <tr key={p.name}>
                    <td className="mono">{p.name}</td>
                    <td className="mono">{p.value ?? <span className="error-text">unset</span>}</td>
                    <td className="muted small">{p.bound ? "set here" : p.value ? "the type's" : ""}</td>
                  </tr>
                ))}
                {!r.parameters.length && (
                  <tr>
                    <td className="muted">none</td>
                  </tr>
                )}
              </tbody>
            </table>
            <div className="section-title">Applies to</div>
            <div className="small">
              {r.explicit ? (
                <>
                  <span className="mono">{r.satisfiedBy.map(short).join(", ")}</span> <span className="muted">(satisfy)</span>
                </>
              ) : (
                <>
                  every <span className="mono">{short(r.subjectType)}</span>: {r.appliesTo.map(short).join(", ") || "none yet"}
                </>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="small muted">
          A statement: it holds where everything under it that applies holds. {kids.length} requirement{kids.length === 1 ? "" : "s"} directly under it.
        </div>
      )}
      {v && (
        <div className="small">
          {failing.length ? (
            <>
              Not holding for <span className="mono">{failing.map(([q]) => short(q)).join(", ")}</span>.
            </>
          ) : Object.keys(v).length ? (
            <span className="pill ok">✓ holds for all {Object.keys(v).length}</span>
          ) : (
            <span className="muted">Applies to nothing yet.</span>
          )}
        </div>
      )}
    </div>
  );
}
