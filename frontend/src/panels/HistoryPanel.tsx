import { useEffect, useState } from "react";
import { api } from "../api";
import type { Run, Version } from "../types";

interface Props {
  modelId: string;
  refreshKey: string;
  onRestore(source: string, version: number): void;
  onShowRun(run: Run): void;
}

export function HistoryPanel({ modelId, refreshKey, onRestore, onShowRun }: Props) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    api.versions(modelId).then(setVersions, () => undefined);
    api.runs(modelId).then(setRuns, () => undefined);
  }, [modelId, refreshKey]);

  return (
    <div className="panel two-col">
      <div>
        <div className="strong section">Versions</div>
        <table className="table compact">
          <tbody>
            {versions.map((v) => (
              <tr key={v.version}>
                <td className="mono">v{v.version}</td>
                <td>
                  <span className={`dot ${v.valid ? "ok" : "bad"}`} /> {v.message || <span className="muted">no message</span>}
                </td>
                <td className="small muted">{v.elementCount} elements</td>
                <td className="small muted">{new Date(v.createdAt).toLocaleString()}</td>
                <td>
                  <button
                    className="link"
                    onClick={async () => {
                      const m = await api.model(modelId, v.version);
                      onRestore(m.source, v.version);
                    }}
                  >
                    load
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="strong section">Runs</div>
        <table className="table compact">
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={async () => onShowRun(await api.getRun(r.id))}>
                <td>
                  <span className={`dot ${r.status === "holds" ? "ok" : "bad"}`} />
                </td>
                <td>{r.kind}</td>
                <td className="mono small">{r.target || ""}</td>
                <td className="small muted">v{r.version}</td>
                <td className="small muted">{new Date(r.createdAt).toLocaleTimeString()}</td>
              </tr>
            ))}
            {!runs.length && (
              <tr>
                <td className="muted">No runs yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
