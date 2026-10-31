import { useEffect, useMemo, useRef, useState } from "react";
import type { GNode, Graph, Run, RunKind, RunRequest, TraceEvent } from "../types";

interface Target {
  key: string;
  kind: RunKind;
  label: string;
  node?: GNode;
  params: GNode[];
}

interface Props {
  graph: Graph;
  busy: boolean;
  lastRun: Run | null;
  onRun(req: RunRequest): Promise<Run | null>;
  onActive(nodeIds: string[], focusNodeId: string | null): void;
}

const KIND_LABEL: Record<string, string> = {
  calc: "Calculation",
  analysis: "Analysis case",
  requirement: "Requirement",
  constraint: "Constraint",
  satisfy: "Satisfy relations",
  action: "Action",
  state: "State machine",
  evaluate: "Expression",
};

export function targetsOf(graph: Graph): Target[] {
  const byOwner = new Map<string, GNode[]>();
  for (const n of graph.nodes) if (n.owner) byOwner.set(n.owner, [...(byOwner.get(n.owner) || []), n]);
  const top = (n: GNode) => !n.owner || graph.nodes.find((o) => o.id === n.owner)?.kind === "package";
  const t: Target[] = [{ key: "satisfy", kind: "satisfy", label: "All satisfy relations", params: [] }];
  for (const n of graph.nodes) {
    const qn = n.qualifiedName || n.name;
    const params = (byOwner.get(n.id) || []).filter((p) => p.kind === "param" && p.direction !== "out");
    if (n.kind === "calc def") t.push({ key: `calc:${qn}`, kind: "calc", label: qn, node: n, params });
    else if ((n.kind === "analysis" || n.kind === "verification") && top(n)) t.push({ key: `analysis:${qn}`, kind: "analysis", label: qn, node: n, params: [] });
    else if (n.kind === "requirement" && top(n)) t.push({ key: `requirement:${qn}`, kind: "requirement", label: qn, node: n, params: [] });
    else if (n.kind === "constraint" && top(n)) t.push({ key: `constraint:${qn}`, kind: "constraint", label: qn, node: n, params: [] });
    else if ((n.kind === "action def" || (n.kind === "action" && top(n)))) t.push({ key: `action:${qn}`, kind: "action", label: qn, node: n, params: [] });
    else if (n.kind === "state def" || (n.kind === "state" && top(n))) t.push({ key: `state:${qn}`, kind: "state", label: qn, node: n, params: [] });
  }
  t.push({ key: "evaluate", kind: "evaluate", label: "Evaluate expressions", params: [] });
  return t;
}

/** Frames for trace playback: the behavior steps that were active, in order. */
function frames(trace: TraceEvent[]): string[] {
  const out: string[] = [];
  for (const e of trace) {
    if (e.type === "step" && e.node) out.push(e.node);
    else if (e.type === "enter" && e.node) out.push(e.node);
  }
  return out;
}

