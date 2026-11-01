import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { CatalogChange, Component, ComponentInstance } from "../types";
import { NAME_RE, partText, placeholder, ref, short, type CatalogPanelProps } from "./catalogUtil";

interface InstanceForm {
  component: string;
  name: string;
  doc: string;
  package: string;
  values: Record<string, string>;
  replace: string | null;
}

interface ComponentForm {
  name: string;
  package: string;
  specializes: string;
  abstract: boolean;
  doc: string;
  attributes: { name: string; type: string }[];
  ports: { name: string; type: string }[];
}

/** Component library: component types (#Component part defs) and their instances. */
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
  const concrete = components.filter((c) => !c.isAbstract);
  const visible = tree.filter(({ c }) => !filter || c.name.toLowerCase().includes(filter.toLowerCase()) || instances.some((i) => i.component === c.qualifiedName && i.name.toLowerCase().includes(filter.toLowerCase())));

  const newInstance = (c?: Component) => {
    const target = c && !c.isAbstract ? c : concrete.find((x) => c && family(c).has(x.qualifiedName)) || concrete[0];
    if (!target) return;
    const n = instances.filter((i) => i.component === target.qualifiedName).length + 1;
    setCompForm(null);
    setForm({ component: target.qualifiedName, name: `${target.name[0].toLowerCase()}${target.name.slice(1)}${n}`, doc: "", package: "", values: {}, replace: null });
  };
  const editInstance = (i: ComponentInstance) => {
    setCompForm(null);
    setForm({ component: i.component, name: i.name, doc: i.doc || "", package: i.package || "", values: { ...i.ownValues }, replace: i.qualifiedName });
  };
  const newComponent = () => {
    setForm(null);
    const pkg = comp?.package || catalog?.packages.find((p) => catalog.tags.Component && catalog.tags.Component.startsWith(p.qualifiedName + "::"))?.qualifiedName || "";
    setCompForm({ name: "", package: pkg, specializes: "", abstract: false, doc: "", attributes: [{ name: "mass", type: "MassValue" }], ports: [] });
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
      () => api.saveInstance(projectId, { ...form, package: form.package || null, values: Object.fromEntries(Object.entries(form.values).filter(([, v]) => v.trim())) }),
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
          ports: compForm.ports.filter((p) => p.name.trim()),
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
    const cands = catalog.packages.filter((p) => p.modelId === c.modelId && short(p.qualifiedName) === "Catalog").map((p) => p.qualifiedName);
    cands.sort((a, b) => Number(!a.startsWith(root + "::")) - Number(!b.startsWith(root + "::")) || a.split("::").length - b.split("::").length);
    return cands[0] || c.package || "";
  }
  const scope = form?.replace ? instances.find((i) => i.qualifiedName === form.replace)?.package || "" : formPkg;
  const instancePreview =
    form && formComp
      ? partText(
          `part ${form.name || "…"} : ${ref(formComp.qualifiedName, scope)}`,
          formComp.attributes.filter((a) => form.values[a.name]?.trim()).map((a) => `attribute :>> ${a.name} = ${form.values[a.name].trim()};`),
          form.doc
        )
      : "";
  const compPreview = compForm
    ? partText(
        `${compForm.abstract ? "abstract " : ""}#${ref(catalog?.tags.Component || "Component", compForm.package)} part def ${compForm.name || "…"}${compForm.specializes ? ` :> ${ref(compForm.specializes, compForm.package)}` : ""}`,
        [
          ...compForm.attributes.filter((a) => a.name.trim()).map((a) => `attribute ${a.name.trim()}${a.type.trim() ? ` : ${a.type.trim()}` : ""};`),
          ...compForm.ports.filter((p) => p.name.trim()).map((p) => `port ${p.name.trim()}${p.type.trim() ? ` : ${p.type.trim()}` : ""};`),
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
        <button disabled={!catalog.tags.Component || busy} onClick={newComponent} title={catalog.tags.Component ? "Declare a new #Component part def" : "No 'metadata def Component' in this project"}>
          + New component
        </button>
        <input className="lib-filter" placeholder="Filter components and instances" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {loading && <span className="hint">Refreshing…</span>}
        {dirty && <span className="hint">Unsaved edits are saved before a change.</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      {form && (
        <div className="form-card">
          <div className="strong">{form.replace ? `Edit instance ${short(form.replace)}` : "New component instance"}</div>
          <div className="grid3">
            <label>
              Component
              <select value={form.component} onChange={(e) => setForm({ ...form, component: e.target.value, values: {} })}>
                {concrete.map((c) => (
                  <option key={c.id} value={c.qualifiedName}>
                    {c.name}
                  </option>
                ))}
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
          <div className="params">
            {formComp?.attributes.map((a) => (
              <label key={a.name}>
                <span>
                  {a.name} <span className="mono">: {a.type || "?"}</span>
                </span>
                <input className="mono" value={form.values[a.name] || ""} placeholder={a.value ? `= ${a.value} (default)` : placeholder(a.type)} onChange={(e) => setForm({ ...form, values: { ...form.values, [a.name]: e.target.value } })} />
              </label>
            ))}
          </div>
          <code className="block">{instancePreview}</code>
          <div className="row-end">
            <span className="hint">Values are SysML expressions; units go in brackets, e.g. 140 [SI::kg].</span>
            <button onClick={() => setForm(null)}>Cancel</button>
            <button className="primary" disabled={busy || !NAME_RE.test(form.name)} onClick={saveInstance}>
              {busy ? "Saving…" : form.replace ? "Save instance" : "Add to catalog"}
            </button>
          </div>
        </div>
      )}

      {compForm && (
        <div className="form-card">
          <div className="strong">New component</div>
          <div className="grid3">
            <label>
              Name
              <input value={compForm.name} placeholder="Radiator" onChange={(e) => setCompForm({ ...compForm, name: e.target.value })} className={compForm.name && !NAME_RE.test(compForm.name) ? "invalid" : ""} />
            </label>
            <label>
              Specializes
              <select value={compForm.specializes} onChange={(e) => setCompForm({ ...compForm, specializes: e.target.value })}>
                <option value="">—</option>
                {components.map((c) => (
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
            <FeatureRows label="Attributes" typeHint="MassValue" rows={compForm.attributes} onChange={(attributes) => setCompForm({ ...compForm, attributes })} />
            <FeatureRows label="Ports" typeHint="~FuelPort" rows={compForm.ports} onChange={(ports) => setCompForm({ ...compForm, ports })} />
          </div>
          <code className="block">{compPreview}</code>
          <div className="row-end">
            <button onClick={() => setCompForm(null)}>Cancel</button>
            <button className="primary" disabled={busy || !NAME_RE.test(compForm.name) || !compForm.package} onClick={saveComponent}>
              {busy ? "Saving…" : "Add component"}
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
          <p className="muted small">Create a model from the “Component library” template (Model ▸ +), or open the “Product Line” demo project.</p>
        </div>
      ) : (
        <div className="lib-split">
          <div className="lib-list">
            {visible.map(({ c, depth }) => (
              <div key={c.id} className={`lib-item${comp?.id === c.id ? " on" : ""}`} style={{ paddingLeft: 8 + depth * 14 }} onClick={() => setSelected(c.qualifiedName)}>
                <span className={c.isAbstract ? "em" : ""}>{depth > 0 && <span className="muted">↳ </span>}{c.name}</span>
                <span className="count">{instances.filter((i) => i.component === c.qualifiedName).length || ""}</span>
              </div>
            ))}
          </div>
          {comp && (
            <div className="lib-detail">
              <div className="detail-head">
                <div>
                  <div className="muted small">
                    {comp.isAbstract ? "abstract " : ""}component · {comp.modelName} · {comp.package}
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
                <div>
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
                </div>
              </div>

              <div className="section-title row-between">
                <span>
                  Instances {comp.isAbstract && <span className="muted small">(of its specializations)</span>}
                </span>
                <button disabled={busy || !concrete.some((x) => family(comp).has(x.qualifiedName))} onClick={() => newInstance(comp)}>
                  + New {comp.isAbstract ? "" : comp.name} instance
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
    const names = comp.attributes.map((a) => a.name);
    for (const r of rows) for (const k of Object.keys(r.values)) if (!names.includes(k)) names.push(k);
    return names;
  }, [comp, rows]);
  const mixed = rows.some((r) => r.component !== comp.qualifiedName);
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
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={selected === r.qualifiedName ? "row-on" : ""}>
              <td>
                <div className="strong mono">{r.name}</div>
                {r.doc && <div className="muted small">{r.doc}</div>}
              </td>
              {mixed && <td className="small">{short(r.component)}</td>}
              {cols.map((c) => (
                <td key={c} className={`mono small${r.ownValues[c] ? "" : " muted"}`}>
                  {r.values[c] ?? "—"}
                </td>
              ))}
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
