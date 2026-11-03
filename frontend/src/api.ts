import type {
  Analysis,
  Catalog,
  CatalogChange,
  ComponentRequest,
  ConfigurationRequest,
  InstanceRequest,
  MissionEvaluation,
  MissionRequest,
  SimulationRequest,
  SimulationResult,
  StepRequest,
  ModelTemplate,
  Invocation,
  Model,
  ModelSummary,
  Preview,
  Project,
  Requirement,
  Run,
  RunRequest,
  Version,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(url: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const opts: RequestInit = { ...init };
  if (init?.json !== undefined) {
    opts.body = JSON.stringify(init.json);
    opts.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const d = body.detail;
      msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join("; ") : msg;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

const M = "/api/models";
const A = "/api/analysis";
const I = "/api/interpreter";
const G = "/api/geometry";

export const api = {
  projects: () => call<Project[]>(`${M}/projects`),
  createProject: (name: string, description = "") =>
    call<Project>(`${M}/projects`, { method: "POST", json: { name, description } }),
  models: (projectId: string) => call<ModelSummary[]>(`${M}/projects/${projectId}/models`),
  createModel: (projectId: string, name: string, template: ModelTemplate) =>
    call<Model>(`${M}/projects/${projectId}/models`, { method: "POST", json: { name, template } }),
  deleteModel: (id: string) => call<void>(`${M}/models/${id}`, { method: "DELETE" }),
  model: (id: string, version?: number) =>
    call<Model>(`${M}/models/${id}${version ? `?version=${version}` : ""}`),
  preview: (id: string, source: string, signal?: AbortSignal) =>
    call<Preview>(`${M}/models/${id}/preview`, { method: "POST", json: { source }, signal }),
  save: (id: string, source: string, message: string) =>
    call<Model>(`${M}/models/${id}/source`, { method: "PUT", json: { source, message } }),
  versions: (id: string) => call<Version[]>(`${M}/models/${id}/versions`),
  requirements: (id: string) => call<Requirement[]>(`${M}/models/${id}/requirements`),
  run: (id: string, req: RunRequest) => call<Run>(`${M}/models/${id}/runs`, { method: "POST", json: req }),
  runs: (id: string) => call<Run[]>(`${M}/models/${id}/runs`),
  getRun: (runId: string) => call<Run>(`${M}/runs/${runId}`),

  catalog: (projectId: string) => call<Catalog>(`${M}/projects/${projectId}/catalog`),
  createComponent: (projectId: string, req: ComponentRequest) =>
    call<CatalogChange>(`${M}/projects/${projectId}/catalog/components`, { method: "POST", json: req }),
  saveInstance: (projectId: string, req: InstanceRequest) =>
    call<CatalogChange>(`${M}/projects/${projectId}/catalog/instances`, { method: "POST", json: req }),
  saveConfiguration: (projectId: string, req: ConfigurationRequest) =>
    call<CatalogChange>(`${M}/projects/${projectId}/catalog/configurations`, { method: "POST", json: req }),
  saveMission: (projectId: string, req: MissionRequest) =>
    call<CatalogChange>(`${M}/projects/${projectId}/catalog/missions`, { method: "POST", json: req }),
  simulateMission: (projectId: string, req: SimulationRequest) =>
    call<SimulationResult>(`${M}/projects/${projectId}/catalog/missions/simulate`, { method: "POST", json: req }),
  evaluateMissions: (projectId: string, missionType: string, draft: StepRequest[] | null) =>
    call<MissionEvaluation>(`${M}/projects/${projectId}/catalog/missions/evaluate`, { method: "POST", json: { missionType, draft } }),
  deleteCatalogElement: (projectId: string, qualifiedName: string) =>
    call<CatalogChange>(`${M}/projects/${projectId}/catalog/elements?qualifiedName=${encodeURIComponent(qualifiedName)}`, {
      method: "DELETE",
    }),

  analyses: () => call<Analysis[]>(`${A}/analyses`),
  createAnalysis: (a: Partial<Analysis>) => call<Analysis>(`${A}/analyses`, { method: "POST", json: a }),
  updateAnalysis: (a: Partial<Analysis>) =>
    call<Analysis>(`${A}/analyses/${a.name}`, { method: "PUT", json: a }),
  toggleAnalysis: (name: string, enabled: boolean) =>
    call<Analysis>(`${A}/analyses/${name}?enabled=${enabled}`, { method: "PATCH" }),
  deleteAnalysis: (name: string) => call<void>(`${A}/analyses/${name}`, { method: "DELETE" }),
  invoke: (name: string, inputs: Record<string, unknown>) =>
    call<{ outputs: Record<string, { value: unknown; unit?: string }> }>(`${A}/analyses/${name}/invoke`, {
      method: "POST",
      json: { inputs },
    }),
  invocations: (limit = 50) => call<Invocation[]>(`${A}/invocations?limit=${limit}`),

  /** binary glTF of a system instance, from the geometry service */
  geometry: async (projectId: string, system: string): Promise<ArrayBuffer> => {
    const res = await fetch(`${G}/projects/${projectId}/geometry.glb?system=${encodeURIComponent(system)}`);
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try {
        msg = (await res.json()).detail || msg;
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, msg);
    }
    return res.arrayBuffer();
  },

  interpreterHealth: () => call<{ status: string; opensysml: string | null }>(`${I}/health`),
};
