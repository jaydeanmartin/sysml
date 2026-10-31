import { useEffect, useState } from "react";
import { api } from "../api";
import { requirementSnippet } from "../snippets";
import type { Graph, Loc, Requirement } from "../types";

interface Props {
  modelId: string;
  refreshKey: string;
  dirty: boolean;
  graph: Graph;
  onReveal(loc: Loc, nodeId: string): void;
  onInsert(text: string): void;
  onCheckAll(): Promise<void>;
}

export function RequirementsPanel({ modelId, refreshKey, dirty, graph, onReveal, onInsert, onCheckAll }: Props) {
  const [reqs, setReqs] = useState<Requirement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [form, setForm] = useState({ name: "", id: "", text: "", subjectName: "subj", subjectType: "", constraint: "" });
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    api.requirements(modelId).then(setReqs, (e) => setError(e.message));
  }, [modelId, refreshKey]);

  const partDefs = graph.nodes.filter((n) => n.kind === "part def").map((n) => n.name);
  const add = () => {
    if (!/^[A-Za-z_]\w*$/.test(form.name)) return;
    onInsert(requirementSnippet(form));
    setForm({ ...form, name: "", id: "", text: "", constraint: "" });
    setShowForm(false);
  };

  return (
    <div className="panel">
      <div className="panel-bar">
        <button className="primary" onClick={() => setShowForm((s) => !s)}>
          + Capture requirement
        </button>
        <button
          disabled={checking}
          onClick={async () => {
            setChecking(true);
            try {
              await onCheckAll();
              setReqs(await api.requirements(modelId));
            } finally {
              setChecking(false);
            }
          }}
        >
          {checking ? "Checking…" : "▶ Check all satisfy relations"}
        </button>
        {dirty && <span className="hint">Showing the last saved version — save to refresh.</span>}
      </div>
      {showForm && (
        <div className="form-card">
          <div className="grid2">
            <label>
              Name
              <input value={form.name} placeholder="BrakingDistance" onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>
              Id (short name)
              <input value={form.id} placeholder="REQ-012" onChange={(e) => setForm({ ...form, id: e.target.value })} />
            </label>
          </div>
          <label>
            Requirement text
            <textarea rows={3} value={form.text} placeholder="The vehicle shall stop within 40 m from 100 km/h." onChange={(e) => setForm({ ...form, text: e.target.value })} />
          </label>
          <div className="grid2">
            <label>
              Subject type
              <input list="partdefs" value={form.subjectType} placeholder="Vehicle" onChange={(e) => setForm({ ...form, subjectType: e.target.value })} />
              <datalist id="partdefs">
                {partDefs.map((p) => (
                  <option key={p} value={p} />
                ))}
              </datalist>
            </label>
            <label>
              Subject name
              <input value={form.subjectName} onChange={(e) => setForm({ ...form, subjectName: e.target.value })} />
            </label>
          </div>
          <label>
            Constraint (SysML expression, optional)
            <input value={form.constraint} placeholder="subj.stoppingDistance <= 40" onChange={(e) => setForm({ ...form, constraint: e.target.value })} />
          </label>
          <div className="row-end">
            <button onClick={() => setShowForm(false)}>Cancel</button>
            <button className="primary" disabled={!/^[A-Za-z_]\w*$/.test(form.name)} onClick={add}>
              Insert into model
            </button>
          </div>
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>Requirement</th>
            <th>Text / constraint</th>
            <th>Subject</th>
            <th>Satisfied by</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {reqs.map((r) => (
            <tr key={r.id} onClick={() => r.loc && onReveal(r.loc, r.id)} className="clickable">
              <td>
                <div className="strong">{r.name}</div>
                <div className="muted small">{r.kind}{r.typeName ? ` : ${r.typeName}` : ""}</div>
              </td>
              <td>
                {r.doc && <div>{r.doc}</div>}
                {r.constraints.map((c, i) => (
                  <code key={i} className="block">
                    {c.text}
                  </code>
                ))}
              </td>
              <td className="small">
                {r.subject ? `${r.subject.name}${r.subject.type ? " : " + r.subject.type : ""}${r.subject.value ? " = " + r.subject.value : ""}` : "—"}
              </td>
              <td className="small">{r.satisfiedBy.join(", ") || "—"}</td>
              <td>
                {r.verdict ? (
                  <span className={`pill ${r.verdict.status === "holds" ? "ok" : "bad"}`}>{r.verdict.status === "holds" ? "✓ holds" : `✗ ${r.verdict.status}`}</span>
                ) : (
                  <span className="pill">not run</span>
                )}
              </td>
            </tr>
          ))}
          {!reqs.length && (
            <tr>
              <td colSpan={5} className="muted">
                No requirements in the saved model yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
