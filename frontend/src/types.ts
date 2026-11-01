export interface Loc {
  file: string | null;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
}

export interface GNode {
  id: string;
  kind: string;
  metatype: string;
  name: string;
  qualifiedName: string | null;
  owner: string | null;
  loc: Loc | null;
  typeName: string | null;
  typeId: string | null;
  conjugated: boolean;
  direction: "in" | "out" | "inout" | null;
  value: string | null;
  multiplicity: string | null;
  isAbstract: boolean;
  header: string;
  redefines: string | null;
  doc?: string;
  constraints?: { kind: string; text: string }[];
  tool?: { toolName: string; uri: string };
}

export interface GEdge {
  id: string;
  kind: string;
  source: string;
  target: string;
  sourcePath?: string[];
  targetPath?: string[];
  context?: string | null;
  label?: string | null;
  loc?: Loc | null;
}

export interface Graph {
  nodes: GNode[];
  edges: GEdge[];
}

export interface Diagnostic {
  severity: "error" | "warning" | "info" | string;
  message: string;
  file: string | null;
  line: number;
  column: number;
  code?: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  modelCount: number;
}

export interface ModelSummary {
  id: string;
  projectId: string;
  name: string;
  description: string;
  headVersion: number;
  valid?: boolean;
  summary?: Record<string, number>;
}

export interface Model extends ModelSummary {
  source: string;
  diagnostics: Diagnostic[];
  graph: Graph;
  elementCount: number;
  versionMessage: string;
}

export interface Preview {
  valid: boolean;
  diagnostics: Diagnostic[];
  graph: Graph;
  summary: Record<string, number>;
  elapsedMs: number;
}

export interface Check {
  subject: string;
  status: string;
  values: { name: string; value: string }[];
  lines: string[];
  standing?: string | null;
}

export interface TraceEvent {
  type: "step" | "enter" | "exit" | "transition" | "stmt";
  node?: string;
  from?: string;
  to?: string;
  event?: string;
  step?: number;
  text?: string;
}

export interface Invocation {
  id: number;
  analysis: string;
  runId: string | null;
  uri: string | null;
  inputs: Record<string, unknown> | null;
  outputs: Record<string, { value: unknown; unit?: string }> | null;
  status: string;
  error: string | null;
  durationMs: number | null;
  createdAt: string;
}

export interface ExecResult {
  runId: string;
  status: string;
  exit: number;
  elapsedMs: number;
  command: string[];
  checks: Check[];
  diagnostics: Diagnostic[];
  output: string[];
  errors: string[];
  trace: TraceEvent[];
  tools: string[];
}

export interface Run {
  id: string;
  modelId: string;
  version: number;
  kind: string;
  target: string | null;
  request: RunRequest;
  status: string;
  result: ExecResult | null;
  elapsedMs: number | null;
  createdAt: string;
  invocations?: Invocation[];
}

export type RunKind =
  | "calc"
  | "analysis"
  | "requirement"
  | "constraint"
  | "satisfy"
  | "action"
  | "state"
  | "evaluate"
  | "validate";

export interface RunRequest {
  kind: RunKind;
  target?: string | null;
  args?: string[];
  subject?: string | null;
  expression?: string | null;
  advance?: number | null;
  trace?: boolean;
}

export interface Variable {
  name: string;
  unit?: string | null;
  description?: string | null;
}

export interface Analysis {
  name: string;
  kind: "builtin" | "external" | "formula";
  description: string;
  version: string;
  endpointUrl: string | null;
  formulas: Record<string, string>;
  inputs: Variable[];
  outputs: Variable[];
  timeoutSeconds: number;
  enabled: boolean;
}

export interface Requirement {
  id: string;
  kind: string;
  name: string;
  qualifiedName: string;
  doc: string | null;
  constraints: { kind: string; text: string }[];
  typeName: string | null;
  subject: { name: string; type: string | null; value: string | null } | null;
  satisfiedBy: string[];
  verdict: { status: string; at: string } | null;
  loc: Loc | null;
}

export interface Version {
  version: number;
  message: string;
  valid: boolean;
  elementCount: number;
  size: number;
  createdAt: string;
}

// ------------------------------------------------------------ component library & configurator
export type ModelTemplate = "empty" | "vehicle" | "library" | "architecture";

/** Where a catalog element is declared. */
export interface Located {
  id: string;
  name: string;
  qualifiedName: string;
  package: string | null;
  modelId: string | null;
  modelName: string | null;
  doc: string | null;
  loc: Loc | null;
}

export interface CatalogAttribute {
  name: string;
  type: string | null;
  value: string | null;
  declaredIn: string | null;
}

export interface Component extends Located {
  isAbstract: boolean;
  specializes: string[];
  attributes: CatalogAttribute[];
  ports: { name: string; type: string | null; declaredIn: string | null }[];
  parts: { name: string; type: string | null; multiplicity: string | null }[];
  instances: string[];
}

export interface ComponentInstance extends Located {
  componentId: string;
  component: string;
  /** effective values (component defaults + the instance's own) */
  values: Record<string, string>;
  ownValues: Record<string, string>;
}

export interface Slot {
  name: string;
  type: string;
  typeId: string;
  multiplicity: string | null;
  doc: string | null;
  declaredIn: string | null;
  /** ids of the instances whose component fits this slot */
  candidates: string[];
}

export interface Architecture extends Located {
  isAbstract: boolean;
  specializes: string[];
  slots: Slot[];
  attributes: CatalogAttribute[];
  connections: number;
  /** qualified names of the requirement defs whose subject type this architecture conforms to */
  requirements: string[];
}

export interface CatalogRequirement extends Located {
  subject: string;
  subjectTypeId: string;
}

export interface SystemConfiguration extends Located {
  architectureId: string;
  architecture: string;
  /** slot name -> qualified name of the component instance */
  assignments: Record<string, string | null>;
}

export interface Catalog {
  components: Component[];
  instances: ComponentInstance[];
  architectures: Architecture[];
  configurations: SystemConfiguration[];
  requirements: CatalogRequirement[];
  packages: { qualifiedName: string; modelId: string | null; modelName: string | null; loc: Loc | null }[];
  tags: Record<string, string>;
  diagnostics: Diagnostic[];
}

export interface CatalogChange {
  modelId: string;
  modelName: string;
  version: number;
  valid: boolean;
  qualifiedName: string;
  text?: string;
}

export interface InstanceRequest {
  component: string;
  name: string;
  doc?: string;
  values: Record<string, string>;
  package?: string | null;
  replace?: string | null;
}

export interface ConfigurationRequest {
  architecture: string;
  name: string;
  doc?: string;
  assignments: Record<string, string | null>;
  package?: string | null;
  replace?: string | null;
}

export interface ComponentRequest {
  name: string;
  package: string;
  specializes?: string | null;
  abstract?: boolean;
  doc?: string;
  attributes: { name: string; type: string }[];
  ports: { name: string; type: string }[];
}
