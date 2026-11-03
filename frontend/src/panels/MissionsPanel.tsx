import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { CatalogChange, Component, FlownMission, Mission, MissionEvaluation, MissionType, StepRequest, Verification } from "../types";
import { NAME_RE, errorText, partText, placeholder, ref, short, type CatalogPanelProps } from "./catalogUtil";
import { MissionSimulation } from "./MissionSimulation";

interface Draft {
  name: string;
  doc: string;
  package: string;
  /** in order: a behavior (abstract #Behavior action def) and its quantities */
  steps: StepRequest[];
  /** qualified name of the mission being edited; null for a new one */
  replace: string | null;
}

interface Evaluation extends MissionEvaluation {
  at: string;
  /** stepsKey of the draft sent along (null: none was) */
  draftKey: string | null;
}

const DRAFT = "draft";
const RESERVED = new Set(["in", "out", "inout", "start", "done", "then", "first", "action", "state", "part", "attribute", "end", "if", "else"]);
const lowerFirst = (s: string) => s[0].toLowerCase() + s.slice(1);

/** Step names as the server writes them: the behavior's name, numbered from its second use. */
function stepNames(steps: StepRequest[]): string[] {
  const seen = new Map<string, number>();
  return steps.map((s) => {
    let base = lowerFirst(short(s.behavior) || "step");
    if (RESERVED.has(base)) base += "Step";
    const n = (seen.get(base) || 0) + 1;
    seen.set(base, n);
    return n === 1 ? base : `${base}_${n}`;
  });
}

/** Steps compared by behavior and set quantities only. */
const stepsKey = (steps: StepRequest[]) =>
  steps
    .map((s) => `${s.behavior}{${Object.keys(s.values).filter((k) => s.values[k]?.trim()).sort().map((k) => `${k}=${s.values[k].trim()}`).join(",")}}`)
    .join("|");

/** Mission planner. A mission is an ordered list of abstract behaviors, each
 *  with its quantities (distance, altitude...) set. A system instance flies each
 *  step with the behavior instance it performs for that behavior (its speed,
 *  climb rate, power factor...), so a mission's totals, and whether its mission
 *  requirements hold, depend on the system instance. */
