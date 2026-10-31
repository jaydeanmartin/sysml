import { useEffect, useState } from "react";
import { api } from "../api";
import { analysisBindingSnippet } from "../snippets";
import type { Analysis, Invocation, Variable } from "../types";

interface Props {
  onInsert(text: string): void;
}

const emptyForm = {
  name: "",
  kind: "external" as "external" | "formula",
  description: "",
  endpointUrl: "https://",
  inputs: "mass:kg, power:W",
  outputs: "result",
  formulas: "result = power / mass",
};

function parseVars(s: string): Variable[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => {
      const [name, unit] = x.split(":").map((y) => y.trim());
      return unit ? { name, unit } : { name };
    });
}

export function AnalysesPanel({ onInsert }: Props) {
  const [list, setList] = useState<Analysis[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [showForm, setShowForm] = useState(false);
  const [testIn, setTestIn] = useState<Record<string, string>>({});
  const [testOut, setTestOut] = useState<string | null>(null);
  const [invocations, setInvocations] = useState<Invocation[]>([]);

  const reload = () => {
    api.analyses().then(setList, (e) => setError(e.message));
    api.invocations(15).then(setInvocations, () => undefined);
  };
  useEffect(reload, []);

  const register = async () => {
    setError(null);
    try {
      const outputs = parseVars(form.outputs);
      const formulas: Record<string, string> = {};
      if (form.kind === "formula") {
        for (const line of form.formulas.split("\n")) {
          const i = line.indexOf("=");
          if (i > 0) formulas[line.slice(0, i).trim()] = line.slice(i + 1).trim();
        }
      }
      await api.createAnalysis({
        name: form.name,
        kind: form.kind,
        description: form.description,
        endpointUrl: form.kind === "external" ? form.endpointUrl : null,
        inputs: parseVars(form.inputs),
        outputs,
        formulas,
      });
      setShowForm(false);
      setForm(emptyForm);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const test = async (a: Analysis) => {
    setTestOut(null);
    try {
      const inputs: Record<string, unknown> = {};
      for (const v of a.inputs) {
        const raw = testIn[`${a.name}.${v.name}`] ?? "";
        const num = Number(raw);
        inputs[v.name] = v.unit ? { value: num, unit: v.unit } : raw !== "" && !isNaN(num) ? num : raw;
      }
      const r = await api.invoke(a.name, inputs);
      setTestOut(JSON.stringify(r.outputs, null, 2));
      api.invocations(15).then(setInvocations, () => undefined);
    } catch (e) {
      setTestOut(`Error: ${(e as Error).message}`);
    }
  };

  return (
    <div className="panel">
      <div className="panel-bar">
        <button className="primary" onClick={() => setShowForm((s) => !s)}>
          + Register analysis
        </button>
        <span className="hint">
          Bind one in a model with <code>metadata ToolExecution {"{"} toolName = "…"; {"}"}</code>: the interpreter calls it over REST during execution.
        </span>
      </div>
      {error && <div className="error-box">{error}</div>}
      {showForm && (
        <div className="form-card">
          <div className="grid2">
            <label>
              Name (toolName)
              <input value={form.name} placeholder="stressCheck" onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>
              Kind
              <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as "external" | "formula" })}>
                <option value="external">External REST endpoint</option>
                <option value="formula">Formula (hosted)</option>
              </select>
            </label>
          </div>
          <label>
            Description
            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          {form.kind === "external" ? (
            <label>
              Endpoint URL (receives POST {"{toolName, uri, inputs, runId}"}, returns {"{outputs}"})
              <input value={form.endpointUrl} onChange={(e) => setForm({ ...form, endpointUrl: e.target.value })} />
            </label>
          ) : (
            <label>
              Formulas (one per output: <code>name = expression</code>)
              <textarea rows={3} className="mono" value={form.formulas} onChange={(e) => setForm({ ...form, formulas: e.target.value })} />
            </label>
          )}
          <div className="grid2">
            <label>
              Inputs (<code>name:unit</code>, comma separated)
              <input className="mono" value={form.inputs} onChange={(e) => setForm({ ...form, inputs: e.target.value })} />
            </label>
            <label>
              Outputs
              <input className="mono" value={form.outputs} onChange={(e) => setForm({ ...form, outputs: e.target.value })} />
            </label>
          </div>
          <div className="row-end">
            <button onClick={() => setShowForm(false)}>Cancel</button>
            <button className="primary" disabled={!form.name} onClick={register}>
              Register
            </button>
          </div>
        </div>
      )}
      <div className="cards">
        {list.map((a) => (
          <div key={a.name} className={`card${a.enabled ? "" : " disabled"}`}>
            <div className="card-head" onClick={() => setOpen(open === a.name ? null : a.name)}>
              <span className={`kind kind-${a.kind}`}>{a.kind}</span>
              <span className="mono strong">{a.name}</span>
              <span className="muted small grow">{a.description}</span>
              <span className="small mono">
                ({a.inputs.map((v) => v.name).join(", ")}) → {a.outputs.map((v) => v.name).join(", ")}
              </span>
            </div>
            {open === a.name && (
              <div className="card-body">
                {a.endpointUrl && (
                  <div className="small">
                    Endpoint: <code>{a.endpointUrl}</code>
                  </div>
                )}
                {Object.keys(a.formulas || {}).length > 0 && (
                  <div className="small">
                    {Object.entries(a.formulas).map(([k, v]) => (
                      <div key={k}>
                        <code>
                          {k} = {v}
                        </code>
                      </div>
                    ))}
                  </div>
                )}
                <div className="params">
                  {a.inputs.map((v) => (
                    <label key={v.name}>
                      {v.name}
                      {v.unit && <span className="muted small"> [{v.unit}]</span>}
                      <input value={testIn[`${a.name}.${v.name}`] ?? ""} onChange={(e) => setTestIn({ ...testIn, [`${a.name}.${v.name}`]: e.target.value })} />
                    </label>
                  ))}
                </div>
                <div className="row-end">
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={a.enabled}
                      onChange={async (e) => {
                        await api.toggleAnalysis(a.name, e.target.checked);
                        reload();
                      }}
                    />{" "}
                    enabled
                  </label>
                  {a.kind !== "builtin" && (
                    <button
                      className="danger"
                      onClick={async () => {
                        if (confirm(`Delete analysis '${a.name}'?`)) {
                          await api.deleteAnalysis(a.name);
                          reload();
                        }
                      }}
                    >
                      Delete
                    </button>
                  )}
                  <button onClick={() => onInsert(analysisBindingSnippet(a))}>Insert SysML binding</button>
                  <button className="primary" onClick={() => test(a)}>
                    Test call
                  </button>
                </div>
                {testOut && <pre className="console">{testOut}</pre>}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="strong" style={{ marginTop: 16 }}>
        Recent REST calls
      </div>
      <table className="table compact">
        <tbody>
          {invocations.map((i) => (
            <tr key={i.id}>
              <td>
                <span className={`dot ${i.status === "ok" ? "ok" : "bad"}`} />
              </td>
              <td className="mono">{i.analysis}</td>
              <td className="small muted">{i.runId ? `run ${i.runId.slice(0, 8)}` : "direct"}</td>
              <td className="small">{i.error || Object.entries(i.outputs || {}).map(([k, v]) => `${k}=${String(v.value)}${v.unit ? " " + v.unit : ""}`).join(", ")}</td>
              <td className="small muted">{new Date(i.createdAt).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
