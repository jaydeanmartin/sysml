import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { MissionRequirement, RequirementCheck, SpecRequirement, SpecVerdict } from "../types";
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

  // mission requirements (system view): each mission type's objective, then the missions that add their own;
  // they apply to the drones the missions are assigned to
  const missionRows = useMemo(() => {
    type Row = { key: string; depth: number; label?: string; kind?: string; doc?: string | null; req?: MissionRequirement; applies: Set<string>; reqs: string[] };
    const out: Row[] = [];
    if (view !== "system" || !catalog) return out;
    const reqBy = new Map(catalog.missionRequirements.map((r) => [r.qualifiedName, r]));
    for (const t of catalog.missionTypes) {
      const ms = catalog.missions.filter((m) => m.missionTypeId === t.id);
      const own = (t.requirements || []).map((q) => reqBy.get(q)).filter((r): r is MissionRequirement => !!r);
      if (!own.length && !ms.some((m) => m.requirements.length)) continue;
      const typeApplies = new Set(ms.flatMap((m) => m.assignedTo));
      out.push({ key: t.qualifiedName, depth: 0, label: t.name, kind: "mission type", doc: t.doc, applies: typeApplies, reqs: own.map((r) => r.qualifiedName) });
      if (collapsed.has(t.qualifiedName)) continue;
      for (const r of own) out.push({ key: r.qualifiedName, depth: 1, req: r, applies: typeApplies, reqs: [r.qualifiedName] });
      for (const m of ms.filter((x) => x.requirements.length)) {
        const applies = new Set(m.assignedTo);
        out.push({ key: m.qualifiedName, depth: 1, label: m.name, kind: "mission", doc: m.doc, applies, reqs: m.requirements });
        for (const q of m.requirements) {
          const r = reqBy.get(q);
          if (r) out.push({ key: q, depth: 2, req: r, applies, reqs: [q] });
        }
      }
    }
    return out;
  }, [catalog, view, collapsed]);
  const rank: Record<SpecVerdict, number> = { fails: 3, error: 2, unverified: 1, holds: 0 };
  const missionVerdict = (reqs: string[], subj: string): SpecVerdict | undefined => {
    let worst: SpecVerdict | undefined;
    for (const q of reqs) {
      const v = check?.missionVerdicts?.[q]?.[subj];
      if (v && (!worst || rank[v] > rank[worst])) worst = v;
    }
    return worst;
  };

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
  if (check && view === "system") for (const per of Object.values(check.missionVerdicts || {})) for (const v of Object.values(per)) tally[v]++;
  const selectedMission = sel ? catalog.missionRequirements.find((r) => r.qualifiedName === sel) : undefined;
  const selected = sel ? byQn.get(sel) || selectedMission : undefined;

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
            {tally.unverified > 0 && <span className="pill" title="Checked with measured values no simulation supplies yet">◇ {tally.unverified} unverified</span>}
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
                    const by = r.replacedFor?.[c.qn];
                    if (by)
                      return (
                        <td key={c.qn} className="spec-col muted" title={`replaced for ${c.name} by ${byQn.get(by)?.shortName || short(by)}, which it satisfies`}>
                          ↷
                        </td>
                      );
                    if (!here.has(c.qn)) return <td key={c.qn} className="spec-col" />;
                    const v = verdicts[c.qn];
                    if (!v) return <td key={c.qn} className="spec-col muted" title="applies">·</td>;
                    const [cls, sym] = PILL[v];
                    const sim = r.type ? check?.systemDetail?.[r.qualifiedName]?.[c.qn] : undefined;
                    const how = sim
                      ? `\nby ${short(sim.simulation)}${sim.note ? `: ${sim.note}` : ""}${Object.entries(sim.values || {}).map(([k, x]) => `\n${k} ${typeof x === "number" ? Number(x.toPrecision(3)) : x}`).join("")}`
                      : "";
                    return (
                      <td key={c.qn} className="spec-col" title={`${r.shortName || r.name} for ${c.name}: ${v}${how}`}>
                        <span className={`pill ${cls}`}>{sym}</span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {missionRows.length > 0 && (
              <tr className="spec-section">
                <td colSpan={2 + columns.length}>Missions · apply to the drones each mission is assigned to</td>
              </tr>
            )}
            {missionRows.map((row) => {
              const toggle = row.label && row.kind === "mission type";
              return (
                <tr key={`m:${row.key}`} className={`clickable${sel === row.key ? " row-on" : ""}${row.req ? "" : " spec-statement"}`} onClick={() => row.req && setSel(row.key)}>
                  <td className="mono small spec-id">{row.req?.shortName || ""}</td>
                  <td className="spec-name" style={{ paddingLeft: 6 + row.depth * 16 }}>
                    {toggle && (
                      <button
                        className="link spec-toggle"
                        onClick={(e) => {
                          e.stopPropagation();
                          const next = new Set(collapsed);
                          if (next.has(row.key)) next.delete(row.key);
                          else next.add(row.key);
                          setCollapsed(next);
                        }}
                      >
                        {collapsed.has(row.key) ? "▸" : "▾"}
                      </button>
                    )}
                    {row.req ? (
                      <>
                        <span className="mono">{row.req.name}</span>
                        <span className="muted small">
                          {" "}
                          : {short(row.req.type)}
                          {row.req.parameters.filter((p) => p.bound).map((p) => ` · ${p.name} ${p.value}`)}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="mono strong">{row.label}</span> <span className="muted small">{row.kind}</span>
                        {row.doc && <div className="muted small spec-doc">{row.doc}</div>}
                      </>
                    )}
                  </td>
                  {columns.map((c) => {
                    if (!row.applies.has(c.qn)) return <td key={c.qn} className="spec-col" />;
                    const v = missionVerdict(row.reqs, c.qn);
                    if (!v) return <td key={c.qn} className="spec-col muted" title="applies: an assigned mission">·</td>;
                    const [cls, sym] = PILL[v];
                    const detail = row.req ? Object.entries(check?.missionDetail?.[row.req.qualifiedName]?.[c.qn] || {}).map(([m, x]) => `${short(m)}: ${x}`).join("\n") : "";
                    return (
                      <td key={c.qn} className="spec-col" title={`${row.req?.shortName || row.label} for ${c.name}: ${v}${detail ? `\n${detail}` : ""}`}>
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

      {selected && <Detail
          r={selected}
          kids={children.get(selected.qualifiedName) || []}
          check={check}
          byQn={byQn}
          onOpen={onOpen}
          typeOf={(q) => catalog.requirements.find((x) => x.qualifiedName === q)}
          mission={
            selectedMission
              ? {
                  owner: selectedMission.owner,
                  drones: [...new Set(catalog.missions.filter((m) => m.qualifiedName === selectedMission.owner || m.missionType === selectedMission.owner).flatMap((m) => m.assignedTo))],
                }
              : undefined
          }
        />}
    </div>
  );
}

/** One requirement: its statement, type, parameters, constraint and applicability. */
function Detail({
  r, kids, check, onOpen, typeOf, mission, byQn,
}: {
  r: SpecRequirement; kids: SpecRequirement[]; check: RequirementCheck | null; onOpen: CatalogPanelProps["onOpen"];
  byQn: Map<string, SpecRequirement>;
  typeOf(q: string): import("../types").CatalogRequirement | undefined;
  /** for a mission requirement: whose objective holds it, and the drones its missions are assigned to */
  mission?: { owner: string; drones: string[] };
}) {
  const type = r.type ? typeOf(r.type) : undefined;
  const v = check ? (mission ? check.missionVerdicts?.[r.qualifiedName] : r.type ? check.verdicts[r.qualifiedName] : check.rollup[r.qualifiedName]) || {} : null;
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
            {r.measured.length > 0 && (
              <div className="hint small">
                Checked with measured values ({r.measured.join(", ")}) that a simulation supplies
                {type?.verifiedBy?.length ? <>: <span className="mono">{type.verifiedBy.map(short).join(", ")}</span></> : ""}.
                {r.isTest && " A control test: the simulation flies it for each system it applies to."}
              </div>
            )}
          </div>
          <div>
            <div className="section-title">Parameters</div>
            <table className="table compact">
              <tbody>
                {r.parameters.map((p) => (
                  <tr key={p.name}>
                    <td className="mono">{p.name}</td>
                    <td className="mono">{p.value ?? <span className="error-text">unset</span>}</td>
                    <td className="muted small">
                      {p.bound ? "set here" : p.value ? "the type's" : ""}
                      {r.isTest && p.name in r.flies ? " · flown" : r.isTest ? " · limit" : ""}
                    </td>
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
              {mission ? (
                <>
                  the drones {short(mission.owner)}'s missions are assigned to: <span className="mono">{mission.drones.map(short).join(", ") || "none yet"}</span>
                  <div className="muted">In the objective of {short(mission.owner)}; checked with measured values when those missions are flown.</div>
                </>
              ) : r.explicit ? (
                <>
                  <span className="mono">{r.satisfiedBy.map(short).join(", ")}</span> <span className="muted">(satisfy)</span>
                </>
              ) : (
                <>
                  every <span className="mono">{short(r.subjectType)}</span>: {r.appliesTo.map(short).join(", ") || "none yet"}
                </>
              )}
              {Object.entries(r.replacedFor || {}).map(([subj, by]) => (
                <div key={subj} className="muted">
                  ↷ not for <span className="mono">{short(subj)}</span>: it satisfies <span className="mono">{byQn.get(by)?.shortName || ""} {short(by)}</span> instead
                </div>
              ))}
            </div>
            {check?.systemDetail?.[r.qualifiedName] && (
              <>
                <div className="section-title">Simulated</div>
                <table className="table compact">
                  <tbody>
                    {Object.entries(check.systemDetail[r.qualifiedName]).map(([subj, d]) => {
                      const x = check.verdicts[r.qualifiedName]?.[subj];
                      return (
                        <tr key={subj}>
                          <td className="mono">{short(subj)}</td>
                          <td>{x && <span className={`pill ${PILL[x][0]}`}>{PILL[x][1]}</span>}</td>
                          <td className="mono small">
                            {d.note || Object.entries(d.values || {}).map(([k, val]) => `${k} ${typeof val === "number" ? Number(val.toPrecision(3)) : val}`).join(" · ")}
                          </td>
                          <td>
                            {d.resultId && (
                              <a href={api.resultUrl(d.resultId)} target="_blank" rel="noreferrer" className="small">
                                data ↗
                              </a>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
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
