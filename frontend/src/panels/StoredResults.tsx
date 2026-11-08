import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvidenceRecord, StoredResult } from "../types";
import { errorText, short } from "./catalogUtil";

/** The newest stored simulation results of a mission: results live outside the
 *  SysML v2 repository; recorded evidence in the model points at one of them. */
export function StoredResults({ projectId, mission, evidence, refreshKey }: { projectId: string; mission: string; evidence: EvidenceRecord[]; refreshKey: unknown }) {
  const [rows, setRows] = useState<StoredResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let stale = false;
    api
      .results(projectId, mission, undefined, 30)
      .then((r) => !stale && (setRows(r), setError(null)))
      .catch((e) => !stale && setError(errorText(e)));
    return () => {
      stale = true;
    };
  }, [projectId, mission, refreshKey, open]);

  const recorded = new Set(evidence.map((e) => e.values.resultId));
  const num = (r: StoredResult, k: string) => (typeof r.outputs?.[k] === "number" ? (r.outputs[k] as number) : null);

  return (
    <div className="stored-results">
      <div className="section-title row-between">
        <button className="link section-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "▾" : "▸"} Stored results <span className="muted small">· kept outside the model; evidence refers to them</span>
        </button>
      </div>
      {open &&
        (error ? (
          <div className="error-box">{error}</div>
        ) : !rows ? (
          <div className="muted small">Loading…</div>
        ) : !rows.length ? (
          <div className="muted small">No stored results for {short(mission)} yet: evaluate or simulate it.</div>
        ) : (
          <div className="table-scroll">
            <table className="table compact">
              <thead>
                <tr>
                  <th>When</th>
                  <th>System</th>
                  <th>Simulation</th>
                  <th title="The project commit that was simulated">Commit</th>
                  <th className="num">Energy</th>
                  <th className="num">Time</th>
                  <th>Verdicts</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const e = num(r, "energyPercent");
                  const t = num(r, "missionTime");
                  return (
                    <tr key={r.id}>
                      <td className="small nowrap" title={r.createdAt}>
                        {new Date(r.createdAt).toLocaleString()}
                      </td>
                      <td className="mono">{short(r.system)}</td>
                      <td className="small" title={`${r.toolName} version ${r.toolVersion ?? "?"}`}>
                        {short(r.simulation)}
                      </td>
                      <td className="mono small" title={r.commitId || ""}>
                        {r.commitId?.slice(0, 8) || "—"}
                      </td>
                      <td className="mono num">{e === null ? "—" : `${e.toFixed(1)} %`}</td>
                      <td className="mono num">{t === null ? "—" : `${(t / 60).toFixed(1)} min`}</td>
                      <td>
                        {r.error ? (
                          <span className="error-text small">{r.error}</span>
                        ) : (
                          <span className="chips">
                            {Object.entries(r.verdicts).map(([q, v]) => (
                              <span key={q} className={`pill ${v === "holds" ? "ok" : "bad"}`}>
                                {v === "holds" ? "✓" : v === "fails" ? "✗" : "?"} {short(q)}
                              </span>
                            ))}
                          </span>
                        )}
                      </td>
                      <td className="nowrap small">
                        {recorded.has(r.id) && <span className="pill ok" title="Recorded as verification evidence in the model">◆ evidence</span>}{" "}
                        {r.reusedFrom && <span className="muted" title={`Outputs reused from result ${r.reusedFrom}: same simulator, version and inputs`}>reused </span>}
                        <a href={api.resultUrl(r.id)} target="_blank" rel="noreferrer">
                          data ↗
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
