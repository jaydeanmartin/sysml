import { useEffect, useMemo, useState, type ReactElement } from "react";
import { api } from "../api";
import type { Located, RequirementNode, RequirementTree, RequirementVerdict, TestRef } from "../types";
import { errorText, short, type CatalogPanelProps } from "./catalogUtil";
import { fillDoc, NUMBER_RE, ReqId, reqId, VerdictPill } from "./RequirementValues";

type Filter = "all" | "problems" | "untested";
const SYSTEM_KEY = "studio.reqTree.system";
const PROBLEMS: (RequirementVerdict | null | undefined)[] = ["fails", "error", "missing", "cannot fly"];

const located = (n: { key: string; name: string; modelId?: string | null; loc?: Located["loc"] | null; element?: string | null }): Located => ({
  id: n.key,
  name: n.name,
  qualifiedName: n.element || n.name,
  package: null,
  modelId: n.modelId || null,
  modelName: null,
  doc: null,
  loc: n.loc || null,
});

function* requirements(n: RequirementNode): Generator<RequirementNode> {
  if (n.kind === "requirement") yield n;
  for (const c of n.children) yield* requirements(c);
}

/** Requirements of one system instance in their hierarchy: the system's own (by
 *  subject type and the architecture's requirement usages), its components',
 *  the behaviors it performs and the missions it flies, each with the numbers
 *  its instance states, the tests that verify it and, once checked, its verdict. */