export function MissionsPanel({ projectId, catalog, loading, error, dirty, ensureSaved, onChange, onOpen }: CatalogPanelProps) {
  const [typeQn, setTypeQn] = useState<string | null>(null);
  const [selQn, setSelQn] = useState<string | null>(null); // mission shown in the form
  const [draft, setDraft] = useState<Draft | null>(null);
  const [flyer, setFlyer] = useState<string | null>(null); // system instance whose flight the form shows
  const [busy, setBusy] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evals, setEvals] = useState<Record<string, Evaluation>>({});

  const types = catalog?.missionTypes || [];
  const mtype = types.find((t) => t.qualifiedName === typeQn) || types[0];
  const missions = useMemo(() => (catalog?.missions || []).filter((m) => mtype && m.missionTypeId === mtype.id), [catalog, mtype]);
  const comps = useMemo(() => new Map((catalog?.components || []).map((c) => [c.qualifiedName, c])), [catalog]);
  const archById = useMemo(() => new Map((catalog?.architectures || []).map((a) => [a.id, a])), [catalog]);

  /** the behavior and every behavior it specializes */
  const lineage = (qn: string): Set<string> => {
    const out = new Set<string>();
    const walk = (q: string) => {
      if (out.has(q) || !comps.has(q)) return;
      out.add(q);
      comps.get(q)!.specializes.forEach(walk);
    };
    walk(qn);
    return out;
  };
  // behaviors a step can use: those some architecture performs (by type or a specialization of it)
  const stepTypes = useMemo(() => {
    const behaviors = (catalog?.components || []).filter((c) => c.kind === "action");
    const performedBy = (c: Component) => (catalog?.architectures || []).filter((a) => a.behaviors.some((b) => lineage(c.qualifiedName).has(b.type))).map((a) => a.name);
    const out = behaviors.map((c) => ({ c, by: performedBy(c) })).filter((x) => x.by.length);
    return out.length ? out : behaviors.map((c) => ({ c, by: [] as string[] }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, comps]);
  const color = (qn: string) => Math.max(0, stepTypes.findIndex((t) => t.c.qualifiedName === qn)) % 6;
  const quantities = (qn: string) => (comps.get(qn)?.attributes || []).filter((a) => !a.value);
  const fliers = useMemo(() => (catalog?.configurations || []).filter((c) => (archById.get(c.architectureId)?.behaviors.length || 0) > 0), [catalog, archById]);
  const evaluation = mtype ? evals[mtype.qualifiedName] : undefined;
  const totals = evaluation?.totals || mtype?.totals.map((t) => t.name) || [];

  const blank = (t: MissionType): Draft => {
    const base = lowerFirst(t.name);
    let n = 1;
    while (catalog?.missions.some((m) => m.name === `${base}${n}`)) n++;
    return { name: `${base}${n}`, doc: "", package: "", steps: [], replace: null };
  };
  const fromMission = (m: Mission): Draft => ({
    name: m.name,
    doc: m.doc || "",
    package: m.package || "",
    steps: m.steps.filter((s) => s.behavior).map((s) => ({ behavior: s.behavior!, values: { ...s.values } })),
    replace: m.qualifiedName,
  });

  // keep the form in step with the catalog (it is re-read after every save)
  useEffect(() => {
    if (!mtype) return setDraft(null);
    const m = selQn ? missions.find((x) => x.qualifiedName === selQn) : undefined;
    if (m) setDraft(fromMission(m));
    // a just-saved mission shows up once the catalog has been re-read: keep the draft until then
    else setDraft((d) => (d && (selQn || !d.replace) ? d : blank(mtype)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mtype, missions, selQn]);

  const pickType = (qn: string, mission: string | null = null) => {
    setTypeQn(qn);
    setSelQn(mission);
    const t = types.find((x) => x.qualifiedName === qn);
    if (!mission && t) setDraft(blank(t));
  };

  const defaultPackage = (t: MissionType) => {
    const root = t.qualifiedName.split("::")[0];
    const cands = (catalog?.packages || []).filter((p) => p.modelId === t.modelId && short(p.qualifiedName) === "Missions").map((p) => p.qualifiedName);
    cands.sort((x, y) => Number(!x.startsWith(root + "::")) - Number(!y.startsWith(root + "::")) || x.split("::").length - y.split("::").length);
    return cands[0] || t.package || "";
  };

  const run = async (fn: () => Promise<CatalogChange>) => {
    setBusy(true);
    try {
      return await onChange(fn);
    } finally {
      setBusy(false);
    }
  };

  const clean = (steps: StepRequest[]) => steps.map((s) => ({ behavior: s.behavior, values: Object.fromEntries(Object.entries(s.values).filter(([, v]) => v.trim())) }));

  const save = async () => {
    if (!mtype || !draft) return;
    const r = await run(() =>
      api.saveMission(projectId, {
        missionType: mtype.qualifiedName,
        name: draft.name,
        doc: draft.doc,
        package: draft.replace ? null : draft.package || null,
        steps: clean(draft.steps),
        replace: draft.replace,
      })
    );
    if (r) setSelQn(r.qualifiedName);
  };

  const remove = async (m: Mission) => {
    if (!confirm(`Remove mission ${m.name} from ${m.modelName}?`)) return;
    const r = await run(() => api.deleteCatalogElement(projectId, m.qualifiedName));
    if (r) setSelQn(null);
  };

  const setSteps = (steps: StepRequest[]) => draft && setDraft({ ...draft, steps });
  const setStep = (i: number, s: StepRequest) => draft && setSteps(draft.steps.map((x, j) => (j === i ? s : x)));
  const move = (i: number, d: number) => {
    if (!draft) return;
    const s = [...draft.steps];
    [s[i], s[i + d]] = [s[i + d], s[i]];
    setSteps(s);
  };

  const current = draft?.replace ? missions.find((m) => m.qualifiedName === draft.replace) : undefined;
  const draftKey = draft ? stepsKey(draft.steps) : "";
  const stepsChanged = !current || draftKey !== stepsKey(fromMission(current).steps);
  const incomplete = draft ? draft.steps.some((s) => quantities(s.behavior).some((q) => !s.values[q.name]?.trim())) : false;

  /** One evaluation: every mission (and the draft, if it is not a saved mission
   *  as is) flown by every system instance that performs behaviors. */
  const evaluate = async () => {
    if (!mtype || !draft) return;
    if (!(await ensureSaved())) return;
    setEvaluating(true);
    const sendDraft = stepsChanged && draft.steps.length > 0 && !incomplete;
    try {
      const res = await api.evaluateMissions(projectId, mtype.qualifiedName, sendDraft ? clean(draft.steps) : null);
      setEvals((m) => ({ ...m, [mtype.qualifiedName]: { ...res, at: new Date().toLocaleTimeString(), draftKey: sendDraft ? draftKey : null } }));
    } catch (e) {
      setEvals((m) => ({ ...m, [mtype.qualifiedName]: { missionType: mtype.qualifiedName, totals, missions: {}, errors: [errorText(e)], at: new Date().toLocaleTimeString(), draftKey: null } }));
    } finally {
      setEvaluating(false);
    }
  };

  // the catalog is re-read after every save: refresh an evaluation already on screen
  useEffect(() => {
    if (mtype && evals[mtype.qualifiedName]) evaluate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;

  if (!types.length)
    return (
      <div className="panel">
        {error && <div className="error-box">{error}</div>}
        <div className="empty-card">
          <div className="strong">No mission types in this project yet</div>
          <p>
            A behavior is an abstract <code>action def</code> tagged <code>#Behavior</code>. Its <code>in</code> parameters are set by the system that performs it, its attributes without a value by each mission step. An architecture performs behaviors; each system instance chooses a behavior instance with every parameter set. A mission type is an <code>action def</code> tagged <code>#Mission</code>, whose attributes are totals over the steps.
          </p>
          <code className="block">{`abstract #Behavior action def Cruise {\n    in speed : Real;\n    attribute distance : Real;\n    attribute duration : Real = distance / speed;\n}\n#SystemArchitecture part def Drone { perform action cruise : Cruise; }\npart d1 : Drone { perform action :>> cruise :> Behaviors::cruise12; }\n\n#Mission action def Delivery { attribute duration : Real; }\naction shortHop : Delivery {\n    first start;\n    then action cruise : Cruise { attribute :>> distance = 2000; }\n    then done;\n}`}</code>
          <p className="muted small">Open the “Delivery Drones” demo project for behaviors, missions and a mission energy requirement.</p>
        </div>
      </div>
    );

  const scope = draft?.replace ? current?.package || "" : draft?.package || (mtype ? defaultPackage(mtype) : "");
  const names = draft ? stepNames(draft.steps) : [];
  const preview =
    mtype && draft
      ? partText(
          `action ${draft.name || "…"} : ${ref(mtype.qualifiedName, scope)}`,
          draft.steps.length
            ? [
                "first start;",
                ...draft.steps.map((s, i) => {
                  const body = quantities(s.behavior)
                    .filter((q) => s.values[q.name]?.trim())
                    .map((q) => `attribute :>> ${q.name} = ${s.values[q.name].trim()};`);
                  return `then action ${names[i]} : ${ref(s.behavior, scope)}${body.length ? ` { ${body.join(" ")} }` : ";"}`;
                }),
                "then done;",
              ]
            : [],
          draft.doc
        )
      : "";
  const changed = !current || !draft || draft.name !== current.name || draft.doc !== (current.doc || "") || stepsChanged;
  // the draft's flights: the saved mission's while unchanged, else the draft sent along
  const draftResults: Record<string, FlownMission> | undefined = evaluation
    ? !stepsChanged && current
      ? evaluation.missions[current.qualifiedName]
      : evaluation.draftKey === draftKey
        ? evaluation.missions[DRAFT]
        : undefined
    : undefined;
  const flyerQn = flyer && draftResults?.[flyer] ? flyer : Object.keys(draftResults || {}).find((k) => !draftResults![k].problems.length) || null;
  const flight = flyerQn ? draftResults?.[flyerQn] : undefined;
  const timeTotal = totals[0];
  const reqDocs = Object.fromEntries(catalog.requirements.map((r) => [r.qualifiedName, r.doc || ""]));

  return (
    <div className="panel lib">
      <div className="panel-bar">
        <button className="primary" disabled={!mtype || busy} onClick={() => { setSelQn(null); setDraft(mtype ? blank(mtype) : null); }}>
          + New mission
        </button>
        <button disabled={evaluating || !mtype} onClick={evaluate} title="Fly every mission with every system instance: totals and mission requirements">
          {evaluating ? "Evaluating…" : "▶ Evaluate & check missions"}
        </button>
        {loading && <span className="hint">Refreshing…</span>}
        {dirty && <span className="hint">Unsaved edits are saved before a change.</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="lib-split">
        <div className="lib-list">
          {types.map((t) => (
            <div key={t.id}>
              <div className={`lib-item${mtype?.id === t.id && !selQn ? " on" : ""}`} onClick={() => pickType(t.qualifiedName)}>
                <span className="strong">{t.name}</span>
                <span className="count">{catalog.missions.filter((m) => m.missionTypeId === t.id).length || ""}</span>
              </div>
              {catalog.missions
                .filter((m) => m.missionTypeId === t.id)
                .map((m) => (
                  <div key={m.id} className={`lib-item sub${selQn === m.qualifiedName ? " on" : ""}`} onClick={() => pickType(t.qualifiedName, m.qualifiedName)}>
                    <span className="mono">{m.name}</span>
                    <FleetCount results={evals[t.qualifiedName]?.missions[m.qualifiedName]} />
                  </div>
                ))}
            </div>
          ))}
        </div>

        {mtype && (
          <div className="lib-detail">
            <div className="detail-head">
              <div>
                <div className="muted small">
                  mission type · {mtype.modelName} · totals {mtype.totals.map((t) => t.name).join(", ") || "none"}
                </div>
                <div className="title">{mtype.name}</div>
                {mtype.doc && <div className="small">{mtype.doc}</div>}
              </div>
              <button className="link" onClick={() => onOpen(mtype)}>
                Show in editor
              </button>
            </div>

            {draft && (
              <div className="form-card">
                <div className="row-between">
                  <span className="strong">{draft.replace ? `Mission ${short(draft.replace)}` : "New mission"}</span>
                  {current && (
                    <span>
                      <button className="link" onClick={() => onOpen(current)}>
                        Source
                      </button>
                      <button className="link" onClick={() => {
                          setSelQn(null);
                          setDraft({ ...draft, name: `${draft.name}Copy`, replace: null, package: "" });
                        }}>
                        Duplicate
                      </button>
                      <button className="link danger" disabled={busy} onClick={() => remove(current)}>
                        Delete
                      </button>
                    </span>
                  )}
                </div>
                <div className="grid3">
                  <label>
                    Name
                    <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={draft.name && !NAME_RE.test(draft.name) ? "invalid" : ""} />
                  </label>
                  <label>
                    Description
                    <input value={draft.doc} placeholder="3 km each way, winch drop" onChange={(e) => setDraft({ ...draft, doc: e.target.value })} />
                  </label>
                  <label>
                    Package
                    <select value={draft.replace ? draft.package : draft.package || defaultPackage(mtype)} disabled={!!draft.replace} onChange={(e) => setDraft({ ...draft, package: e.target.value })}>
                      {catalog.packages.map((p) => (
                        <option key={p.qualifiedName} value={p.qualifiedName}>
                          {p.qualifiedName} ({p.modelName})
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {draftResults && (
                  <div className="row-between">
                    <label className="inline small">
                      Flown by
                      <select value={flyerQn || ""} onChange={(e) => setFlyer(e.target.value)}>
                        {Object.entries(draftResults).map(([qn, r]) => (
                          <option key={qn} value={qn} disabled={r.problems.length > 0}>
                            {short(qn)}
                            {r.problems.length ? ` — ${r.problems.join("; ")}` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    {flight && timeTotal && <span className="muted small">{timeTotal} {fmt(flight.totals[timeTotal])}</span>}
                  </div>
                )}
                {flight && timeTotal && (flight.totals[timeTotal] || 0) > 0 && (
                  <div className="mission-strip" title={`${timeTotal} per step, flown by ${short(flyerQn)}`}>
                    {flight.steps.map((s, i) => (
                      <div key={i} className={`seg c${color(draft.steps[i]?.behavior)}`} style={{ flexGrow: Math.max(s.values[timeTotal] || 0, 0) }} title={`${i + 1}. ${s.name} (${short(s.performedBy)}): ${timeTotal} ${fmt(s.values[timeTotal])}`}>
                        {s.name}
                      </div>
                    ))}
                  </div>
                )}

                <table className="table compact slots">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Behavior</th>
                      <th>Quantities</th>
                      {flight && totals.map((t) => <th key={t} className="mono num">{t}</th>)}
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {draft.steps.map((s, i) => (
                      <tr key={i}>
                        <td className="muted">{i + 1}</td>
                        <td>
                          <select value={s.behavior} onChange={(e) => {
                              const keep = new Set(quantities(e.target.value).map((q) => q.name));
                              setStep(i, { behavior: e.target.value, values: Object.fromEntries(Object.entries(s.values).filter(([k]) => keep.has(k))) });
                            }}>
                            {!stepTypes.some((t) => t.c.qualifiedName === s.behavior) && <option value={s.behavior}>{short(s.behavior)}</option>}
                            {stepTypes.map(({ c }) => (
                              <option key={c.id} value={c.qualifiedName}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                          {flight?.steps[i] && <div className="muted small mono">by {short(flight.steps[i].performedBy)}</div>}
                        </td>
                        <td>
                          <div className="step-values">
                            {quantities(s.behavior).map((q) => (
                              <label key={q.name} className="inline small">
                                <span className="mono">{q.name}</span>
                                <input className={`mono${s.values[q.name]?.trim() ? "" : " invalid"}`} value={s.values[q.name] || ""} placeholder={placeholder(q.type)} onChange={(e) => setStep(i, { ...s, values: { ...s.values, [q.name]: e.target.value } })} />
                              </label>
                            ))}
                            {!quantities(s.behavior).length && <span className="muted small">none</span>}
                          </div>
                        </td>
                        {flight && totals.map((t) => <td key={t} className="mono small num">{fmt(flight.steps[i]?.values[t])}</td>)}
                        <td className="actions">
                          <button className="icon" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                          <button className="icon" title="Move down" disabled={i === draft.steps.length - 1} onClick={() => move(i, 1)}>↓</button>
                          <button className="icon" title="Remove step" onClick={() => setSteps(draft.steps.filter((_, j) => j !== i))}>×</button>
                        </td>
                      </tr>
                    ))}
                    {!draft.steps.length && (
                      <tr>
                        <td colSpan={4} className="muted small">No steps yet: add behaviors below, in the order they are performed.</td>
                      </tr>
                    )}
                    {flight && draft.steps.length > 0 && (
                      <tr className="total-row">
                        <td />
                        <td className="strong">Total</td>
                        <td className="muted small">flown by {short(flyerQn)}</td>
                        {totals.map((t) => <td key={t} className="mono small num strong">{fmt(flight.totals[t])}</td>)}
                        <td />
                      </tr>
                    )}
                  </tbody>
                </table>

                <div className="section-title">Add a step</div>
                <div className="palette-group">
                  {stepTypes.map(({ c, by }) => (
                    <button
                      key={c.id}
                      className={`step-chip c${color(c.qualifiedName)}`}
                      title={[c.doc, quantities(c.qualifiedName).length ? `sets ${quantities(c.qualifiedName).map((q) => q.name).join(", ")}` : "", by.length ? `performed by ${by.join(", ")}` : ""].filter(Boolean).join("\n")}
                      onClick={() => setSteps([...draft.steps, { behavior: c.qualifiedName, values: {} }])}
                    >
                      + {c.name}
                    </button>
                  ))}
                  {!stepTypes.length && <span className="muted small">No behaviors yet: declare #Behavior action defs in the Library tab.</span>}
                </div>

                <code className="block">{preview}</code>
                {draftResults && <VerificationTable results={draftResults} docs={reqDocs} />}
                <div className="row-end">
                  {incomplete && <span className="hint">Every step needs all its quantities.</span>}
                  {!incomplete && !draftResults && draft.steps.length > 0 && <span className="hint">Evaluate to fly it with every system instance.</span>}
                  {draft.replace && changed && <button onClick={() => current && setDraft(fromMission(current))}>Revert</button>}
                  <button className="primary" disabled={busy || !NAME_RE.test(draft.name) || !changed || incomplete} onClick={save}>
                    {busy ? "Saving…" : draft.replace ? "Save mission" : "Create mission"}
                  </button>
                </div>
              </div>
            )}

            {draft && catalog.simulations.length > 0 && (
              <MissionSimulation
                projectId={projectId}
                missionType={mtype.qualifiedName}
                mission={current && !stepsChanged ? current.qualifiedName : null}
                draft={current && !stepsChanged ? null : clean(draft.steps)}
                label={current && !stepsChanged ? current.name : "draft"}
                systems={catalog.configurations}
                ensureSaved={ensureSaved}
                refreshKey={catalog}
                disabled={!draft.steps.length || incomplete}
              />
            )}

            <div className="section-title row-between">
              <span>Compare missions</span>
              {evaluation && <span className="muted small">evaluated {evaluation.at}</span>}
            </div>
            {missions.length ? (
              <div className="table-scroll">
                <table className="table compact">
                  <thead>
                    <tr>
                      <th>Mission</th>
                      <th className="num">Steps</th>
                      {fliers.map((c) => (
                        <th key={c.id} className="mono" title={c.qualifiedName}>
                          {c.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {missions.map((m) => (
                      <tr key={m.id} className={`clickable${selQn === m.qualifiedName ? " row-on" : ""}`} onClick={() => setSelQn(m.qualifiedName)}>
                        <td>
                          <div className="mono strong">{m.name}</div>
                          {m.doc && <div className="muted small">{m.doc}</div>}
                        </td>
                        <td className="num small">{m.steps.length}</td>
                        {fliers.map((c) => {
                          const r = evaluation?.missions[m.qualifiedName]?.[c.qualifiedName];
                          return (
                            <td key={c.id}>
                              {r ? (
                                <>
                                  <FlightPill r={r} />
                                  {!r.problems.length && timeTotal && <div className="mono small muted">{timeTotal} {fmt(r.totals[timeTotal])}</div>}
                                </>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="muted small">No {mtype.name} missions yet — add steps above and create one.</div>
            )}
            {!evaluation && missions.length > 0 && <div className="hint">Evaluate to fly every mission with every system instance, using the behaviors each performs, and check the mission requirements.</div>}
            {evaluation && evaluation.errors.length > 0 && <div className="error-box">{evaluation.errors.join("\n")}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

/** whether one system instance can fly a mission: performs every step and meets the mission requirements */
function FlightPill({ r, name }: { r: FlownMission; name?: string }) {
  const verdicts = Object.entries(r.verdicts);
  const title = r.problems.length
    ? r.problems.join("\n")
    : [...Object.entries(r.totals).map(([t, v]) => `${t} ${fmt(v)}`), ...verdicts.map(([q, v]) => `${short(q)}: ${v} (${how(r.verification?.[q])})`)].join("\n");
  if (r.problems.length)
    return (
      <span className="pill" title={title}>
        ⊘ {name ? `${name} ` : ""}cannot perform
      </span>
    );
  const bad = verdicts.filter(([, v]) => v !== "holds");
  const label = bad.length ? (bad.some(([, v]) => v === "error") ? "? error" : "✗ fails") : "✓ holds";
  return (
    <span className={`pill ${bad.length ? "bad" : "ok"}`} title={title}>
      {name ? `${bad.length ? "✗" : "✓"} ${name}` : label}
    </span>
  );
}

/** "simulated by MissionSimulation: energyPercent 75.2" */
function how(v?: Verification): string {
  if (!v) return "";
  const vals = Object.entries(v.values || {}).map(([k, x]) => `${k} ${fmt(x)}`).join(", ");
  const by = v.method === "simulation" ? `simulated by ${short(v.by)}` : "estimated from the mission totals";
  return [by, v.note || vals].filter(Boolean).join(": ");
}

/** each system's verdict on each mission requirement, and how it was verified */
function VerificationTable({ results, docs }: { results: Record<string, FlownMission>; docs: Record<string, string> }) {
  const reqs = [...new Set(Object.values(results).flatMap((r) => Object.keys(r.verification || {})))];
  if (!reqs.length) return null;
  const method = (q: string) => Object.values(results).find((r) => r.verification?.[q])?.verification[q];
  return (
    <div className="table-scroll">
      <table className="table compact">
        <thead>
          <tr>
            <th>Requirement verification</th>
            {reqs.map((q) => {
              const m = method(q);
              return (
                <th key={q} title={docs[q]}>
                  {short(q)}
                  <div className="muted small">{m?.method === "simulation" ? `◆ simulated · ${short(m.by)}` : "○ estimated · mission totals"}</div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {Object.entries(results).map(([qn, r]) => (
            <tr key={qn}>
              <td className="mono strong">{short(qn)}</td>
              {r.problems.length ? (
                <td colSpan={reqs.length} className="muted small">
                  ⊘ {r.problems.join("; ")}
                </td>
              ) : (
                reqs.map((q) => {
                  const v = r.verdicts[q];
                  const ver = r.verification?.[q];
                  return (
                    <td key={q} title={how(ver)}>
                      {v ? <span className={`pill ${v === "holds" ? "ok" : "bad"}`}>{v === "holds" ? "✓ holds" : v === "fails" ? "✗ fails" : "? error"}</span> : <span className="muted">—</span>}
                      <div className="mono small muted">{ver?.note || Object.entries(ver?.values || {}).map(([k, x]) => `${k} ${fmt(x)}`).join(", ")}</div>
                    </td>
                  );
                })
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** how many of the system instances can fly the mission */
function FleetCount({ results }: { results?: Record<string, FlownMission> }) {
  if (!results) return null;
  const rs = Object.values(results);
  const ok = rs.filter((r) => !r.problems.length && Object.values(r.verdicts).every((v) => v === "holds")).length;
  return (
    <span className="count" title={`${ok} of ${rs.length} system instances can fly it`}>
      <span className={`dot ${ok ? "ok" : "bad"}`} />
      {ok}/{rs.length}
    </span>
  );
}

function fmt(n?: number | null): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? String(Math.round(n)) : String(Number(n.toPrecision(3)));
}
