import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { CatalogAttribute, CatalogChange, CatalogRequirement, Component, ComponentInstance } from "../types";
import { NAME_RE, partText, placeholder, ref, short, type CatalogPanelProps } from "./catalogUtil";
import { ComponentAnalysisCard } from "./ComponentAnalysis";
import { fillDoc, missingValues, openThresholds, ownValues, ReqId, reqId, RequirementValues, requirementLines, walkUsages } from "./RequirementValues";

interface InstanceForm {
  component: string;
  name: string;
  doc: string;
  package: string;
  values: Record<string, string>;
  /** requirement value ("usage.attribute") -> number */
  requirements: Record<string, string>;
  /** component-typed part -> instance that fills it (a material) */
  parts: Record<string, string | null>;
  replace: string | null;
}

interface RequirementRow {
  name: string;
  /** qualified name of a library requirement def */
  type: string;
  /** attribute -> expression bound in the def (the others are left to each instance) */
  values: Record<string, string>;
}

interface ComponentForm {
  kind: "part" | "action";
  name: string;
  package: string;
  specializes: string;
  abstract: boolean;
  doc: string;
  attributes: { name: string; type: string }[];
  ports: { name: string; type: string }[];
  /** a behavior's in parameters */
  params: { name: string; type: string }[];
  requirements: RequirementRow[];
}

/** Component library: component types (#Component part defs), abstract
 *  behaviors (#Behavior action defs with in parameters) and their instances. */