export function RequirementTreePanel({ projectId, catalog, loading, error, dirty, ensureSaved, onOpen, focus }: CatalogPanelProps & { focus?: string | null }) {
  const configs = catalog?.configurations || [];
  const [system, setSystem] = useState<string | null>(() => {
    try {
      return localStorage.getItem(SYSTEM_KEY);
    } catch {
      return null;
    }
  });
  const sys = configs.find((c) => c.qualifiedName === system) || configs[0];
  const [tree, setTree] = useState<RequirementTree | null>(null);
  const [checked, setChecked] = useState<string | null>(null); // system the verdicts on screen are for
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [missions, setMissions] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selKey, setSelKey] = useState<string | null>(null);

  // a system instance selected in the diagram becomes the one shown
  useEffect(() => {
    if (focus && configs.some((c) => c.qualifiedName === focus)) setSystem(focus);
  }, [focus, configs]);
  useEffect(() => {
    if (!sys) return;
    try {
      localStorage.setItem(SYSTEM_KEY, sys.qualifiedName);
    } catch {
      /* storage unavailable */
    }
  }, [sys]);

  const check = async (qn: string) => {
    if (!(await ensureSaved())) return;
    setBusy(true);
    setErr(null);
    try {
      setTree(await api.checkRequirementTree(projectId, qn, missions));
      setChecked(qn);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  // the structure follows the catalog (re-read after every save); verdicts on screen are re-checked
  useEffect(() => {
    if (!sys) return setTree(null);
    if (checked === sys.qualifiedName) {
      check(sys.qualifiedName);
      return;
    }
    let stale = false;
    setErr(null);
    api.requirementTree(projectId, sys.qualifiedName).then(
      (t) => !stale && setTree(t),
      (e) => !stale && setErr(errorText(e))
    );
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, sys?.qualifiedName, catalog]);

  const pick = (qn: string) => {
    setSystem(qn);
    setChecked(null);
    setSelKey(null);
  };

  const root = tree?.system === sys?.qualifiedName ? tree?.tree : undefined;
  const all = useMemo(() => (root ? [...requirements(root)] : []), [root]);
  const byKey = useMemo(() => new Map(all.map((n) => [n.key, n])), [all]);
  const sel = selKey ? byKey.get(selKey) : undefined;
  const counts = useMemo(() => {
    const c = { total: all.length, holds: 0, fails: 0, missing: 0, other: 0, unchecked: 0, untested: 0 };
    for (const n of all) {
      if (!n.tests?.length) c.untested++;
      if (!n.verdict) c.unchecked++;
      else if (n.verdict === "holds") c.holds++;
      else if (n.verdict === "fails") c.fails++;
      else if (n.verdict === "missing") c.missing++;
      else c.other++;
    }
    return c;
  }, [all]);

  const q = search.trim().toLowerCase();
  const found = (n: RequirementNode) => !q || [reqId(n), n.name, n.doc].some((x) => x?.toLowerCase().includes(q));
  const matches = (n: RequirementNode): boolean =>
    (n.kind === "requirement" && found(n) && (filter === "all" || (filter === "problems" ? PROBLEMS.includes(n.verdict) : !n.tests?.length))) ||
    (n.kind !== "requirement" && filter === "all" && !q) ||
    n.children.some(matches);

  const toggle = (key: string) =>
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;
  if (!configs.length)
    return (
      <div className="panel">
        <div className="empty-card">
          <div className="strong">No system instances in this project yet</div>
          <p>
            Configure one in the Configurator. Its requirements are gathered from the requirement defs whose subject its architecture conforms to, the requirement usages of
            its architecture, its components and behaviors, and the missions it can fly.
          </p>
          <code className="block">{`abstract requirement def MinimumValue {\n    attribute actual : Real;\n    attribute minimum : Real;\n    require constraint { actual >= minimum }\n}\n#Component part def Battery {\n    attribute energy : Real;\n    requirement capacity : MinimumValue { attribute :>> actual = energy; }\n}\npart lipo : Battery {\n    attribute :>> energy = 120;\n    requirement :>> capacity { attribute :>> minimum = 100; }   // every instance states its number\n}\nverification def BenchTest {\n    subject battery : Battery;\n    objective { verify battery.capacity; }\n}`}</code>
        </div>
      </div>
    );

  const renderNode = (n: RequirementNode, depth: number): ReactElement[] => {
    if (!matches(n)) return [];
    const open = !collapsed.has(n.key);
    const kids = n.children.length > 0;
    const rows: ReactElement[] = [];
    if (n.kind === "requirement") {
      const values = Object.fromEntries((n.thresholds || []).map((t) => [t.name, t.value]));
      rows.push(
        <tr key={n.key} className={`clickable${selKey === n.key ? " row-on" : ""}`} onClick={() => setSelKey(n.key)}>
          <td style={{ paddingLeft: 8 + depth * 16 }} className="nowrap">
            <Toggle show={kids} open={open} onClick={() => toggle(n.key)} />
            <ReqId id={reqId(n)} />
            <span className="strong mono">{n.name}</span>
            {n.defName && n.defName !== n.name && <span className="muted mono small"> : {n.defName}</span>}
          </td>
          <td className="small">{fillDoc(n.doc, values) || <span className="muted">{n.constraints?.map((c) => c.text).join("; ")}</span>}</td>
          <td className="small">
            <Thresholds n={n} />
          </td>
          <td className="small">
            <Tests tests={n.tests || []} onOpen={onOpen} />
          </td>
          <td className="nowrap">
            <VerdictPill v={n.verdict} title={n.verifiedBy?.note || undefined} />
            {kids && n.rollup && n.rollup !== n.verdict && (
              <span className="muted small" title="the worst verdict of its nested requirements">
                {" "}
                ↳ {n.rollup}
              </span>
            )}
          </td>
        </tr>
      );
    } else {
      const count = [...requirements(n)].length;
      const label =
        n.kind === "system" ? (
          <>
            <span className="strong">{n.name}</span> <span className="muted mono small">: {short(n.type)}</span>
          </>
        ) : n.kind === "group" ? (
          <span className="section-title">{n.name}</span>
        ) : (
          <>
            <span className="mono">{n.name}</span>
            {n.multiplicity && <span className="muted mono">{n.multiplicity}</span>}
            <span className="muted"> ▸ </span>
            {n.element ? <span className="mono">{short(n.element)}</span> : <span className="error-text small">{n.kind === "mission" ? "" : "empty slot"}</span>}
            <span className="muted mono small"> : {short(n.type)}</span>
          </>
        );
      rows.push(
        <tr key={n.key} className={`req-tree-${n.kind}`}>
          <td colSpan={4} style={{ paddingLeft: 8 + depth * 16 }}>
            <Toggle show={kids} open={open} onClick={() => toggle(n.key)} />
            {label}
            {n.kind !== "group" && n.loc && (
              <button className="link small" onClick={() => onOpen(located(n))}>
                {" "}
                show
              </button>
            )}
            {!count && n.kind !== "system" && <span className="muted small"> — no requirements</span>}
            {n.problems && n.problems.length > 0 && <span className="muted small"> — {n.problems.join("; ")}</span>}
          </td>
          <td className="nowrap">{count > 0 && (n.rollup ? <VerdictPill v={n.rollup} /> : <span className="muted small">{count}</span>)}</td>
        </tr>
      );
    }
    if (open) for (const c of n.children) rows.push(...renderNode(c, depth + 1));
    return rows;
  };

  return (
    <div className="panel lib">
      <div className="panel-bar">
        <label className="inline">
          System{" "}
          <select value={sys?.qualifiedName} onChange={(e) => pick(e.target.value)}>
            {catalog.architectures.map((a) => {
              const cs = configs.filter((c) => c.architectureId === a.id);
              return cs.length ? (
                <optgroup key={a.id} label={a.name}>
                  {cs.map((c) => (
                    <option key={c.id} value={c.qualifiedName}>
                      {c.name}
                    </option>
                  ))}
                </optgroup>
              ) : null;
            })}
          </select>
        </label>
        <button className="primary" disabled={!sys || busy} onClick={() => sys && check(sys.qualifiedName)} title="Evaluate every requirement against the model; with missions, fly each mission and run the simulations that verify its requirements">
          {busy ? "Checking…" : "▶ Check requirements"}
        </button>
        <label className="inline small">
          <input type="checkbox" checked={missions} onChange={(e) => setMissions(e.target.checked)} /> fly missions
        </label>
        <div className="seg-toggle">
          {(["all", "problems", "untested"] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "problems" ? "Problems" : "Without a test"}
            </button>
          ))}
        </div>
        <input className="lib-filter" placeholder="Find id, name or text" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="link" onClick={() => setCollapsed(new Set())}>
          expand all
        </button>
        {dirty && <span className="hint">Unsaved edits are saved before a check.</span>}
      </div>
      {(err || error) && <div className="error-box">{err || error}</div>}
      {tree?.errors && tree.errors.length > 0 && <div className="error-box">{tree.errors.slice(0, 3).join("; ")}</div>}
      {root && (
        <div className="req-summary small">
          <b>{counts.total}</b> requirements
          {checked === sys?.qualifiedName ? (
            <>
              {" "}
              · <span className="ok-text">{counts.holds} hold</span> · <span className="error-text">{counts.fails} fail</span>
              {counts.missing > 0 && <> · {counts.missing} with values missing</>}
              {counts.other > 0 && <> · {counts.other} error / cannot fly</>}
              {counts.unchecked > 0 && <> · {counts.unchecked} not checked</>}
            </>
          ) : (
            <span className="muted"> · not checked yet</span>
          )}{" "}
          · <span className={counts.untested ? "warn-text" : ""}>{counts.untested} without a test</span>
        </div>
      )}
      <div className="req-tree-split">
        <div className="table-scroll">
          <table className="table compact req-tree">
            <thead>
              <tr>
                <th>Requirement</th>
                <th>Text</th>
                <th>Values</th>
                <th>Verified by</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>{root ? renderNode(root, 0) : <tr><td colSpan={5} className="muted">{loading || !tree ? "Reading the requirements…" : ""}</td></tr>}</tbody>
          </table>
        </div>
        {sel && <Detail n={sel} onOpen={onOpen} onClose={() => setSelKey(null)} />}
      </div>
    </div>
  );
}

