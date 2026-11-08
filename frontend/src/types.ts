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
  parts: { name: string; type: string | null; multiplicity: string | null }[];
  instances: string[];
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
  /** requirement defs (with in parameters) that a system simulation verifies */
  systemRequirements: string[];
  /** simulations of the system on its own (#SystemSimulation): verification and tuning */
  systemSimulations: string[];
  /** mission simulations this architecture can be the subject of */
  simulations: string[];
}

export interface CatalogRequirement extends Located {
  subject: string;
  subjectTypeId: string;
  /** names of its in parameters (mission requirements have some) */
  params: string[];
}

/** a #Mission action def; each attribute is a total over the steps */
export interface MissionType extends Located {
  isAbstract: boolean;
  specializes: string[];
  totals: CatalogAttribute[];
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
  /** behavior slot -> behavior instance; every slot is required */
  behaviors?: Record<string, string | null>;
  package?: string | null;
  replace?: string | null;
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
  /** the stored result (GET /api/models/results/{id}) */
  resultId?: string;
  /** true when the outputs came from a stored result with the same inputs (the simulator was not called) */
  reused?: boolean;
  /** the project commit that was simulated */
  commitId?: string | null;
  toolVersion?: string | null;
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
  systems: string[] | null;
}

export interface MissionSimulationDef extends Located {
  /** mission: flies missions' steps; system: the system on its own (#SystemSimulation) */
  scope: "mission" | "system";
  /** verification def (verifies requirements) or analysis def (e.g. tuning) */
  kind: string;
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

/** a simulation result in the results store (outside the SysML v2 repository) */
export interface StoredResult {
  id: string;
  runId: string;
  projectId: string;
  commitId: string | null;
  missionType: string;
  /** qualified name, or "draft" */
  mission: string;
  system: string;
  simulation: string;
  toolName: string;
  toolVersion: string | null;
  inputHash: string;
  /** without timeline, steps and curves in listings */
  outputs: Record<string, unknown> | null;
  units: Record<string, string>;
  verdicts: Record<string, "holds" | "fails" | "error">;
  verification: Record<string, Verification>;
  error: string | null;
  reusedFrom: string | null;
  createdAt: string;
}

/** a result recorded as verification evidence in the model */
export interface EvidenceRecord extends Located {
  simulation: string;
  /** the system instance the simulation's subject is bound to */
  system: string | null;
  /** SimulationEvidence metadata: resultId, uri, commitId, mission, verdict, verdicts, recordedAt */
  values: Record<string, string>;
  /** current: the newest result for this mission, system and simulation has the same inputs, simulator version and verdicts */
  status: "current" | "outdated" | "missing";
  result: StoredResult | null;
  latestResultId: string | null;
}

/** a scenario flown by the control simulation */
export interface ControlScenario {
  name: string;
  behavior: string;
  parameter: string;
  command: number;
  quantity: string;
  unit: string;
  riseTime: number | null;
  settlingTime: number | null;
  overshoot: number | null;
  finalError: number;
  maxDeviation?: number;
  maxTrackingError?: number;
  maxTilt: number;
  maxThrottle: number;
  saturation: number;
  series: { t: number; reference: number; actual: number; [k: string]: number }[];
}

export interface LoopMargin {
  phaseMargin: number | null;
  gainMargin: number | null;
  crossover: number | null;
}

/** what the control simulator (controlSimulator) returns */
export interface ControlOutputs {
  mode: "tune" | "verify";
  gains: Record<string, number | boolean>;
  mixer: { rotors: { index: number; angle: number; x: number; y: number; spin: "cw" | "ccw" }[]; allocation: number[][]; mixer: number[][]; yawPerThrust: number };
  hover: { thrustPerRotor: number; maxThrustPerRotor: number; rpm: number; maxRpm: number; throttle: number };
  identification: {
    rotor: { gain: number; timeConstant: number; step: number; hoverRpm: number; test: { t: number; measured: number; model: number }[] };
    roll: { b: number; inertiaEstimate: number | null; lag: number; torque: number; test: { t: number; measured: number; model: number }[] };
    pitch: { b: number; inertiaEstimate: number | null; lag: number; torque: number; test: { t: number; measured: number; model: number }[] };
    yaw: { b: number; inertiaEstimate: number | null; lag: number; torque: number; test: { t: number; measured: number; model: number }[] };
  };
  margins: Record<string, LoopMargin>;
  phaseMargin: number | null;
  gainMargin: number | null;
  thrustMargin: number;
  yawAuthority: number;
  saturation: number;
  climbSettlingTime: number | null;
  speedSettlingTime: number | null;
  speedOvershoot: number | null;
  headingSettlingTime: number | null;
  gustDeviation: number | null;
  completed: boolean;
  issues: string[];
  scenarios: ControlScenario[];
  [gain: string]: unknown;
}

/** one system simulation of one system instance */
export interface SystemRun {
  simulation: string;
  toolName: string;
  toolVersion: string | null;
  inputs: Record<string, unknown>;
  outputs?: ControlOutputs;
  units?: Record<string, string>;
  error?: string;
  verdicts: Record<string, "holds" | "fails" | "error">;
  verification: Record<string, Verification>;
  resultId?: string;
  reused?: boolean;
  commitId?: string | null;
}

export interface SystemSimulationResult {
  runId: string;
  /** system instance -> simulation def -> run */
  systems: Record<string, Record<string, SystemRun>>;
  errors: string[];
}

export interface TuneResult {
  runId: string;
  system: string;
  simulation: string;
  result: SystemRun;
  errors: string[];
  /** the analysis' out parameters: proposals for the attributes of the component in ``slot`` */
  proposals: Record<string, number>;
  slot: string | null;
  component: string | null;
  current: { qualifiedName: string; values: Record<string, string> } | null;
}
