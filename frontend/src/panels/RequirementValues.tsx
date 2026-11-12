import type { RequirementThreshold, RequirementUsage, RequirementVerdict } from "../types";
import { short } from "./catalogUtil";

/** a requirement value an instance states: a number, optionally with a unit (mirrors the server) */
export const NUMBER_RE = /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?:\s*\[\s*[A-Za-z_][\w:]*(?:\s*[*/^]\s*[\w:]+)*\s*\])?$/;

/** a requirement's id: its own short name, else its def's */
export const reqId = (r: { shortId?: string | null; defShortId?: string | null }) => r.shortId || r.defShortId || null;

/** the id as a tag before a requirement's name */
export function ReqId({ id }: { id: string | null | undefined }) {
  return id ? <span className="req-id">{id}</span> : null;
}

/** every usage, nested ones included, parents first */
export function walkUsages(usages: RequirementUsage[], out: RequirementUsage[] = []): RequirementUsage[] {
  for (const u of usages) {
    out.push(u);
    walkUsages(u.children, out);
  }
  return out;
}

/** the values an instance must state: thresholds its def's usages leave unset */
export function openThresholds(u: RequirementUsage): RequirementThreshold[] {
  return u.thresholds.filter((t) => t.required && !u.values[t.name]);
}

/** "usage.attribute" of the required values not (validly) given */
export function missingValues(usages: RequirementUsage[], values: Record<string, string>): string[] {
  return walkUsages(usages).flatMap((u) => openThresholds(u).map((t) => `${u.path}.${t.name}`).filter((k) => !NUMBER_RE.test((values[k] || "").trim())));
}

/** an instance's own values as "usage.attribute" -> value */
export function ownValues(usages: RequirementUsage[]): Record<string, string> {
  return Object.fromEntries(walkUsages(usages).flatMap((u) => Object.entries(u.ownValues).map(([k, v]) => [`${u.path}.${k}`, v])));
}

/** the requirement text with its {attribute} placeholders filled in */
export function fillDoc(doc: string | null | undefined, values: Record<string, string | null | undefined>): string {
  return (doc || "").replace(/\{(\w+)\}/g, (_, k) => (values[k] != null && values[k] !== "" ? String(values[k]) : `‹${k}›`));
}

/** SysML lines that set the values (mirrors catalog.requirements_text) */
export function requirementLines(values: Record<string, string>, usages: RequirementUsage[] = []): string[] {
  // a usage declared with an id is redefined restating it (OpenSysML finds it by that id)
  const ids = new Map(walkUsages(usages).filter((u) => u.declaredShortId).map((u) => [u.path, u.declaredShortId!]));
  type Tree = { values: [string, string][]; kids: Map<string, Tree> };
  const root: Tree = { values: [], kids: new Map() };
  for (const [key, raw] of Object.entries(values)) {
    const v = raw.trim();
    const parts = key.split(".");
    if (!v || parts.length < 2) continue;
    let node = root;
    for (const p of parts.slice(0, -1)) {
      if (!node.kids.has(p)) node.kids.set(p, { values: [], kids: new Map() });
      node = node.kids.get(p)!;
    }
    node.values.push([parts[parts.length - 1], v]);
  }
  const emit = (path: string, name: string, t: Tree): string[] => {
    const inner = [...t.values.map(([a, v]) => `attribute :>> ${a} = ${v};`), ...[...t.kids].flatMap(([k, sub]) => emit(`${path}.${k}`, k, sub))];
    const id = ids.get(path);
    const head = id ? `requirement <'${id}'> ${name} :>> ${name}` : `requirement :>> ${name}`;
    if (inner.length === 1 && !inner[0].startsWith("requirement")) return [`${head} { ${inner[0]} }`];
    return [`${head} {`, ...inner.map((l) => `    ${l}`), "}"];
  };
  return [...root.kids].flatMap(([k, t]) => emit(k, k, t));
}

/** Inputs for the numbers an instance states in the requirements its def includes. */
export function RequirementValues({
  usages,
  values,
  onChange,
  what = "instance",
}: {
  usages: RequirementUsage[];
  values: Record<string, string>;
  onChange(values: Record<string, string>): void;
  what?: string;
}) {
  const rows = walkUsages(usages);
  if (!rows.length) return null;
  return (
    <div className="req-values">
      <div className="section-title">
        Requirements <span className="muted small">— every {what} states its own numbers</span>
      </div>
      {rows.map((u) => {
        const open = openThresholds(u);
        const depth = u.path.split(".").length - 1;
        const filled = { ...u.values, ...Object.fromEntries(open.map((t) => [t.name, values[`${u.path}.${t.name}`]])) };
        return (
          <div key={u.path} className="req-value-row" style={{ marginLeft: depth * 16 }}>
            <div className="req-value-head">
              <ReqId id={reqId(u)} />
              <span className="strong mono">{u.name}</span>
              <span className="muted mono small"> : {u.defName || "?"}</span>
              {u.doc && <span className="small"> — {fillDoc(u.doc, filled)}</span>}
            </div>
            <div className="chips">
              {u.thresholds
                .filter((t) => !open.includes(t) && u.values[t.name] && u.bound.includes(t.name))
                .map((t) => (
                  <span key={t.name} className="port-chip mono" title={`set in ${short(t.setBy)}`}>
                    {t.name} = {u.values[t.name]}
                  </span>
                ))}
            </div>
            {open.length > 0 && (
              <div className="params">
                {open.map((t) => {
                  const key = `${u.path}.${t.name}`;
                  const v = values[key] || "";
                  const bad = !NUMBER_RE.test(v.trim());
                  return (
                    <label key={key}>
                      <span>
                        {t.name} <span className="mono">: {t.type || "?"}</span>
                        {bad && <span className="error-text small"> {v.trim() ? "a number" : "required"}</span>}
                      </span>
                      <input className={`mono${bad ? " invalid" : ""}`} value={v} placeholder="number, e.g. 15 or 2.5 [SI::kg]" onChange={(e) => onChange({ ...values, [key]: e.target.value })} />
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const VERDICT_LABEL: Record<RequirementVerdict, string> = {
  holds: "✓ holds",
  fails: "✗ fails",
  error: "! error",
  missing: "values missing",
  "cannot fly": "cannot fly",
};

export function VerdictPill({ v, title }: { v: RequirementVerdict | null | undefined; title?: string }) {
  if (!v) return <span className="pill" title={title}>not checked</span>;
  return (
    <span className={`pill ${v === "holds" ? "ok" : v === "fails" || v === "error" ? "bad" : "warn"}`} title={title}>
      {VERDICT_LABEL[v]}
    </span>
  );
}
