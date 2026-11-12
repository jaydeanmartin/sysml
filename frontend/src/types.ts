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
  /** its id: the SysML short name */
  shortName?: string | null;
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
  /** The commit of this version in the project's SysML v2 API repository. */
  commitId: string | null;
}

// ------------------------------------------------------------ component library & configurator
export type ModelTemplate = "empty" | "library" | "architecture";

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
  /** part: a #Component part def; action: a #Behavior action def */
  kind: "part" | "action";
  isAbstract: boolean;
  specializes: string[];
  attributes: CatalogAttribute[];
  /** a behavior's in parameters, set by the system that performs it */
  params: CatalogAttribute[];
  ports: { name: string; type: string | null; declaredIn: string | null }[];
  /** its part features; those typed by a component (a material) list the instances that can fill them */
  parts: { name: string; type: string | null; multiplicity: string | null; candidates: string[] }[];
  instances: string[];
  /** component analyses (tool-bound defs with this component as subject) */
  analyses: string[];
  /** requirement usages its instances give numbers to */
  requirementUsages: RequirementUsage[];
  lineageIds: string[];
}

export interface ComponentInstance extends Located {
  /** part: a component instance; action: a behavior instance */
  kind: "part" | "action";
  componentId: string;
  component: string;
  /** effective values (component defaults + the instance's own) */
  values: Record<string, string>;
  ownValues: Record<string, string>;
  /** parameters still without a value: such a behavior instance cannot be performed */
  missing: string[];
  requirementUsages: RequirementUsage[];
  /** "usage.attribute" of required requirement values still unset */
  missingRequirementValues: string[];
  /** component-typed part -> the instance that fills it (a propeller's material) */
  parts: Record<string, string | null>;
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

/** a behavior the architecture performs (perform action usage typed by a #Behavior) */
export interface BehaviorSlot {
  name: string;
  type: string;
  typeId: string;
  doc: string | null;
  declaredIn: string | null;
  /** ids of the complete behavior instances that fit */
  candidates: string[];
}

export interface Architecture extends Located {
  isAbstract: boolean;
  specializes: string[];
  slots: Slot[];
  behaviors: BehaviorSlot[];
  attributes: CatalogAttribute[];
  connections: number;
  /** qualified names of the requirement defs whose subject type this architecture conforms to */
  requirements: string[];
  /** ... that also take mission totals as in parameters */
  missionRequirements: string[];
  /** mission simulations this architecture can be the subject of */
  simulations: string[];
  requirementUsages: RequirementUsage[];
  lineageIds: string[];
}

export interface CatalogRequirement extends Located {
  /** its id: the SysML short name, requirement def <'SYS-01'> Name */
  shortId: string | null;
  /** null: no subject (a library requirement such as MinimumValue) */
  subject: string | null;
  subjectType: string | null;
  subjectTypeId: string | null;
  /** names of its in parameters (mission requirements have some) */
  params: string[];
  isAbstract: boolean;
  /** abstract, or leaves an attribute without a value: used through requirement usages whose instances state the numbers */
  template: boolean;
  /** its attributes (inherited included); those without a value are set by a usage or an instance */
  thresholds: { name: string; type: string | null; value: string | null }[];
  specializes: string[];
  /** the def and every requirement def it specializes */
  lineage: string[];
  constraints: { kind: string; text: string }[];
  /** nested requirement usages */
  children: { name: string; type: string | null }[];
  /** simulations that verify it */
  verifiedBy: string[];
  /** tests (verification defs, simulations included) that verify it */
  tests: string[];
}

/** an attribute of a requirement usage's def: a threshold when the def leaves it without a value */
export interface RequirementThreshold {
  name: string;
  type: string | null;
  /** the def leaves it without a value: a usage or each instance sets it */
  required: boolean;
  value: string | null;
  /** where the value comes from */
  setBy: string | null;
}

/** a requirement usage of a def (component, architecture, behavior, mission type) or as an instance redefines it */
export interface RequirementUsage {
  name: string;
  /** dotted path from the owner: "delivery.payload" for a nested one */
  path: string;
  def: string | null;
  defName: string | null;
  /** its id (short name of its most specific declaration), and its def's */
  shortId: string | null;
  defShortId: string | null;
  /** the short name its declaration gives it; a redefinition restates it */
  declaredShortId: string | null;
  defLineage: string[];
  /** text; {name} stands for the value of attribute name */
  doc: string | null;
  constraints: { kind: string; text: string }[];
  /** its def has a subject: checked with the owner as subject */
  subject: boolean;
  /** in parameters: supplied when checked (by a simulation or mission totals) */
  params: string[];
  thresholds: RequirementThreshold[];
  values: Record<string, string>;
  /** the values the instance itself states */
  ownValues: Record<string, string>;
  bound: string[];
  /** required values still unset */
  missing: string[];
  /** qualified names of the usages that declare or redefine it */
  declarations: string[];
  declaredIn: string | null;
  loc: Loc | null;
  modelId: string | null;
  children: RequirementUsage[];
}

/** a verification def: a test of the requirements its objective verifies */
export interface CatalogTest extends Located {
  /** simulation: bound to an external analysis; analysis: the requirement is evaluated against the model */
  method: "simulation" | "analysis";
  toolName: string | null;
  subject: string | null;
  subjectType: string | null;
  subjectTypeId: string | null;
  /** requirement defs and requirement usages it verifies */
  verifies: string[];
}

export type RequirementVerdict = "holds" | "fails" | "error" | "missing" | "cannot fly";

export interface TestRef {
  qualifiedName: string;
  name: string;
  method: "simulation" | "analysis";
  toolName: string | null;
  modelId: string | null;
  loc: Loc | null;
  /** inherited from the parent requirement of this name */
  via?: string;
}

/** a node of a system instance's requirement hierarchy */
export interface RequirementNode {
  kind: "system" | "group" | "component" | "behavior" | "mission" | "requirement";
  key: string;
  name: string;
  /** system, component, behavior: the instance; mission: the mission */
  element?: string | null;
  /** the slot's or mission's type */
  type?: string | null;
  multiplicity?: string | null;
  modelId?: string | null;
  loc?: Loc | null;
  children: RequirementNode[];
  /** the worst verdict of the node and everything below it */
  rollup?: RequirementVerdict | null;
  // requirement nodes
  shortId?: string | null;
  defShortId?: string | null;
  path?: string | null;
  def?: string | null;
  defName?: string | null;
  doc?: string | null;
  constraints?: { kind: string; text: string }[];
  thresholds?: { name: string; type: string | null; value: string | null; required?: boolean; setBy?: string | null }[];
  params?: string[];
  missing?: string[];
  origin?: string;
  tests?: TestRef[];
  verdict?: RequirementVerdict | null;
  verifiedBy?: { method: string | null; by?: string | null; values?: Record<string, number | null> | null; note?: string | null };
  // mission nodes, once checked
  problems?: string[];
  totals?: Record<string, number | null>;
}

export interface RequirementTree {
  system: string;
  tree: RequirementNode;
  tests: TestRef[];
  errors?: string[];
  runId?: string | null;
}

/** a #Mission action def; each attribute is a total over the steps */
export interface MissionType extends Located {
  isAbstract: boolean;
  specializes: string[];
  totals: CatalogAttribute[];
  requirementUsages: RequirementUsage[];
  lineageIds: string[];
}

export interface MissionStep {
  name: string;
  /** qualified name of the step's behavior (an abstract #Behavior action def) */
  behavior: string | null;
  /** quantity -> expression */
  values: Record<string, string>;
}

export interface Mission extends Located {
  missionTypeId: string;
  missionType: string;
  steps: MissionStep[];
  requirementUsages: RequirementUsage[];
  missingRequirementValues: string[];
}

/** a mission as one system instance flies it */
export interface FlownMission {
  /** why it cannot fly it (a behavior its architecture does not perform...) */
  problems: string[];
  steps: { name: string; performedBy: string; values: Record<string, number | null> }[];
  totals: Record<string, number | null>;
  /** mission requirement -> verdict */
  verdicts: Record<string, "holds" | "fails" | "error">;
  /** mission requirement -> how it was verified */
  verification: Record<string, Verification>;
  /** simulation def -> its run, for each simulation that verified a requirement
   *  (outputs without timeline, steps and curves) */
  simulations?: Record<string, SimulationRun>;
}

/** how a mission requirement was verified for one system instance */
export interface Verification {
  /** simulation: with the results of the mission simulation that verifies it;
   *  evaluation: with the mission totals, summed over the flown steps */
  method: "simulation" | "evaluation";
  /** the simulation def */
  by: string | null;
  /** the requirement's parameters, as supplied */
  values?: Record<string, number | null>;
  note?: string;
}

export interface MissionEvaluation {
  missionType: string;
  totals: string[];
  /** mission qualified name (or "draft") -> system instance -> result */
  missions: Record<string, Record<string, FlownMission>>;
  errors: string[];
}

export interface SystemConfiguration extends Located {
  architectureId: string;
  architecture: string;
  /** slot name -> qualified name of the component instance */
  assignments: Record<string, string | null>;
  /** behavior slot -> qualified name of the behavior instance performed */
  behaviors: Record<string, string | null>;
  requirementUsages: RequirementUsage[];
  missingRequirementValues: string[];
}

export interface Catalog {
  components: Component[];
  instances: ComponentInstance[];
  architectures: Architecture[];
  configurations: SystemConfiguration[];
  missionTypes: MissionType[];
  missions: Mission[];
  requirements: CatalogRequirement[];
  /** defs bound to an external analysis (ToolExecution) with a system subject */
  simulations: MissionSimulationDef[];
  /** verification defs (simulations included) */
  tests: CatalogTest[];
  /** tool-bound defs whose subject is a component, run for a catalog instance */
  componentAnalyses: ComponentAnalysisDef[];
  packages: { qualifiedName: string; modelId: string | null; modelName: string | null; loc: Loc | null }[];
  tags: Record<string, string>;
  diagnostics: Diagnostic[];
}

export interface ComponentAnalysisDef extends Located {
  toolName: string;
  subject: string;
  subjectTypeId: string;
  inputs: { name: string; value: string }[];
  outputs: string[];
}

/** a component analysis run for one catalog instance */
export interface ComponentAnalysisResult {
  instance: string;
  component: string;
  analysis: string;
  toolName: string;
  runId: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  units: Record<string, string>;
  /** the instance's values of the outputs that are attributes of its component */
  current: Record<string, string | null>;
}

/** what propellerAero returns besides the fitted coefficients */
export interface PropellerAeroOutputs {
  ct0: number;
  ct1: number;
  ct2: number;
  cp0: number;
  cp1: number;
  cp2: number;
  thrustPerWatt: number | null;
  curve: { J: number; CT: number; CP: number; eta: number; converged: boolean }[];
  span: { rR: number; chord: number; twist: number; alpha: number; cl: number; cd: number; inflow: number; swirl: number; dT: number; dQ: number; loss: number }[];
  sections: {
    station: number; airfoil: string; reynolds: number; mach: number; source: string; converged: number; requested: number;
    alphaStall: number; clMax: number; cdMin: number; alpha: number[]; cl: number[]; cd: number[]; warning: string | null;
  }[];
  fitRange: [number, number];
  fitError: { CT: number; CP: number };
  staticThrust: number;
  staticPower: number;
  referenceRpm: number;
  figureOfMerit: number | null;
  warnings: string[];
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
  /** requirement value ("usage.attribute") -> number; omitted on a rewrite: keep the current ones */
  requirements?: Record<string, string>;
  /** component-typed part -> instance; omitted on a rewrite: keep the current ones */
  parts?: Record<string, string | null>;
}

export interface ConfigurationRequest {
  architecture: string;
  name: string;
  doc?: string;
  assignments: Record<string, string | null>;
  /** behavior slot -> behavior instance; every slot is required */
  behaviors?: Record<string, string | null>;
  package?: string | null;
  replace?: string | null;
  /** requirement value ("usage.attribute") -> number; omitted on a rewrite: keep the current ones */
  requirements?: Record<string, string>;
}

export interface StepRequest {
  behavior: string;
  values: Record<string, string>;
}

export interface MissionRequest {
  missionType: string;
  name: string;
  doc?: string;
  steps: StepRequest[];
  package?: string | null;
  replace?: string | null;
  /** requirement value ("usage.attribute") -> number; omitted on a rewrite: keep the current ones */
  requirements?: Record<string, string>;
}

export interface ComponentRequest {
  kind?: "part" | "action";
  name: string;
  package: string;
  specializes?: string | null;
  abstract?: boolean;
  doc?: string;
  attributes: { name: string; type: string }[];
  ports: { name: string; type: string }[];
  params?: { name: string; type: string }[];
  /** requirement usages typed by library requirement defs; values bind some attributes (e.g. actual = energy) */
  requirements?: { name: string; type: string; values: Record<string, string> }[];
}

/** a part of a generated scene (glTF node extras) */
export interface GeometryPart {
  name: string;
  material: string;
  slot: string;
  /** element of a multiple slot (0-based), null for a single one */
  index: number | null;
  type: string;
  instance: string | null;
  component: string | null;
  values: Record<string, string>;
}

/** what the geometry service generated for a system instance (glTF scene extras) */
export interface GeometryInfo {
  system: string;
  architecture: string;
  generator: "multirotor" | "generic";
  /** lengths in metres */
  metrics: Record<string, number | null>;
  warnings: string[];
  parts: GeometryPart[];
}

/** what the mission simulator (an external analysis) returns for one system */
export interface SimulationOutputs {
  /** Wh drawn from the battery */
  energyUsed: number;
  /** of the battery's total energy */
  energyPercent: number;
  energyDelivered: number;
  /** s (up to depletion) */
  missionTime: number;
  endStateOfCharge: number;
  reserveMargin: number;
  peakPower: number;
  averagePower: number;
  completed: boolean;
  feasible: boolean;
  limitingStep: string;
  issues: string[];
  flownSteps: { name: string; behavior: string; start: number; duration: number; power: number; mass: number; energy: number; startSoc: number; endSoc: number }[];
  timeline: { t: number; soc: number; power: number; step: string | null }[];
}

/** a propulsion operating point, time-averaged over a step or behavior (airborne only) */
export interface OperatingPoint {
  J: number;
  CT: number;
  CP: number;
  CQ: number;
  /** CT / CP */
  CTCP: number;
  propEfficiency: number;
  /** N of thrust per W of shaft power, per rotor */
  thrustPerPower: number;
  rpm: number;
  /** N·m per rotor */
  torque: number;
  /** N per rotor */
  thrust: number;
  /** W per rotor */
  shaftPower: number;
  /** A per motor */
  current: number;
  motorVoltage: number;
  motorEfficiency: number;
  /** motor voltage / bus voltage */
  throttle: number;
  /** degrees */
  tilt: number;
  speed: number;
}

/** energy and power of one behavior (or step) in the dynamic simulation */
export interface BehaviorDynamics {
  duration: number;
  /** Wh drawn from the pack */
  energy: number;
  averagePower: number;
  peakPower: number;
  maxThrottle: number;
  airborne: boolean;
  operatingPoint: OperatingPoint | null;
}

/** what the dynamic simulator (dynamicsSimulator) returns for one system */
export interface DynamicsOutputs extends Omit<SimulationOutputs, "flownSteps" | "timeline" | "energyDelivered"> {
  maxThrottle: number;
  distanceFlown: number;
  behaviors: (BehaviorDynamics & { behavior: string })[];
  flownSteps: (BehaviorDynamics & { name: string; behavior: string; start: number; mass: number; startSoc: number; endSoc: number })[];
  propellerCurves: { J: number; CT: number; CP: number; CQ: number; CTCP: number; efficiency: number }[];
  /** at full throttle and the battery's nominal voltage */
  motorCurves: { current: number; speed: number; torque: number; power: number; efficiency: number }[];
  reference: { hoverRpm: number; propDiameter: number; airDensity: number; batteryVoltage: number; motorMaxCurrent: number };
  timeline: { t: number; soc: number; power: number; speed: number; altitude: number; rpm: number; throttle: number; step: string | null }[];
}

export function isDynamics(o: SimulationOutputs | DynamicsOutputs | undefined): o is DynamicsOutputs {
  return !!o && Array.isArray((o as DynamicsOutputs).behaviors);
}

export interface SimulationRun {
  /** why the system cannot fly the mission */
  problems: string[];
  simulation?: string;
  toolName?: string;
  /** the simulation def's inputs, evaluated for the system */
  inputs?: Record<string, number | boolean | string | null>;
  outputs?: SimulationOutputs | DynamicsOutputs;
  units?: Record<string, string>;
  error?: string;
  /** requirements the simulation verifies -> verdict */
  verdicts?: Record<string, "holds" | "fails" | "error">;
  verification?: Record<string, Verification>;
}

export interface SimulationResult {
  mission: string;
  /** the simulation def asked for (null: the first that applies to each system) */
  simulation: string | null;
  /** the analysis service logs the simulator calls under this run id */
  runId: string;
  /** system instance -> result */
  systems: Record<string, SimulationRun>;
  errors: string[];
}

export interface SimulationRequest {
  missionType: string;
  /** the simulation def to run (null: the first that applies to each system) */
  simulation: string | null;
  mission: string | null;
  draft: StepRequest[] | null;
  draftRequirements?: Record<string, string> | null;
  systems: string[] | null;
}

export interface MissionSimulationDef extends Located {
  toolName: string;
  subject: string;
  subjectTypeId: string;
  /** in parameters with a value: evaluated with a system instance as subject */
  inputs: { name: string; value: string }[];
  /** out parameters: the results requirements are checked with */
  outputs: string[];
  /** requirement defs it verifies */
  verifies: string[];
}
