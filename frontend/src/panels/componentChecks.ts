import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Catalog, SpecRequirement, SpecVerdict } from "../types";

export interface ComponentCheck {
  requirement: SpecRequirement;
  verdict: SpecVerdict;
}

/** The component requirements of the specification (instances whose subject is a
 *  component type) for each catalog instance they apply to, with verdicts from a
 *  static requirements check (evaluation only, no simulations), re-run whenever
 *  the catalog changes. Shared by the Library and the Configurator. */
export function useComponentChecks(projectId: string, catalog: Catalog | null) {
  const [verdicts, setVerdicts] = useState<Record<string, Record<string, SpecVerdict>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reqs = useMemo(() => (catalog?.specification || []).filter((r) => r.type && r.level === "component"), [catalog]);

  useEffect(() => {
    if (!catalog || !reqs.length) {
      setVerdicts(null);
      return;
    }
    let live = true;
    api
      .checkRequirements(projectId, "static")
      .then((c) => {
        if (!live) return;
        setVerdicts(c.verdicts);
        setError(c.errors.length ? c.errors.join("\n") : null);
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [projectId, catalog, reqs.length]);

  /** instance qualified name -> the component requirements that apply to it */
  const byInstance = useMemo(() => {
    const m = new Map<string, ComponentCheck[]>();
    for (const r of reqs)
      for (const inst of r.appliesTo) {
        const verdict = verdicts?.[r.qualifiedName]?.[inst] ?? "unverified";
        m.set(inst, [...(m.get(inst) || []), { requirement: r, verdict }]);
      }
    return m;
  }, [reqs, verdicts]);

  const failing = (inst: string | null | undefined) =>
    (byInstance.get(inst || "") || []).filter((c) => c.verdict === "fails" || c.verdict === "error");

  return { requirements: reqs, byInstance, failing, checked: verdicts !== null, error };
}

/** "CMP-1.1 motorEfficiency" */
export const reqLabel = (r: SpecRequirement) => `${r.shortName ? `${r.shortName} ` : ""}${r.name}`;