export function ExecutePanel({ graph, busy, lastRun, onRun, onActive }: Props) {
  const targets = useMemo(() => targetsOf(graph), [graph]);
  const [key, setKey] = useState("satisfy");
  const [args, setArgs] = useState<Record<string, string>>({});
  const [expr, setExpr] = useState("");
  const [advance, setAdvance] = useState("30");
  const [frame, setFrame] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);

  const target = targets.find((t) => t.key === key) || targets[0];
  const result = lastRun?.result;
  const fr = useMemo(() => frames(result?.trace || []), [result]);

  useEffect(() => {
    if (!expr) {
      const inst = graph.nodes.find((n) => n.kind === "part" && !graph.nodes.find((o) => o.id === n.owner && o.kind !== "package"));
      const attr = inst && graph.nodes.find((n) => n.owner === inst.id && n.kind === "attribute");
      if (inst && attr) setExpr(`${inst.qualifiedName}.${attr.name}`);
    }
  }, [graph, expr]);

  // map a trace step name to graph node ids under the executed behavior
  const resolve = (name: string): string[] => {
    const rootQn = lastRun?.target || "";
    const root = graph.nodes.find((n) => n.qualifiedName === rootQn);
    const under = (n: GNode): boolean => {
      let cur: GNode | undefined = n;
      for (let i = 0; i < 20 && cur; i++) {
        if (!root || cur.owner === root.id || cur.owner === root.typeId) return true;
        cur = graph.nodes.find((o) => o.id === cur!.owner);
      }
      return false;
    };
    return graph.nodes.filter((n) => n.name === name && under(n)).map((n) => n.id);
  };

  useEffect(() => {
    if (frame < 0 || frame >= fr.length) {
      onActive([], null);
      return;
    }
    const ids = resolve(fr[frame]);
    onActive(ids, ids[0] || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, fr]);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setFrame((f) => {
        if (f + 1 >= fr.length) {
          setPlaying(false);
          return f;
        }
        return f + 1;
      });
    }, 700);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, fr.length]);

  // a new run: auto-play its behavior trace, otherwise clear the highlight
  useEffect(() => {
    const animate = (lastRun?.kind === "action" || lastRun?.kind === "state") && fr.length > 0;
    setFrame(animate ? 0 : -1);
    setPlaying(animate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastRun?.id]);

  const run = async () => {
    const req: RunRequest = { kind: target.kind, trace: true };
    if (target.kind === "evaluate") req.expression = expr;
    else if (target.kind !== "satisfy") req.target = target.node?.qualifiedName || target.label;
    if (target.kind === "calc") req.args = target.params.map((p) => args[p.name] ?? "0");
    if (target.kind === "action" || target.kind === "state") req.advance = Number(advance) || 0;
    await onRun(req);
  };

  return (
    <div className="panel">
      <div className="form-card">
        <div className="grid2">
          <label>
            What to execute
            <select value={target.key} onChange={(e) => setKey(e.target.value)}>
              {Object.entries(KIND_LABEL).map(([kind, label]) => {
                const opts = targets.filter((t) => t.kind === kind);
                return opts.length ? (
                  <optgroup key={kind} label={label}>
                    {opts.map((t) => (
                      <option key={t.key} value={t.key}>
                        {t.label}
                        {t.node?.tool ? "  ⚡REST" : ""}
                      </option>
                    ))}
                  </optgroup>
                ) : null;
              })}
            </select>
          </label>
          {(target.kind === "action" || target.kind === "state") && (
            <label>
              Simulated time to advance (s)
              <input value={advance} onChange={(e) => setAdvance(e.target.value)} />
            </label>
          )}
        </div>
        {target.kind === "calc" && target.params.length > 0 && (
          <div className="params">
            {target.params.map((p) => (
              <label key={p.id}>
                {p.name}
                <span className="muted small"> : {p.typeName || "Real"}</span>
                <input
                  value={args[p.name] ?? ""}
                  placeholder={/Value$/.test(p.typeName || "") ? "e.g. 100 [SI::kg]" : "e.g. 3.5"}
                  onChange={(e) => setArgs({ ...args, [p.name]: e.target.value })}
                />
              </label>
            ))}
          </div>
        )}
        {target.kind === "evaluate" && (
          <label>
            Expressions (one per line)
            <textarea rows={3} className="mono" value={expr} onChange={(e) => setExpr(e.target.value)} />
          </label>
        )}
        {target.node?.tool && (
          <div className="hint">
            ⚡ Performed by the REST analysis <b>{target.node.tool.toolName}</b> during execution.
          </div>
        )}
        <div className="row-end">
          <span className="hint">Runs the saved model (unsaved edits are saved first).</span>
          <button className="primary" disabled={busy} onClick={run}>
            {busy ? "Running…" : "▶ Run"}
          </button>
        </div>
      </div>

      {lastRun && result && (
        <div className="result">
          <div className="result-head">
            <span className={`pill ${result.status === "holds" ? "ok" : "bad"}`}>{result.status}</span>
            <span className="strong">{KIND_LABEL[lastRun.kind] || lastRun.kind}</span>
            <span className="mono small">{lastRun.target || ""}</span>
            <span className="muted small">
              v{lastRun.version} · {result.elapsedMs} ms · run {lastRun.id.slice(0, 8)}
            </span>
          </div>
          {result.errors.length > 0 && <div className="error-box">{result.errors.join("\n")}</div>}
          {result.checks.map((c, i) => (
            <div key={i} className="check">
              <div className="check-head">
                <span className={`dot ${c.status === "holds" ? "ok" : "bad"}`} />
                <span className="mono">{c.subject}</span>
                {c.standing && <span className="muted small">{c.standing}</span>}
              </div>
              {c.values.length > 0 && (
                <table className="table compact">
                  <tbody>
                    {c.values.map((v, j) => (
                      <tr key={j}>
                        <td className="mono">{v.name}</td>
                        <td className="mono strong">{v.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
          {fr.length > 0 && (
            <div className="trace">
              <div className="trace-bar">
                <span className="strong">Execution trace</span>
                <button onClick={() => setFrame((f) => Math.max(0, f - 1))}>◀</button>
                <button onClick={() => (playing ? setPlaying(false) : (setFrame((f) => (f >= fr.length - 1 ? 0 : Math.max(f, 0))), setPlaying(true)))}>
                  {playing ? "❚❚" : "▶"}
                </button>
                <button onClick={() => setFrame((f) => Math.min(fr.length - 1, f + 1))}>▶|</button>
                <span className="muted small">
                  step {Math.max(frame + 1, 0)} / {fr.length}
                </span>
              </div>
              <div className="trace-steps">
                {fr.map((name, i) => (
                  <span key={i} className={`chip${i === frame ? " on" : ""}`} onClick={() => setFrame(i)}>
                    {name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {(lastRun.invocations?.length ?? 0) > 0 && (
            <div className="invocations">
              <div className="strong">REST analysis calls made during this run</div>
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Analysis</th>
                    <th>Inputs</th>
                    <th>Outputs</th>
                    <th>ms</th>
                  </tr>
                </thead>
                <tbody>
                  {lastRun.invocations!.map((inv) => (
                    <tr key={inv.id}>
                      <td className="mono">
                        <span className={`dot ${inv.status === "ok" ? "ok" : "bad"}`} /> {inv.analysis}
                      </td>
                      <td className="mono small">{fmtVals(inv.inputs)}</td>
                      <td className="mono small">{inv.error ? <span className="bad-text">{inv.error}</span> : fmtVals(inv.outputs)}</td>
                      <td className="small">{inv.durationMs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary className="muted small">Interpreter output</summary>
            <pre className="console">{[result.command.join(" "), ...result.output, ...result.checks.flatMap((c) => c.lines)].join("\n")}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

function fmtVals(v: Record<string, unknown> | null): string {
  if (!v) return "—";
  return Object.entries(v)
    .map(([k, x]) => {
      if (x && typeof x === "object" && "value" in (x as Record<string, unknown>)) {
        const o = x as { value: unknown; unit?: string };
        return `${k}=${typeof o.value === "number" ? +o.value.toFixed(4) : o.value}${o.unit ? " " + o.unit : ""}`;
      }
      return `${k}=${x}`;
    })
    .join(", ");
}