export function LibraryPanel({ projectId, catalog, loading, error, dirty, onChange, onOpen }: CatalogPanelProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState<InstanceForm | null>(null);
  const [compForm, setCompForm] = useState<ComponentForm | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const formOpen = !!form || !!compForm;
  useEffect(() => {
    if (formOpen) panel.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [formOpen, form?.replace]);

  const components = catalog?.components || [];
  const instances = catalog?.instances || [];
  const byQn = useMemo(() => new Map(components.map((c) => [c.qualifiedName, c])), [components]);
  const comp = components.find((c) => c.qualifiedName === selected) || components[0];

  useEffect(() => {
    if (!selected && components.length) setSelected(components[0].qualifiedName);
  }, [components, selected]);

  // specialization tree of the component types
  const tree = useMemo(() => {
    const kids = new Map<string, Component[]>();
    const roots: Component[] = [];
    for (const c of components) {
      const parent = c.specializes.find((s) => byQn.has(s));
      if (parent) kids.set(parent, [...(kids.get(parent) || []), c]);
      else roots.push(c);
    }
    const out: { c: Component; depth: number }[] = [];
    const walk = (c: Component, depth: number) => {
      out.push({ c, depth });
      for (const k of kids.get(c.qualifiedName) || []) walk(k, depth + 1);
    };
    roots.forEach((r) => walk(r, 0));
    return out;
  }, [components, byQn]);

  /** the component and every component specializing it */
  const family = (c: Component) => {
    const ids = new Set([c.qualifiedName]);
    for (let grew = true; grew; ) {
      grew = false;
      for (const x of components)
        if (!ids.has(x.qualifiedName) && x.specializes.some((s) => ids.has(s))) {
          ids.add(x.qualifiedName);
          grew = true;
        }
    }
    return ids;
  };
  const shown = comp ? instances.filter((i) => family(comp).has(i.component)) : [];
  // abstract behaviors get instances (their parameters set); abstract components do not
  const concrete = components.filter((c) => !c.isAbstract || c.kind === "action");
  const hasBehaviors = components.some((c) => c.kind === "action");
  const visible = tree.filter(({ c }) => !filter || c.name.toLowerCase().includes(filter.toLowerCase()) || instances.some((i) => i.component === c.qualifiedName && i.name.toLowerCase().includes(filter.toLowerCase())));

  const newInstance = (c?: Component) => {
    const target = c && concrete.includes(c) ? c : concrete.find((x) => c && family(c).has(x.qualifiedName)) || concrete[0];
    if (!target) return;
    const n = instances.filter((i) => i.component === target.qualifiedName).length + 1;
    setCompForm(null);
    setForm({ component: target.qualifiedName, name: `${target.name[0].toLowerCase()}${target.name.slice(1)}${n}`, doc: "", package: "", values: {}, requirements: {}, parts: {}, replace: null });
  };
  const editInstance = (i: ComponentInstance) => {
    setCompForm(null);
    setForm({ component: i.component, name: i.name, doc: i.doc || "", package: i.package || "", values: { ...i.ownValues }, requirements: ownValues(i.requirementUsages), parts: { ...i.parts }, replace: i.qualifiedName });
  };
  const newComponent = (kind: "part" | "action") => {
    setForm(null);
    const tag = catalog?.tags[TAG[kind]];
    const pkg = (comp?.kind === kind && comp.package) || catalog?.packages.find((p) => tag && tag.startsWith(p.qualifiedName + "::"))?.qualifiedName || "";
    const attributes = kind === "part" ? [{ name: "mass", type: "MassValue" }] : [{ name: "area", type: "Real" }];
    const params = kind === "part" ? [] : [{ name: "speed", type: "Real" }];
    const general = kind === "action" ? components.find((c) => c.kind === "action" && !c.specializes.some((g) => byQn.has(g))) : undefined;
    setCompForm({ kind, name: "", package: pkg, specializes: general?.qualifiedName || "", abstract: kind === "action", doc: "", attributes, ports: [], params, requirements: [] });
  };

  const run = async (fn: () => Promise<CatalogChange>, after?: (qn: string) => void) => {
    setBusy(true);
    try {
      const r = await onChange(fn);
      if (r) after?.(r.qualifiedName);
      return r;
    } finally {
      setBusy(false);
    }
  };

  const saveInstance = () =>
    form &&
    run(
      () =>
        api.saveInstance(projectId, {
          ...form,
          package: form.package || null,
          values: Object.fromEntries(Object.entries(form.values).filter(([, v]) => v.trim())),
          parts: Object.fromEntries(subParts(formComp!).map((p) => [p.name, form.parts[p.name] || null])),
        }),
      () => setForm(null)
    );
  const deleteInstance = (i: ComponentInstance) => {
    if (!confirm(`Remove instance ${i.name} from ${i.modelName}?\nThis is refused while a system instance still uses it.`)) return;
    run(() => api.deleteCatalogElement(projectId, i.qualifiedName), () => form?.replace === i.qualifiedName && setForm(null));
  };
  const saveComponent = () =>
    compForm &&
    run(
      () =>
        api.createComponent(projectId, {
          ...compForm,
          specializes: compForm.specializes ? ref(compForm.specializes, compForm.package) : null,
          attributes: compForm.attributes.filter((a) => a.name.trim()),
          ports: compForm.kind === "part" ? compForm.ports.filter((p) => p.name.trim()) : [],
          params: compForm.kind === "action" ? compForm.params.filter((p) => p.name.trim()) : [],
          requirements: compForm.requirements.filter((r) => r.name.trim() && r.type),
        }),
      (qn) => {
        setCompForm(null);
        setSelected(qn);
      }
    );

  const formComp = form ? byQn.get(form.component) : undefined;
  const formPkg = form ? form.package || (form.replace ? "" : defaultCatalogPackage(formComp)) : "";
  function defaultCatalogPackage(c?: Component) {
    if (!c || !catalog) return "";
    const root = c.qualifiedName.split("::")[0];
    const name = c.kind === "action" ? "Behaviors" : "Catalog";
    const cands = catalog.packages.filter((p) => p.modelId === c.modelId && short(p.qualifiedName) === name).map((p) => p.qualifiedName);
    cands.sort((a, b) => Number(!a.startsWith(root + "::")) - Number(!b.startsWith(root + "::")) || a.split("::").length - b.split("::").length);
    return cands[0] || c.package || "";
  }
  const templates = (catalog?.requirements || []).filter((r) => r.template || !r.subjectTypeId);
  const reqMissing = form && formComp ? missingValues(formComp.requirementUsages, form.requirements) : [];
  const scope = form?.replace ? instances.find((i) => i.qualifiedName === form.replace)?.package || "" : formPkg;
  const instancePreview =
    form && formComp
      ? partText(
          `${formComp.kind} ${form.name || "…"} : ${ref(formComp.qualifiedName, scope)}`,
          fields(formComp)
            .filter((a) => form.values[a.name]?.trim())
            .map((a) => `${formComp.params.includes(a) ? "in" : "attribute"} :>> ${a.name} = ${form.values[a.name].trim()};`)
            .concat(subParts(formComp).filter((p) => form.parts[p.name]).map((p) => `part :>> ${p.name} :> ${ref(form.parts[p.name]!, scope)};`))
            .concat(requirementLines(form.requirements, formComp.requirementUsages)),
          form.doc
        )
      : "";
  const compPreview = compForm
    ? partText(
        `${compForm.abstract ? "abstract " : ""}#${ref(catalog?.tags[TAG[compForm.kind]] || TAG[compForm.kind], compForm.package)} ${compForm.kind} def ${compForm.name || "…"}${compForm.specializes ? ` :> ${ref(compForm.specializes, compForm.package)}` : ""}`,
        [
          // a behavior's own parameters redefine the inherited ones by position: those are restated first
          ...(compForm.kind === "action" && compForm.params.some((p) => p.name.trim()) ? byQn.get(compForm.specializes)?.params || [] : []).map((p) => `in :>> ${p.name};`),
          ...(compForm.kind === "action" ? compForm.params : []).filter((p) => p.name.trim()).map((p) => `in ${p.name.trim()}${p.type.trim() ? ` : ${p.type.trim()}` : ""};`),
          ...compForm.attributes.filter((a) => a.name.trim()).map((a) => `attribute ${a.name.trim()}${a.type.trim() ? ` : ${a.type.trim()}` : ""};`),
          ...(compForm.kind === "part" ? compForm.ports : []).filter((p) => p.name.trim()).map((p) => `port ${p.name.trim()}${p.type.trim() ? ` : ${p.type.trim()}` : ""};`),
          ...compForm.requirements.filter((r) => r.name.trim() && r.type).map((r) => {
            const binds = Object.entries(r.values).filter(([, v]) => v.trim()).map(([a, v]) => `attribute :>> ${a} = ${v.trim()};`);
            return `requirement ${r.name.trim()} : ${ref(r.type, compForm.package)}${binds.length ? ` { ${binds.join(" ")} }` : ";"}`;
          }),
        ],
        compForm.doc
      ).replace(/;$/, " {\n}")
    : "";

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;

  return (
    <div className="panel lib" ref={panel}>
      <div className="panel-bar">
        <button className="primary" disabled={!concrete.length || busy} onClick={() => newInstance(comp)}>
          + New instance
        </button>
        <button disabled={!catalog.tags.Component || busy} onClick={() => newComponent("part")} title={catalog.tags.Component ? "Declare a new #Component part def" : "No 'metadata def Component' in this project"}>
          + New component
        </button>
        {catalog.tags.Behavior && (
          <button disabled={busy} onClick={() => newComponent("action")} title="Declare a new #Behavior action def: a mission step">
            + New behavior
          </button>
        )}
        <input className="lib-filter" placeholder="Filter types and instances" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {loading && <span className="hint">Refreshing…</span>}
        {dirty && <span className="hint">Unsaved edits are saved before a change.</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      {form && (
        <div className="form-card">
          <div className="strong">{form.replace ? `Edit instance ${short(form.replace)}` : `New ${formComp?.kind === "action" ? "behavior" : "component"} instance`}</div>
          <div className="grid3">
            <label>
              {formComp?.kind === "action" ? "Behavior" : "Component"}
              <select value={form.component} onChange={(e) => setForm({ ...form, component: e.target.value, values: {}, requirements: {}, parts: {} })}>
                {KINDS.map(([kind, label]) =>
                  concrete.some((c) => c.kind === kind) ? (
                    <optgroup key={kind} label={label}>
                      {concrete
                        .filter((c) => c.kind === kind)
                        .map((c) => (
                          <option key={c.id} value={c.qualifiedName}>
                            {c.name}
                          </option>
                        ))}
                    </optgroup>
                  ) : null
                )}
              </select>
            </label>
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={form.name && !NAME_RE.test(form.name) ? "invalid" : ""} />
            </label>
            <label>
              Package
              <select value={form.replace ? form.package : form.package || formPkg} disabled={!!form.replace} onChange={(e) => setForm({ ...form, package: e.target.value })}>
                {catalog.packages.map((p) => (
                  <option key={p.qualifiedName} value={p.qualifiedName}>
                    {p.qualifiedName} ({p.modelName})
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            Description
            <input value={form.doc} placeholder="1.6 l four-cylinder" onChange={(e) => setForm({ ...form, doc: e.target.value })} />
          </label>
          {formComp?.kind === "action" && <div className="hint">Set every parameter: a system instance can only perform a behavior instance whose parameters all have values.</div>}
          <div className="params">
            {formComp &&
              fields(formComp).map((a) => {
                const unset = formComp.kind === "action" && !a.value && !form.values[a.name]?.trim();
                return (
                  <label key={a.name}>
                    <span>
                      {a.name} <span className="mono">: {a.type || "?"}</span>
                      {unset && <span className="error-text small"> required</span>}
                    </span>
                    <input className={`mono${unset ? " invalid" : ""}`} value={form.values[a.name] || ""} placeholder={a.value ? `= ${a.value} (default)` : placeholder(a.type)} onChange={(e) => setForm({ ...form, values: { ...form.values, [a.name]: e.target.value } })} />
                  </label>
                );
              })}
          </div>
          {formComp && subParts(formComp).length > 0 && (
            <div className="params">
              {subParts(formComp).map((p) => (
                <label key={p.name}>
                  <span>
                    {p.name} <span className="mono">: {p.type}</span>
                  </span>
                  <select value={form.parts[p.name] || ""} onChange={(e) => setForm({ ...form, parts: { ...form.parts, [p.name]: e.target.value || null } })}>
                    <option value="">— none —</option>
                    {instances
                      .filter((i) => p.candidates.includes(i.id))
                      .map((i) => (
                        <option key={i.id} value={i.qualifiedName}>
                          {i.name}
                        </option>
                      ))}
                  </select>
                </label>
              ))}
            </div>
          )}
          {formComp && <RequirementValues usages={formComp.requirementUsages} values={form.requirements} onChange={(requirements) => setForm({ ...form, requirements })} />}
          <code className="block">{instancePreview}</code>
          <div className="row-end">
            <span className="hint">Values are SysML expressions; units go in brackets, e.g. 140 [SI::kg].</span>
            <button onClick={() => setForm(null)}>Cancel</button>
            <button
              className="primary"
              disabled={busy || !NAME_RE.test(form.name) || reqMissing.length > 0}
              title={reqMissing.length ? `State the requirement values: ${reqMissing.join(", ")}` : undefined}
              onClick={saveInstance}
            >
              {busy ? "Saving…" : form.replace ? "Save instance" : "Add to catalog"}
            </button>
          </div>
        </div>
      )}

      {compForm && (
        <div className="form-card">
          <div className="strong">New {compForm.kind === "action" ? "behavior" : "component"}</div>
          <div className="grid3">
            <label>
              Name
              <input value={compForm.name} placeholder={compForm.kind === "action" ? "Survey" : "Radiator"} onChange={(e) => setCompForm({ ...compForm, name: e.target.value })} className={compForm.name && !NAME_RE.test(compForm.name) ? "invalid" : ""} />
            </label>
            <label>
              Specializes
              <select value={compForm.specializes} onChange={(e) => setCompForm({ ...compForm, specializes: e.target.value })}>
                <option value="">—</option>
                {components.filter((c) => c.kind === compForm.kind).map((c) => (
                  <option key={c.id} value={c.qualifiedName}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Package
              <select value={compForm.package} onChange={(e) => setCompForm({ ...compForm, package: e.target.value })}>
                {catalog.packages.map((p) => (
                  <option key={p.qualifiedName} value={p.qualifiedName}>
                    {p.qualifiedName} ({p.modelName})
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid2">
            <label>
              Description
              <input value={compForm.doc} onChange={(e) => setCompForm({ ...compForm, doc: e.target.value })} />
            </label>
            <label className="inline">
              <input type="checkbox" checked={compForm.abstract} onChange={(e) => setCompForm({ ...compForm, abstract: e.target.checked })} /> abstract (only its specializations get instances)
            </label>
          </div>
          <div className="grid2">
            {compForm.kind === "action" && <FeatureRows label="Parameters" typeHint="Real" rows={compForm.params} onChange={(params) => setCompForm({ ...compForm, params })} />}
            <FeatureRows label={compForm.kind === "action" ? "Quantities (set by mission steps)" : "Attributes"} typeHint={compForm.kind === "action" ? "Real" : "MassValue"} rows={compForm.attributes} onChange={(attributes) => setCompForm({ ...compForm, attributes })} />
            {compForm.kind === "part" && <FeatureRows label="Ports" typeHint="~FuelPort" rows={compForm.ports} onChange={(ports) => setCompForm({ ...compForm, ports })} />}
          </div>
          <RequirementRows templates={templates} rows={compForm.requirements} onChange={(requirements) => setCompForm({ ...compForm, requirements })} />
          <code className="block">{compPreview}</code>
          <div className="row-end">
            <button onClick={() => setCompForm(null)}>Cancel</button>
            <button className="primary" disabled={busy || !NAME_RE.test(compForm.name) || !compForm.package} onClick={saveComponent}>
              {busy ? "Saving…" : compForm.kind === "action" ? "Add behavior" : "Add component"}
            </button>
          </div>
        </div>
      )}
      {catalog.diagnostics.length > 0 && (
        <div className="error-box">
          The project has {catalog.diagnostics.length} error{catalog.diagnostics.length > 1 ? "s" : ""}; the catalog may be incomplete. {catalog.diagnostics[0].file}:{catalog.diagnostics[0].line} {catalog.diagnostics[0].message}
        </div>
      )}
      {!components.length ? (
        <div className="empty-card">
          <div className="strong">No components in this project yet</div>
          <p>
            A component is a <code>part def</code> tagged with the <code>#Component</code> metadata. Its instances — part usages that give its attributes values — make up the catalog that system architectures are configured from.
          </p>
          <code className="block">{`metadata def Component;\n\n#Component part def Engine {\n    attribute mass : MassValue;\n    port torqueOut : TorquePort;\n}\n\npackage Catalog {\n    part i4Engine : Engine { attribute :>> mass = 140 [SI::kg]; }\n}`}</code>
          <p className="muted small">Create a model from the “Component library” template (Model ▸ +), or open the “Delivery Drones” demo project.</p>
        </div>
      ) : (
        <div className="lib-split">
          <div className="lib-list">
            {KINDS.map(([kind, label]) => {
              const rows = visible.filter(({ c }) => c.kind === kind);
              if (!rows.length) return null;
              return (
                <div key={kind}>
                  {hasBehaviors && <div className="lib-group">{label}</div>}
                  {rows.map(({ c, depth }) => (
                    <div key={c.id} className={`lib-item${comp?.id === c.id ? " on" : ""}`} style={{ paddingLeft: 8 + depth * 14 }} onClick={() => setSelected(c.qualifiedName)}>
                      <span className={c.isAbstract ? "em" : ""}>{depth > 0 && <span className="muted">↳ </span>}{c.name}</span>
                      <span className="count">{instances.filter((i) => i.component === c.qualifiedName).length || ""}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          {comp && (
            <div className="lib-detail">
              <div className="detail-head">
                <div>
                  <div className="muted small">
                    {comp.isAbstract ? "abstract " : ""}
                    {comp.kind === "action" ? "behavior" : "component"} · {comp.modelName} · {comp.package}
                  </div>
                  <div className="title">
                    {comp.name}
                    {comp.specializes.length > 0 && <span className="muted"> :&gt; {comp.specializes.map(short).join(", ")}</span>}
                  </div>
                  {comp.doc && <div className="small">{comp.doc}</div>}
                </div>
                <button className="link" onClick={() => onOpen(comp)}>
                  Show in editor
                </button>
              </div>
              {comp.kind === "action" ? (
                <BehaviorFeatures comp={comp} />
              ) : (
              <div className="lib-cols">
                <div>
                  <div className="section-title">Attributes</div>
                  <table className="table compact">
                    <tbody>
                      {comp.attributes.map((a) => (
                        <tr key={a.name}>
                          <td className="mono">{a.name}</td>
                          <td className="mono muted">{a.type || "—"}</td>
                          <td className="small muted">
                            {a.value ? <code>= {a.value}</code> : a.declaredIn !== comp.qualifiedName ? `from ${short(a.declaredIn)}` : ""}
                          </td>
                        </tr>
                      ))}
                      {!comp.attributes.length && (
                        <tr>
                          <td className="muted">none</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                {comp.kind === "part" && <div>
                  <div className="section-title">Ports</div>
                  <div className="chips">
                    {comp.ports.map((p) => (
                      <span key={p.name} className="port-chip mono" title={p.declaredIn !== comp.qualifiedName ? `from ${short(p.declaredIn)}` : undefined}>
                        {p.name} : {p.type}
                      </span>
                    ))}
                    {!comp.ports.length && <span className="muted">none</span>}
                  </div>
                  {comp.parts.length > 0 && (
                    <>
                      <div className="section-title">Parts</div>
                      <div className="chips">
                        {comp.parts.map((p) => (
                          <span key={p.name} className="port-chip mono">
                            {p.name} : {p.type}
                            {p.multiplicity || ""}
                          </span>
                        ))}
                      </div>
                    </>
                  )}
                </div>}
              </div>
              )}

              <UsageList comp={comp} />
              {comp.analyses.length > 0 && shown.length > 0 && (
                <ComponentAnalysisCard projectId={projectId} catalog={catalog} comp={comp} instances={shown} busy={busy} onChange={(fn) => run(fn)} onOpen={onOpen} />
              )}

              <div className="section-title row-between">
                <span>
                  Instances {comp.isAbstract && comp.kind === "part" && <span className="muted small">(of its specializations)</span>}
                  {comp.kind === "action" && <span className="muted small"> (performance profiles systems can perform)</span>}
                </span>
                <button disabled={busy || !concrete.some((x) => family(comp).has(x.qualifiedName))} onClick={() => newInstance(comp)}>
                  + New {comp.isAbstract && comp.kind === "part" ? "" : comp.name} instance
                </button>
              </div>
              <InstanceTable comp={comp} rows={shown} selected={form?.replace || null} busy={busy} onEdit={editInstance} onDelete={deleteInstance} onOpen={onOpen} />
            </div>
          )}
        </div>
      )}

    </div>
  );
}

const TAG = { part: "Component", action: "Behavior" } as const;

/** The requirement usages a component or behavior includes: what each binds, and what every instance states. */
function UsageList({ comp }: { comp: Component }) {
  const rows = walkUsages(comp.requirementUsages);
  if (!rows.length) return null;
  return (
    <div>
      <div className="section-title">Requirements</div>
      <table className="table compact">
        <tbody>
          {rows.map((u) => {
            const open = openThresholds(u);
            return (
              <tr key={u.path}>
                <td className="mono" style={{ paddingLeft: 6 + (u.path.split(".").length - 1) * 14 }}>
                  <ReqId id={reqId(u)} />
                  {u.name} <span className="muted">: {u.defName}</span>
                </td>
                <td className="small">{fillDoc(u.doc, u.values)}</td>
                <td className="small">
                  {u.bound.map((b) => (
                    <code key={b} className="nowrap">
                      {b} = {u.values[b]}{" "}
                    </code>
                  ))}
                  {open.length > 0 && <span className="muted">each instance sets {open.map((t) => t.name).join(", ")}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Requirement usages of a new component: a library requirement def, and the attributes the component binds. */
function RequirementRows({ templates, rows, onChange }: { templates: CatalogRequirement[]; rows: RequirementRow[]; onChange(rows: RequirementRow[]): void }) {
  const set = (i: number, r: Partial<RequirementRow>) => onChange(rows.map((x, j) => (j === i ? { ...x, ...r } : x)));
  if (!templates.length) return null;
  return (
    <div>
      <div className="section-title row-between">
        <span>Requirements <span className="muted small">(from the requirement library; each instance states the numbers left open)</span></span>
        <button onClick={() => onChange([...rows, { name: "", type: templates[0].qualifiedName, values: {} }])}>+ Requirement</button>
      </div>
      {rows.map((r, i) => {
        const def = templates.find((t) => t.qualifiedName === r.type);
        return (
          <div key={i} className="req-row">
            <input className="mono" placeholder="name" value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
            <select value={r.type} onChange={(e) => set(i, { type: e.target.value, values: {} })}>
              {templates.map((t) => (
                <option key={t.qualifiedName} value={t.qualifiedName}>
                  {t.shortId ? `${t.shortId} ${t.name}` : t.name}
                </option>
              ))}
            </select>
            {def?.thresholds
              .filter((t) => !t.value)
              .map((t) => (
                <input key={t.name} className="mono" placeholder={`${t.name} = (each instance)`} title={`Bind ${t.name} to an expression of this component, or leave it for each instance to state`} value={r.values[t.name] || ""} onChange={(e) => set(i, { values: { ...r.values, [t.name]: e.target.value } })} />
              ))}
            <button className="link" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
              ✕
            </button>
            {def?.doc && <div className="muted small req-row-doc">{def.doc}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** parts of a component that a catalog instance fills (a material) */
const subParts = (c: Component) => c.parts.filter((p) => p.candidates.length > 0);

/** a value as the instance table shows it: a long sequence as its count */
function shortValue(v: string | undefined): string {
  if (v === undefined) return "—";
  const m = /^\((.*)\)$/.exec(v.trim());
  return m && v.length > 24 ? `(${m[1].split(",").length} values)` : v;
}

/** what an instance form sets: a behavior's parameters, a component's attributes */
const fields = (c: Component) => (c.kind === "action" ? c.params : c.attributes);

/** a behavior's parameters (set by the performing system), quantities (set by mission steps) and derived attributes */
function BehaviorFeatures({ comp }: { comp: Component }) {
  const groups: [string, string, CatalogAttribute[]][] = [
    ["Parameters", "set by the system instance that performs it", comp.params],
    ["Quantities", "set by each mission step", comp.attributes.filter((a) => !a.value)],
    ["Derived", "", comp.attributes.filter((a) => a.value)],
  ];
  return (
    <div className="lib-cols three">
      {groups.map(([title, hint, rows]) => (
        <div key={title}>
          <div className="section-title" title={hint}>
            {title} {hint && <span className="muted small">· {hint}</span>}
          </div>
          <table className="table compact">
            <tbody>
              {rows.map((a) => (
                <tr key={a.name}>
                  <td className="mono">{a.name}</td>
                  <td className="mono muted">{a.type || "—"}</td>
                  <td className="small muted">{a.value ? <code>= {a.value}</code> : a.declaredIn !== comp.qualifiedName ? `from ${short(a.declaredIn)}` : ""}</td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td className="muted">none</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
const KINDS = [
  ["part", "Components"],
  ["action", "Behaviors"],
] as const;

function InstanceTable({
  comp,
  rows,
  selected,
  busy,
  onEdit,
  onDelete,
  onOpen,
}: {
  comp: Component;
  rows: ComponentInstance[];
  selected: string | null;
  busy: boolean;
  onEdit(i: ComponentInstance): void;
  onDelete(i: ComponentInstance): void;
  onOpen(i: ComponentInstance): void;
}) {
  const cols = useMemo(() => {
    const names = fields(comp).map((a) => a.name);
    if (comp.kind === "action") return names; // its quantities and derived attributes belong to mission steps
    for (const r of rows) for (const k of Object.keys(r.values)) if (!names.includes(k)) names.push(k);
    return names;
  }, [comp, rows]);
  const mixed = rows.some((r) => r.component !== comp.qualifiedName);
  const hasReqs = rows.some((r) => r.requirementUsages.length > 0);
  const parts = subParts(comp);
  if (!rows.length) return <div className="muted small">No instances yet.</div>;
  return (
    <div className="table-scroll">
      <table className="table compact">
        <thead>
          <tr>
            <th>Instance</th>
            {mixed && <th>Type</th>}
            {cols.map((c) => (
              <th key={c} className="mono">
                {c}
              </th>
            ))}
            {parts.map((p) => (
              <th key={p.name} className="mono">
                {p.name}
              </th>
            ))}
            {hasReqs && <th title="The numbers each instance states in its requirements">Requirements</th>}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={selected === r.qualifiedName ? "row-on" : ""}>
              <td>
                <div className="strong mono">{r.name}</div>
                {r.doc && <div className="muted small">{r.doc}</div>}
                {r.missing.length > 0 && <span className="pill warn" title="A system instance cannot perform it until every parameter is set">incomplete: {r.missing.join(", ")}</span>}
              </td>
              {mixed && <td className="small">{short(r.component)}</td>}
              {cols.map((c) => (
                <td key={c} className={`mono small${r.ownValues[c] ? "" : " muted"}`} title={r.values[c]}>
                  {shortValue(r.values[c])}
                </td>
              ))}
              {parts.map((p) => (
                <td key={p.name} className="mono small">
                  {short(r.parts?.[p.name]) || "—"}
                </td>
              ))}
              {hasReqs && (
                <td className="mono small">
                  {Object.entries(ownValues(r.requirementUsages)).map(([k, v]) => (
                    <div key={k} className="nowrap">
                      {k} = {v}
                    </div>
                  ))}
                  {r.missingRequirementValues.length > 0 && <span className="pill warn">unset: {r.missingRequirementValues.join(", ")}</span>}
                </td>
              )}
              <td className="actions">
                <button className="link" onClick={() => onOpen(r)}>
                  Source
                </button>
                <button className="link" disabled={busy} onClick={() => onEdit(r)}>
                  Edit
                </button>
                <button className="link danger" disabled={busy} onClick={() => onDelete(r)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeatureRows({ label, typeHint, rows, onChange }: { label: string; typeHint: string; rows: { name: string; type: string }[]; onChange(rows: { name: string; type: string }[]): void }) {
  const set = (i: number, k: "name" | "type", v: string) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="feature-rows">
      <div className="small muted">{label}</div>
      {rows.map((r, i) => (
        <div key={i} className="feature-row">
          <input className="mono" placeholder="name" value={r.name} onChange={(e) => set(i, "name", e.target.value)} />
          <input className="mono" placeholder={typeHint} value={r.type} onChange={(e) => set(i, "type", e.target.value)} />
          <button className="icon" title="Remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button className="link" onClick={() => onChange([...rows, { name: "", type: "" }])}>
        + {label.toLowerCase().replace(/s$/, "")}
      </button>
    </div>
  );
}