function Toggle({ show, open, onClick }: { show: boolean; open: boolean; onClick(): void }) {
  if (!show) return <span className="tree-toggle" />;
  return (
    <button
      className="tree-toggle"
      aria-label={open ? "Collapse" : "Expand"}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {open ? "▾" : "▸"}
    </button>
  );
}

/** the numbers the instance states, and what the usage binds */
function Thresholds({ n }: { n: RequirementNode }) {
  const ts = (n.thresholds || []).filter((t) => t.value && (t.required ?? true));
  if (!ts.length && !n.missing?.length) return <span className="muted">—</span>;
  return (
    <span className="chips inline-chips">
      {ts.map((t) =>
        NUMBER_RE.test(t.value || "") ? (
          <span key={t.name} className="port-chip mono strong" title={t.setBy ? `stated by ${short(t.setBy)}` : undefined}>
            {t.name} = {t.value}
          </span>
        ) : (
          <code key={t.name} className="muted small" title={t.setBy ? `bound in ${short(t.setBy)}` : undefined}>
            {t.name} = {t.value}
          </code>
        )
      )}
      {n.missing?.map((m) => (
        <span key={m} className="pill warn">
          {m} unset
        </span>
      ))}
    </span>
  );
}

function Tests({ tests, onOpen }: { tests: TestRef[]; onOpen(el: Located): void }) {
  if (!tests.length) return <span className="warn-text">no test</span>;
  return (
    <span className="chips inline-chips">
      {tests.map((t) => (
        <button
          key={t.qualifiedName}
          className={`test-chip ${t.method}`}
          title={`${t.method === "simulation" ? `simulation (${t.toolName})` : "test, checked by analysis"}${t.via ? `; verifies ${t.via}, which includes this requirement` : ""}: ${t.qualifiedName}`}
          onClick={(e) => {
            e.stopPropagation();
            onOpen(located({ key: t.qualifiedName, name: t.name, modelId: t.modelId, loc: t.loc, element: t.qualifiedName }));
          }}
        >
          {t.method === "simulation" ? "⚙ " : "✓ "}
          {t.name}
          {t.via && <span className="muted"> (via {t.via})</span>}
        </button>
      ))}
    </span>
  );
}

