import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Architecture, CatalogChange, ComponentInstance, SystemConfiguration } from "../types";
import { NAME_RE, errorText, partText, ref, short, type CatalogPanelProps } from "./catalogUtil";

interface Draft {
  name: string;
  doc: string;
  package: string;
  assignments: Record<string, string | null>;
  /** qualified name of the system instance being edited; null for a new one */
  replace: string | null;
}

interface Evaluation {
  at: string;
  /** config qualified name -> attribute -> value */
  values: Record<string, Record<string, string>>;
  /** config qualified name -> requirement qualified name -> verdict */
  verdicts: Record<string, Record<string, "holds" | "fails" | "error">>;
  errors: string[];
}

const lowerFirst = (s: string) => s[0].toLowerCase() + s.slice(1);

/** System configurator: pick a #SystemArchitecture, fill its slots with catalog
 *  instances, and save the result as a system instance (a part usage). */
export function ConfiguratorPanel({ projectId, catalog, loading, error, dirty, ensureSaved, onChange, onOpen }: CatalogPanelProps) {
  const [archQn, setArchQn] = useState<string | null>(null);
  const [selQn, setSelQn] = useState<string | null>(null); // system instance shown in the form
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evals, setEvals] = useState<Record<string, Evaluation>>({});

  const archs = catalog?.architectures || [];
  const arch = archs.find((a) => a.qualifiedName === archQn) || archs[0];
  const configs = useMemo(() => (catalog?.configurations || []).filter((c) => arch && c.architectureId === arch.id), [catalog, arch]);
  const instById = useMemo(() => new Map((catalog?.instances || []).map((i) => [i.id, i])), [catalog]);
  const instByQn = useMemo(() => new Map((catalog?.instances || []).map((i) => [i.qualifiedName, i])), [catalog]);
  const evaluation = arch ? evals[arch.qualifiedName] : undefined;

  const blank = (a: Architecture): Draft => {
    const base = lowerFirst(a.name);
    let n = 1;
    while (catalog?.configurations.some((c) => c.name === `${base}${n}`)) n++;
    return { name: `${base}${n}`, doc: "", package: "", assignments: Object.fromEntries(a.slots.map((s) => [s.name, null])), replace: null };
  };
  const fromConfig = (c: SystemConfiguration): Draft => ({ name: c.name, doc: c.doc || "", package: c.package || "", assignments: { ...c.assignments }, replace: c.qualifiedName });

  // keep the form in step with the catalog (it is re-read after every save)
  useEffect(() => {
    if (!arch) return setDraft(null);
    const cfg = selQn ? configs.find((c) => c.qualifiedName === selQn) : undefined;
    const fits = (d: Draft) => Object.keys(d.assignments).every((s) => arch.slots.some((x) => x.name === s));
    if (cfg) setDraft(fromConfig(cfg));
    // a just-saved system instance shows up once the catalog has been re-read: keep the draft until then
    else setDraft((d) => (d && (selQn || !d.replace) && fits(d) ? d : blank(arch)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arch, configs, selQn]);

  const pickArch = (qn: string, cfg: string | null = null) => {
    setArchQn(qn);
    setSelQn(cfg);
    const a = archs.find((x) => x.qualifiedName === qn);
    if (!cfg && a) setDraft(blank(a));
  };

  const defaultPackage = (a: Architecture) => {
    const root = a.qualifiedName.split("::")[0];
    const cands = (catalog?.packages || []).filter((p) => p.modelId === a.modelId && short(p.qualifiedName) === "Configurations").map((p) => p.qualifiedName);
    cands.sort((x, y) => Number(!x.startsWith(root + "::")) - Number(!y.startsWith(root + "::")) || x.split("::").length - y.split("::").length);
    return cands[0] || a.package || "";
  };

  const run = async (fn: () => Promise<CatalogChange>) => {
    setBusy(true);
    try {
      return await onChange(fn);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!arch || !draft) return;
    const r = await run(() =>
      api.saveConfiguration(projectId, {
        architecture: arch.qualifiedName,
        name: draft.name,
        doc: draft.doc,
        package: draft.replace ? null : draft.package || null,
        assignments: draft.assignments,
        replace: draft.replace,
      })
    );
    if (r) setSelQn(r.qualifiedName);
  };

  const remove = async (c: SystemConfiguration) => {
    if (!confirm(`Remove system instance ${c.name} from ${c.modelName}?\nThis is refused while something else (a satisfy relation, a requirement subject…) still refers to it.`)) return;
    const r = await run(() => api.deleteCatalogElement(projectId, c.qualifiedName));
    if (r) setSelQn(null);
  };

  /** One evaluate run per model: every derived attribute of every system
   *  instance, and Req(system) for each applicable requirement def. */
  const evaluate = async () => {
    if (!arch || !configs.length) return;
    if (!(await ensureSaved())) return;
    setEvaluating(true);
    const out: Evaluation = { at: new Date().toLocaleTimeString(), values: {}, verdicts: {}, errors: [] };
    try {
      const byModel = new Map<string, SystemConfiguration[]>();
      for (const c of configs) if (c.modelId) byModel.set(c.modelId, [...(byModel.get(c.modelId) || []), c]);
      for (const [modelId, cs] of byModel) {
        const exprs: { cfg: string; key: string; req?: boolean; text: string }[] = [];
        for (const c of cs) {
          for (const a of arch.attributes) exprs.push({ cfg: c.qualifiedName, key: a.name, text: `${c.qualifiedName}.${a.name}` });
          for (const r of arch.requirements) exprs.push({ cfg: c.qualifiedName, key: r, req: true, text: `${r}(${c.qualifiedName})` });
        }
        const runRes = await api.run(modelId, { kind: "evaluate", expression: exprs.map((e) => e.text).join("\n"), trace: false });
        const got = new Map((runRes.result?.checks || []).map((ch) => [ch.subject, ch.values[0]?.value]));
        for (const e of exprs) {
          const v = got.get(e.text);
          if (e.req) {
            (out.verdicts[e.cfg] ||= {})[e.key] = v === "true" ? "holds" : v === "false" ? "fails" : "error";
          } else if (v !== undefined) {
            (out.values[e.cfg] ||= {})[e.key] = v;
          }
        }
        out.errors.push(...(runRes.result?.errors || []));
      }
    } catch (e) {
      out.errors.push(errorText(e));
    } finally {
      setEvaluating(false);
      setEvals((m) => ({ ...m, [arch.qualifiedName]: out }));
    }
  };

  // the catalog is re-read after every save: refresh a comparison already on screen
  useEffect(() => {
    if (arch && evals[arch.qualifiedName]) evaluate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;

  if (!archs.length)
    return (
      <div className="panel">
        {error && <div className="error-box">{error}</div>}
        <div className="empty-card">
          <div className="strong">No system architectures in this project yet</div>
          <p>
            A system architecture is a <code>part def</code> tagged <code>#SystemArchitecture</code>. Each of its part usages typed by a component is a <em>slot</em>; a system instance fills every slot with an instance from the component library.
          </p>
          <code className="block">{`#SystemArchitecture part def Car {\n    part engine : Engine;\n    part tank : FuelTank;\n    connect tank.fuelOut to engine.fuelIn;\n    attribute mass : MassValue = engine.mass + tank.mass;\n}\n\npart economy : Car {\n    part :>> engine :> Catalog::i4Engine;\n    part :>> tank :> Catalog::tank45;\n}`}</code>
          <p className="muted small">Create a model from the “System architecture” template (Model ▸ +), or open the “Product Line” demo project.</p>
        </div>
      </div>
    );

  const scope = draft?.replace ? configs.find((c) => c.qualifiedName === draft.replace)?.package || "" : draft?.package || (arch ? defaultPackage(arch) : "");
  const open = arch && draft ? arch.slots.filter((s) => !draft.assignments[s.name]).length : 0;
  const preview =
    arch && draft
      ? partText(
          `part ${draft.name || "…"} : ${ref(arch.qualifiedName, scope)}`,
          arch.slots.filter((s) => draft.assignments[s.name]).map((s) => `part :>> ${s.name} :> ${ref(draft.assignments[s.name]!, scope)};`),
          draft.doc
        )
      : "";
  const current = draft?.replace ? configs.find((c) => c.qualifiedName === draft.replace) : undefined;
  const changed = !current || !draft || draft.name !== current.name || draft.doc !== (current.doc || "") || arch!.slots.some((s) => (draft.assignments[s.name] || null) !== (current.assignments[s.name] || null));

  return (
    <div className="panel lib">
      <div className="panel-bar">
        <button className="primary" disabled={!arch || busy} onClick={() => { setSelQn(null); setDraft(arch ? blank(arch) : null); }}>
          + New system instance
        </button>
        <button disabled={!configs.length || evaluating} onClick={evaluate} title="Evaluate the derived attributes and the requirements of every system instance of this architecture">
          {evaluating ? "Evaluating…" : "▶ Evaluate & check requirements"}
        </button>
        {loading && <span className="hint">Refreshing…</span>}
        {dirty && <span className="hint">Unsaved edits are saved before a change.</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="lib-split">
        <div className="lib-list">
          {archs.map((a) => (
            <div key={a.id}>
              <div className={`lib-item${arch?.id === a.id && !selQn ? " on" : ""}`} onClick={() => pickArch(a.qualifiedName)}>
                <span className="strong">{a.name}</span>
                <span className="count">{catalog.configurations.filter((c) => c.architectureId === a.id).length || ""}</span>
              </div>
              {catalog.configurations
                .filter((c) => c.architectureId === a.id)
                .map((c) => (
                  <div key={c.id} className={`lib-item sub${selQn === c.qualifiedName ? " on" : ""}`} onClick={() => pickArch(a.qualifiedName, c.qualifiedName)}>
                    <span className="mono">{c.name}</span>
                    <VerdictDots verdicts={evals[a.qualifiedName]?.verdicts[c.qualifiedName]} />
                  </div>
                ))}
            </div>
          ))}
        </div>

        {arch && (
          <div className="lib-detail">
            <div className="detail-head">
              <div>
                <div className="muted small">
                  system architecture · {arch.modelName} · {arch.slots.length} slots · {arch.connections} connections
                </div>
                <div className="title">
                  {arch.name}
                  {arch.specializes.length > 0 && <span className="muted"> :&gt; {arch.specializes.map(short).join(", ")}</span>}
                </div>
                {arch.doc && <div className="small">{arch.doc}</div>}
              </div>
              <button className="link" onClick={() => onOpen(arch)}>
                Show in editor
              </button>
            </div>

            {draft && (
              <div className="form-card">
                <div className="row-between">
                  <span className="strong">{draft.replace ? `System instance ${short(draft.replace)}` : "New system instance"}</span>
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
                    <input value={draft.doc} placeholder="Long-distance trim" onChange={(e) => setDraft({ ...draft, doc: e.target.value })} />
                  </label>
                  <label>
                    Package
                    <select value={draft.replace ? draft.package : draft.package || defaultPackage(arch)} disabled={!!draft.replace} onChange={(e) => setDraft({ ...draft, package: e.target.value })}>
                      {catalog.packages.map((p) => (
                        <option key={p.qualifiedName} value={p.qualifiedName}>
                          {p.qualifiedName} ({p.modelName})
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <table className="table compact slots">
                  <thead>
                    <tr>
                      <th>Slot</th>
                      <th>Component instance</th>
                      <th>Values</th>
                    </tr>
                  </thead>
                  <tbody>
                    {arch.slots.map((s) => {
                      const cands = s.candidates.map((id) => instById.get(id)).filter((i): i is ComponentInstance => !!i);
                      const chosen = draft.assignments[s.name] ? instByQn.get(draft.assignments[s.name]!) : undefined;
                      return (
                        <tr key={s.name}>
                          <td>
                            <div className="mono strong">{s.name}</div>
                            <div className="mono muted small">
                              : {short(s.type)}
                              {s.multiplicity || ""}
                            </div>
                          </td>
                          <td>
                            <select value={draft.assignments[s.name] || ""} onChange={(e) => setDraft({ ...draft, assignments: { ...draft.assignments, [s.name]: e.target.value || null } })}>
                              <option value="">{cands.length ? "— choose —" : "— no instance fits —"}</option>
                              {cands.map((i) => (
                                <option key={i.id} value={i.qualifiedName}>
                                  {i.name}
                                  {i.component !== s.type ? ` (${short(i.component)})` : ""}
                                  {i.doc ? ` — ${i.doc}` : ""}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="mono small muted">
                            {chosen
                              ? Object.entries(chosen.values)
                                  .map(([k, v]) => `${k} ${v}`)
                                  .join(" · ")
                              : ""}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <code className="block">{preview}</code>
                <div className="row-end">
                  {open > 0 && <span className="hint">{open} slot{open > 1 ? "s" : ""} left open (kept as the architecture declares {open > 1 ? "them" : "it"}).</span>}
                  {draft.replace && changed && <button onClick={() => current && setDraft(fromConfig(current))}>Revert</button>}
                  <button className="primary" disabled={busy || !NAME_RE.test(draft.name) || !changed} onClick={save}>
                    {busy ? "Saving…" : draft.replace ? "Save system instance" : "Create system instance"}
                  </button>
                </div>
              </div>
            )}

            <div className="section-title row-between">
              <span>Compare system instances</span>
              {evaluation && <span className="muted small">evaluated {evaluation.at}</span>}
            </div>
            {configs.length ? (
              <div className="table-scroll">
                <table className="table compact">
                  <thead>
                    <tr>
                      <th>System</th>
                      {arch.slots.map((s) => (
                        <th key={s.name} className="mono">
                          {s.name}
                        </th>
                      ))}
                      {arch.attributes.map((a) => (
                        <th key={a.name} className="mono num">
                          {a.name}
                        </th>
                      ))}
                      {arch.requirements.map((r) => (
                        <th key={r} title={catalog.requirements.find((x) => x.qualifiedName === r)?.doc || r}>
                          {short(r)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {configs.map((c) => (
                      <tr key={c.id} className={`clickable${selQn === c.qualifiedName ? " row-on" : ""}`} onClick={() => setSelQn(c.qualifiedName)}>
                        <td>
                          <div className="mono strong">{c.name}</div>
                          {c.doc && <div className="muted small">{c.doc}</div>}
                        </td>
                        {arch.slots.map((s) => (
                          <td key={s.name} className="mono small">
                            {c.assignments[s.name] ? short(c.assignments[s.name]) : <span className="muted">—</span>}
                          </td>
                        ))}
                        {arch.attributes.map((a) => (
                          <td key={a.name} className="mono small num">
                            {formatValue(evaluation?.values[c.qualifiedName]?.[a.name])}
                          </td>
                        ))}
                        {arch.requirements.map((r) => {
                          const v = evaluation?.verdicts[c.qualifiedName]?.[r];
                          return (
                            <td key={r}>
                              {v ? <span className={`pill ${v === "holds" ? "ok" : "bad"}`}>{v === "holds" ? "✓ holds" : v === "fails" ? "✗ fails" : "? error"}</span> : <span className="muted">—</span>}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="muted small">No system instances of {arch.name} yet — fill the slots above and create one.</div>
            )}
            {!evaluation && configs.length > 0 && <div className="hint">Evaluate to compute {arch.attributes.map((a) => a.name).join(", ") || "the attributes"}{arch.requirements.length ? " and check the requirements" : ""} for every system instance.</div>}
            {evaluation && evaluation.errors.length > 0 && <div className="error-box">{evaluation.errors.join("\n")}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

function VerdictDots({ verdicts }: { verdicts?: Record<string, string> }) {
  if (!verdicts) return null;
  const vs = Object.values(verdicts);
  const bad = vs.filter((v) => v !== "holds").length;
  return <span className={`dot ${bad ? "bad" : "ok"}`} title={bad ? `${bad} of ${vs.length} requirements fail` : "all requirements hold"} />;
}

/** "1388.0000001 [SI::kg]" -> "1388 kg" */
function formatValue(v?: string): string {
  if (v === undefined) return "—";
  const m = /^(-?[\d.eE+-]+)\s*(?:\[(?:SI::)?'?([^'\]]+)'?\])?$/.exec(v.trim());
  if (!m) return v;
  const n = Number(m[1]);
  const num = !Number.isFinite(n) ? m[1] : Math.abs(n) >= 100 ? String(Math.round(n)) : String(Number(n.toPrecision(3)));
  return m[2] ? `${num} ${m[2]}` : num;
}
