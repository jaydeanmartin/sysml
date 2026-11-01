import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Editor, type EditorHandle } from "./editor/Editor";
import { Diagram } from "./diagram/Diagram";
import {
  behaviorView,
  defaultContext,
  interconnectionCandidates,
  interconnectionView,
  requirementsView,
  structureView,
  type ViewKind,
} from "./diagram/model";
import { AnalysesPanel } from "./panels/AnalysesPanel";
import { ConfiguratorPanel } from "./panels/ConfiguratorPanel";
import { ExecutePanel } from "./panels/ExecutePanel";
import { HistoryPanel } from "./panels/HistoryPanel";
import { LibraryPanel } from "./panels/LibraryPanel";
import { RequirementsPanel } from "./panels/RequirementsPanel";
import { SNIPPETS } from "./snippets";
import type { Catalog, CatalogChange, Diagnostic, GNode, Graph, Loc, Located, Model, ModelSummary, ModelTemplate, Project, Run, RunRequest } from "./types";

type Tab = "diagram" | "library" | "configurator" | "requirements" | "execute" | "analyses" | "history";
const CATALOG_TABS: Tab[] = ["library", "configurator"];
const EMPTY: Graph = { nodes: [], edges: [] };

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [saved, setSaved] = useState<Model | null>(null);
  const [source, setSource] = useState("");
  const [graph, setGraph] = useState<Graph>(EMPTY);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [parsing, setParsing] = useState(false);
  const [parseMs, setParseMs] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("diagram");
  const [view, setView] = useState<ViewKind>("structure");
  const [ibdContext, setIbdContext] = useState<string | null>(null);
  const [active, setActive] = useState<Set<string>>(new Set());
  const [lastRun, setLastRun] = useState<Run | null>(null);
  const [running, setRunning] = useState(false);
  const [verdicts, setVerdicts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ text: string; bad?: boolean } | null>(null);
  const [engine, setEngine] = useState<string | null>(null);
  const [leftPct, setLeftPct] = useState(42);
  const [newModel, setNewModel] = useState<{ name: string; template: ModelTemplate } | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogTick, setCatalogTick] = useState(0);
  const editor = useRef<EditorHandle>(null);
  const abort = useRef<AbortController | null>(null);
  const pendingReveal = useRef<{ modelId: string; loc: Loc; id: string } | null>(null);

  const dirty = !!saved && source !== saved.source;
  const errors = diagnostics.filter((d) => d.severity === "error");
  const nodesById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  const flash = (text: string, bad = false) => {
    setToast({ text, bad });
    window.setTimeout(() => setToast(null), 3500);
  };

  // ---------------------------------------------------------------- loading
  useEffect(() => {
    api.projects().then((ps) => {
      setProjects(ps);
      if (ps.length) setProjectId((cur) => cur || ps[0].id);
    }, (e) => flash(`Model service unavailable: ${e.message}`, true));
    api.interpreterHealth().then((h) => setEngine(h.opensysml), () => setEngine(null));
  }, []);

  useEffect(() => {
    if (!projectId) return;
    api.models(projectId).then((ms) => {
      setModels(ms);
      setModelId((cur) => (cur && ms.some((m) => m.id === cur) ? cur : ms[0]?.id || null));
    });
  }, [projectId]);

  const loadModel = useCallback(async (id: string) => {
    const m = await api.model(id);
    setSaved(m);
    setSource(m.source);
    setGraph(m.graph);
    setDiagnostics(m.diagnostics);
    setSelected(null);
    setActive(new Set());
    setLastRun(null);
    setIbdContext(defaultContext(m.graph));
    const reveal = pendingReveal.current;
    if (reveal?.modelId === id) {
      pendingReveal.current = null;
      setSelected(reveal.id);
      window.setTimeout(() => editor.current?.reveal(reveal.loc), 30);
    }
    const reqs = await api.requirements(id).catch(() => []);
    const v: Record<string, string> = {};
    for (const r of reqs) if (r.verdict) v[r.qualifiedName] = r.verdict.status;
    setVerdicts(v);
  }, []);

  useEffect(() => {
    if (modelId) loadModel(modelId).catch((e) => flash(e.message, true));
  }, [modelId, loadModel]);

  // ---------------------------------------------------------------- live interpretation
  useEffect(() => {
    if (!modelId || !saved || source === saved.source) {
      if (saved && source === saved.source) {
        setGraph(saved.graph);
        setDiagnostics(saved.diagnostics);
      }
      return;
    }
    const t = window.setTimeout(async () => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setParsing(true);
      try {
        const p = await api.preview(modelId, source, ctl.signal);
        setDiagnostics(p.diagnostics);
        setParseMs(p.elapsedMs);
        if (p.graph.nodes.length || p.valid) setGraph(p.graph); // keep the last good diagram on syntax errors
      } catch (e) {
        if ((e as Error).name !== "AbortError") flash((e as Error).message, true);
      } finally {
        if (abort.current === ctl) setParsing(false);
      }
    }, 550);
    return () => window.clearTimeout(t);
  }, [source, modelId, saved]);

  // ---------------------------------------------------------- component library / configurator
  const catalogTab = CATALOG_TABS.includes(tab);
  useEffect(() => {
    setCatalog(null);
  }, [projectId]);
  useEffect(() => {
    if (!projectId || !catalogTab) return;
    let stale = false;
    setCatalogLoading(true);
    api
      .catalog(projectId)
      .then(
        (c) => {
          if (stale) return;
          setCatalog(c);
          setCatalogError(null);
        },
        (e) => !stale && setCatalogError((e as Error).message)
      )
      .finally(() => !stale && setCatalogLoading(false));
    return () => {
      stale = true;
    };
  }, [projectId, catalogTab, catalogTick, saved?.headVersion]);

  useEffect(() => {
    if (ibdContext && !nodesById.has(ibdContext)) setIbdContext(defaultContext(graph));
    else if (!ibdContext) setIbdContext(defaultContext(graph));
  }, [graph, ibdContext, nodesById]);

  // ---------------------------------------------------------------- actions
  const save = useCallback(async () => {
    if (!modelId || !saved) return null;
    if (source === saved.source) return saved;
    setSaving(true);
    try {
      const m = await api.save(modelId, source, message || "Edited in SysML v2 Studio");
      setSaved(m);
      setGraph(m.graph);
      setDiagnostics(m.diagnostics);
      setMessage("");
      flash(`Saved version ${m.headVersion}${m.valid ? "" : " (with errors)"}`, !m.valid);
      return m;
    } catch (e) {
      flash((e as Error).message, true);
      return null;
    } finally {
      setSaving(false);
    }
  }, [modelId, saved, source, message]);

  const runModel = useCallback(
    async (req: RunRequest): Promise<Run | null> => {
      if (!modelId) return null;
      setRunning(true);
      try {
        if (dirty && !(await save())) return null;
        const r = await api.run(modelId, req);
        setLastRun(r);
        if (r.result && (r.kind === "satisfy" || r.kind === "requirement")) {
          const v: Record<string, string> = { ...verdicts };
          for (const c of r.result.checks) {
            const m = /^satisfy (\S+) by/.exec(c.subject);
            const req = m ? graph.nodes.find((n) => n.name === m[1] && n.kind === "requirement") : graph.nodes.find((n) => n.qualifiedName === c.subject);
            if (req?.qualifiedName) v[req.qualifiedName] = c.status;
          }
          setVerdicts(v);
        }
        return r;
      } catch (e) {
        flash((e as Error).message, true);
        return null;
      } finally {
        setRunning(false);
      }
    },
    [modelId, dirty, save, verdicts, graph]
  );

  const ensureSaved = useCallback(async () => !dirty || !!(await save()), [dirty, save]);

  /** Apply a catalog change (it saves a new version of some model of the project). */
  const changeCatalog = useCallback(
    async (fn: () => Promise<CatalogChange>): Promise<CatalogChange | null> => {
      if (!(await ensureSaved())) return null;
      try {
        const r = await fn();
        flash(`Saved ${r.modelName} v${r.version}: ${r.qualifiedName}`);
        if (r.modelId === modelId) await loadModel(r.modelId); // its new head version also refreshes the catalog
        else setCatalogTick((t) => t + 1);
        return r;
      } catch (e) {
        flash((e as Error).message, true);
        return null;
      }
    },
    [ensureSaved, modelId, loadModel]
  );

  /** Show a catalog element's text in the editor, switching models if needed. */
  const openElement = async (el: Located) => {
    if (!el.modelId || !el.loc) return;
    if (el.modelId === modelId) {
      select(el.id, false);
      editor.current?.reveal(el.loc);
      return;
    }
    if (!(await ensureSaved())) return;
    pendingReveal.current = { modelId: el.modelId, loc: el.loc, id: el.id };
    setModelId(el.modelId);
  };

  const select = (id: string | null, reveal = true) => {
    setSelected(id);
    const n = id ? nodesById.get(id) : undefined;
    if (reveal && n?.loc) editor.current?.reveal(n.loc);
  };

  const onCursor = (line: number, col: number) => {
    let best: GNode | null = null;
    for (const n of graph.nodes) {
      const l = n.loc;
      if (!l) continue;
      const after = line > l.line || (line === l.line && col >= l.column);
      const before = line < l.endLine || (line === l.endLine && col <= l.endColumn);
      if (after && before) {
        if (!best || (l.endLine - l.line) <= (best.loc!.endLine - best.loc!.line)) best = n;
      }
    }
    setSelected(best?.id || null);
  };

  const insert = (text: string, where: "cursor" | "packageEnd" = "packageEnd") => {
    editor.current?.insert(text, where);
    flash("Inserted into the model — the diagram updates as you edit.");
  };

  const createModel = async () => {
    if (!projectId || !newModel?.name) return;
    try {
      const m = await api.createModel(projectId, newModel.name, newModel.template);
      setModels(await api.models(projectId));
      setModelId(m.id);
      setNewModel(null);
    } catch (e) {
      flash((e as Error).message, true);
    }
  };

  const createProject = async () => {
    const name = prompt("New project name");
    if (!name) return;
    try {
      const p = await api.createProject(name);
      setProjects(await api.projects());
      setProjectId(p.id);
    } catch (e) {
      flash((e as Error).message, true);
    }
  };

  // ---------------------------------------------------------------- diagram model
  const diagram = useMemo(() => {
    switch (view) {
      case "interconnection":
        return interconnectionView(graph, ibdContext);
      case "requirements":
        return requirementsView(graph, verdicts);
      case "behavior":
        return behaviorView(graph);
      default:
        return structureView(graph);
    }
  }, [graph, view, ibdContext, verdicts]);
  const candidates = useMemo(() => interconnectionCandidates(graph), [graph]);
  const sel = selected ? nodesById.get(selected) : undefined;
  const activeLocs = useMemo(
    () => [...active].map((id) => nodesById.get(id)?.loc).filter((l): l is Loc => !!l),
    [active, nodesById]
  );

  const onDividerDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const move = (ev: MouseEvent) => setLeftPct(Math.min(75, Math.max(20, (ev.clientX / window.innerWidth) * 100)));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◧</span> SysML v2 Studio
        </div>
        <label className="sel">
          <span>Project</span>
          <select value={projectId || ""} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="icon" title="New project" onClick={createProject}>
            +
          </button>
        </label>
        <label className="sel">
          <span>Model</span>
          <select value={modelId || ""} onChange={(e) => setModelId(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
          <button className="icon" title="New model" onClick={() => setNewModel({ name: "", template: "empty" })} disabled={!projectId}>
            +
          </button>
        </label>
        <div className="grow" />
        <div className={`status ${errors.length ? "bad" : "ok"}`}>
          {parsing ? "Interpreting…" : errors.length ? `✗ ${errors.length} error${errors.length > 1 ? "s" : ""}` : "✓ Valid SysML v2"}
        </div>
        <input className="commit" placeholder="Version message" value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} />
        <button className="primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : dirty ? "Save" : `Saved v${saved?.headVersion ?? "–"}`}
        </button>
      </header>

      <main className="split" style={{ gridTemplateColumns: `${leftPct}% 6px 1fr` }}>
        <section className="left">
          <div className="toolbar">
            {SNIPPETS.map((s) => (
              <button key={s.label} title={s.title} onClick={() => insert(s.text, "cursor")}>
                {s.label}
              </button>
            ))}
            <button title="Capture a requirement" onClick={() => setTab("requirements")}>
              + Requirement
            </button>
          </div>
          <Editor
            ref={editor}
            value={source}
            onChange={setSource}
            diagnostics={diagnostics}
            selected={sel?.loc || null}
            activeLines={activeLocs}
            onCursor={onCursor}
            onSave={save}
          />
          <div className="problems">
            {diagnostics.length === 0 ? (
              <div className="muted small">No problems. {parseMs != null && `Interpreted in ${parseMs} ms.`}</div>
            ) : (
              diagnostics.map((d, i) => (
                <div key={i} className={`problem ${d.severity}`} onClick={() => editor.current?.reveal({ file: null, line: d.line, column: d.column, endLine: d.line, endColumn: d.column + 1 })}>
                  <span className="sev">{d.severity === "error" ? "✗" : "⚠"}</span>
                  <span className="mono small">
                    {d.line}:{d.column}
                  </span>{" "}
                  {d.message} {d.code && <span className="muted small">[{d.code}]</span>}
                </div>
              ))
            )}
          </div>
        </section>
        <div className="divider" onMouseDown={onDividerDown} />
        <section className="right">
          <nav className="tabs">
            {(
              [
                ["diagram", "Diagram"],
                ["library", "Library"],
                ["configurator", "Configurator"],
                ["requirements", "Requirements"],
                ["execute", "Execute"],
                ["analyses", "Analyses"],
                ["history", "History"],
              ] as [Tab, string][]
            ).map(([k, l]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {l}
              </button>
            ))}
          </nav>
          {tab === "diagram" && (
            <div className="diagram-wrap">
              <div className="diagram-bar">
                <div className="seg">
                  {(
                    [
                      ["structure", "Structure"],
                      ["interconnection", "Interconnection"],
                      ["requirements", "Requirements"],
                      ["behavior", "Behavior"],
                    ] as [ViewKind, string][]
                  ).map(([k, l]) => (
                    <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>
                      {l}
                    </button>
                  ))}
                </div>
                {view === "interconnection" && candidates.length > 0 && (
                  <select value={ibdContext || ""} onChange={(e) => setIbdContext(e.target.value)}>
                    {candidates.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.kind} {c.name}
                      </option>
                    ))}
                  </select>
                )}
                {active.size > 0 && <span className="pill warn">▶ execution trace</span>}
              </div>
              <Diagram model={diagram} selected={selected} active={active} onSelect={(id) => select(id)} fitKey={`${modelId}:${view}:${ibdContext}`} />
              {sel && (
                <div className="inspector">
                  <div className="muted small">
                    {sel.kind} {sel.tool && <span className="pill warn">⚡ REST {sel.tool.toolName}</span>}
                  </div>
                  <div className="strong mono">{sel.qualifiedName || sel.name}</div>
                  <code className="block">{sel.header}</code>
                  {sel.doc && <div className="small">{sel.doc}</div>}
                  {sel.loc && <div className="muted small">line {sel.loc.line}–{sel.loc.endLine}</div>}
                </div>
              )}
            </div>
          )}
          {tab === "requirements" && modelId && (
            <RequirementsPanel
              modelId={modelId}
              refreshKey={`${saved?.headVersion}:${lastRun?.id}`}
              dirty={dirty}
              graph={graph}
              onReveal={(loc, id) => {
                setSelected(id);
                editor.current?.reveal(loc);
              }}
              onInsert={(t) => insert(t)}
              onCheckAll={async () => {
                await runModel({ kind: "satisfy" });
              }}
            />
          )}
          {tab === "execute" && (
            <ExecutePanel
              graph={graph}
              busy={running}
              lastRun={lastRun}
              onRun={runModel}
              onActive={(ids, focus) => {
                setActive(new Set(ids));
                if (focus) setSelected(focus);
              }}
            />
          )}
          {catalogTab && projectId && (
            <>
              {tab === "library" ? (
                <LibraryPanel projectId={projectId} catalog={catalog} loading={catalogLoading} error={catalogError} dirty={dirty} ensureSaved={ensureSaved} onChange={changeCatalog} onOpen={openElement} />
              ) : (
                <ConfiguratorPanel projectId={projectId} catalog={catalog} loading={catalogLoading} error={catalogError} dirty={dirty} ensureSaved={ensureSaved} onChange={changeCatalog} onOpen={openElement} />
              )}
            </>
          )}
          {tab === "analyses" && <AnalysesPanel onInsert={(t) => insert(t)} />}
          {tab === "history" && modelId && (
            <HistoryPanel
              modelId={modelId}
              refreshKey={`${saved?.headVersion}:${lastRun?.id}`}
              onRestore={(src, v) => {
                setSource(src);
                flash(`Loaded version ${v} into the editor — save to make it the head version.`);
              }}
              onShowRun={(r) => {
                setLastRun(r);
                setTab("execute");
              }}
            />
          )}
        </section>
      </main>

      <footer className="statusbar">
        <span>{engine ? `Interpreter: OpenMBEE OpenSysML ${engine.replace("sysml ", "")}` : "Interpreter offline"}</span>
        <span>{graph.nodes.length} elements shown</span>
        <span>{saved ? `${saved.elementCount} stored elements (v${saved.headVersion})` : ""}</span>
        <span className="grow" />
        <span>{dirty ? "● unsaved changes" : "all changes saved"}</span>
      </footer>

      {toast && <div className={`toast${toast.bad ? " bad" : ""}`}>{toast.text}</div>}

      {newModel && (
        <div className="modal-bg" onClick={() => setNewModel(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="strong">New model</div>
            <label>
              Name
              <input autoFocus value={newModel.name} onChange={(e) => setNewModel({ ...newModel, name: e.target.value })} onKeyDown={(e) => e.key === "Enter" && createModel()} />
            </label>
            <label>
              Start from
              <select value={newModel.template} onChange={(e) => setNewModel({ ...newModel, template: e.target.value as ModelTemplate })}>
                <option value="empty">Empty package</option>
                <option value="library">Component library</option>
                <option value="architecture">System architecture</option>
                <option value="vehicle">Vehicle example</option>
              </select>
            </label>
            <div className="row-end">
              <button onClick={() => setNewModel(null)}>Cancel</button>
              <button className="primary" disabled={!newModel.name} onClick={createModel}>
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