function Detail({ n, onOpen, onClose }: { n: RequirementNode; onOpen(el: Located): void; onClose(): void }) {
  const values = Object.fromEntries((n.thresholds || []).map((t) => [t.name, t.value]));
  const vb = n.verifiedBy;
  return (
    <div className="form-card req-detail">
      <div className="row-between">
        <div>
          <div className="muted small">{n.origin}</div>
          <div className="title mono">
            <ReqId id={reqId(n)} />
            {n.name}
            {n.defName && <span className="muted"> : {n.defName}</span>}
          </div>
        </div>
        <button className="link" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      {n.doc && <div>{fillDoc(n.doc, values)}</div>}
      {n.def && <div className="muted small mono">def {n.def}</div>}
      {n.constraints && n.constraints.length > 0 && (
        <>
          <div className="section-title">Constraints</div>
          {n.constraints.map((c, i) => (
            <code key={i} className="block">
              {c.text}
            </code>
          ))}
        </>
      )}
      {n.thresholds && n.thresholds.length > 0 && (
        <>
          <div className="section-title">Values</div>
          <table className="table compact">
            <tbody>
              {n.thresholds.map((t) => (
                <tr key={t.name}>
                  <td className="mono">{t.name}</td>
                  <td className="mono">{t.value ?? <span className="warn-text">unset</span>}</td>
                  <td className="muted small">{t.setBy ? short(t.setBy) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {n.params && n.params.length > 0 && <div className="small muted">Parameters {n.params.join(", ")}: supplied by the simulation or the mission totals.</div>}
      <div className="section-title">Verification</div>
      <Tests tests={n.tests || []} onOpen={onOpen} />
      <div className="row-between">
        <VerdictPill v={n.verdict} />
        {vb && (
          <span className="small muted">
            {vb.method === "simulation" ? `simulated by ${short(vb.by)}` : vb.method === "evaluation" ? "from the mission totals" : vb.method === "analysis" ? `evaluated against the model${vb.by ? ` for ${short(vb.by)}` : ""}` : ""}
          </span>
        )}
      </div>
      {vb?.values && (
        <div className="small mono">
          {Object.entries(vb.values)
            .map(([k, v]) => `${k} = ${v == null ? "?" : Math.round(v * 100) / 100}`)
            .join(" · ")}
        </div>
      )}
      {vb?.note && <div className="small muted">{vb.note}</div>}
      {n.loc && (
        <button className="link" onClick={() => onOpen(located(n))}>
          Show in editor
        </button>
      )}
    </div>
  );
}
