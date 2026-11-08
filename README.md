# SysML v2 Studio: microservices platform

This project lets you write a SysML v2 model as text in the browser and see it drawn as diagrams while you type. It stores every version in PostgreSQL, in a repository that any SysML v2 API client can read and write through the standard **SysML v2 API** (OMG Systems Modeling API and Services). It runs the model with the **OpenMBEE OpenSysML** interpreter. While a model runs, its calcs and actions can call analyses over a REST API.

```mermaid
flowchart LR
  subgraph Browser
    UI["React UI<br/>editor · live diagrams · requirements · execution · 3-D"]
  end
  UI -->|/api/*| GW["nginx gateway<br/>(frontend container)"]
  GW -->|/api/models| MS["model service :8001<br/>projects · versions · elements · runs"]
  GW -->|/api/sysml-v2| MS
  CL["SysML v2 API clients<br/>(Jupyter %publish, tools)"] -->|/sysml-v2| MS
  GW -->|/api/analysis| AS["analysis service :8003<br/>registry · REST invoke · call log"]
  GW -->|/api/interpreter| IS["interpreter service :8002<br/>OpenSysML wrapper"]
  GW -->|/api/geometry| GS["geometry service :8004<br/>glTF of system instances"]
  GS -->|catalog| MS
  MS -->|parse / execute| IS
  IS -->|spawns| OS[["OpenSysML sysml CLI<br/>(Go, SysML v2 + KerML)"]]
  OS -->|ToolExecution → bridge.py| AS
  AS -->|kind=external| SIM["mission simulator :8011<br/>(missionSimulator, dynamicsSimulator,<br/>componentCurves, controlSimulator)"]
  MS -->|invoke missionSimulator| AS
  MS --- PG[(PostgreSQL<br/>schemas: models, analysis)]
  AS --- PG
```

| Service | Port | Responsibility |
|---|---|---|
| `frontend` | 8080 | React UI (Vite build) served by nginx. nginx also routes `/api/models`, `/api/sysml-v2`, `/api/interpreter`, `/api/analysis` and `/api/geometry` to the services. |
| `model` | 8001 | Projects and models. Every save is a new, unchangeable version, stored as a commit of the project's SysML v2 API repository. The service serves that repository through the standard SysML v2 API at `/sysml-v2`. Also records runs and serves requirement traceability. |
| `interpreter` | 8002 | Wraps the OpenSysML binary. Endpoints: validate/parse → diagnostics + diagram graph, execute (calc, analysis case, requirement, constraint, satisfy, action, state machine, expression), and OpenSysML's own rendering (Mermaid, DOT, PlantUML, D2). |
| `analysis` | 8003 | Registry of analyses (`builtin` Python functions, `formula`, `external` REST endpoints). `POST /analyses/{name}/invoke` runs one. Every call is logged together with the id of the run that made it. |
| `geometry` | 8004 | Generates 3-D geometry of a system instance as binary glTF 2.0, from the component instances in its slots and their attribute values. It reads the project's catalog from the model service, so it stores nothing. |
| `mission-sim` | 8011 | Mission simulator, an external analysis registered as `missionSimulator`. It flies a mission step by step and returns the battery energy drawn, its share of the pack, the mission time, the state of charge over time, and whether the reserve holds. The same service is also a second external analysis, `dynamicsSimulator` (`POST /dynamics`). It flies the mission through the propulsion chain and returns the energy and power of each behavior, the propeller and motor performance curves, and each behavior's operating point on them. A third, `componentCurves` (`POST /curves`), returns the performance curves of one propeller or motor from its attribute values, using the same component models. A fourth, `controlSimulator` (`POST /control`), simulates the drone's closed-loop control (a command mixer, cascaded PID loops down to each ESC, rotor dynamics, reaction torques and inertia), tunes the gains from open-loop tests and verifies the control requirements. |
| `postgres` | 5432 | PostgreSQL 16. The model and analysis services each own a schema. |

## Quick start

```bash
docker compose up --build
# open http://localhost:8080
```

On first start the model service creates a demo project, **Delivery Drones**. It is a configurable parcel delivery drone. Its library ([`examples/drone_library.sysml`](examples/drone_library.sysml)) has frames from 450 to 1600 mm, six batteries, four motors, four propellers, and parcel boxes and winches carrying 1 to 15 kg. The quad, hexa and octo architectures ([`examples/drone_architectures.sysml`](examples/drone_architectures.sysml)) set the number of rotors through the multiplicity of the `motors` and `propellers` slots (`part :>> motors[6];`). Each architecture derives takeoff mass, thrust-to-weight, hover power, endurance and propeller-tip clearance from the parts chosen. The requirements ([`examples/drone_requirements.sysml`](examples/drone_requirements.sysml)) are a specification: abstract requirement types with parameters, and a hierarchy of statements holding instances of them, for systems and components. Of the six configured drones, `parcel`, `regional` and `heavyLift` pass every check. `courier`, `parcelLongRange` and `regionalLiIon` each fail one or two, for different reasons. The library also declares abstract behaviors with parameters: takeoff, cruise, hover, winch, landing and drop-off. Its behavior catalog holds performance profiles, such as `cruise12` (12 m/s, power factor 1.1). Every architecture performs takeoff, cruise, hover, land and release; the Octocopter also performs winch lowering. Each drone performs a profile for each of them. The four delivery missions ([`examples/drone_missions.sysml`](examples/drone_missions.sysml)) are ordered lists of behaviors with distances and altitudes. Each drone flies a mission with its own profiles. The mission simulator verifies the energy (20 % reserve) and 30-minute time requirements. A design-time endurance estimate is checked from the mission totals. All six drones can fly `shortHop`. Only `parcelLongRange` has the battery for `rural`. Only `heavyLift` can fly `winchDrop`, because it is the only drone that performs winch lowering.

Things to try in the UI. The left sidebar opens each panel; **«** collapses it to icons. Its **Editor** item shows or minimizes the text editor. The **☀ ◐ ☾** switch in the top bar picks a light or dark theme, or follows the system setting (the default). The browser remembers the choice.

1. **Edit the text.** The model is re-interpreted about 0.5 s after you stop typing. Diagnostics appear in the gutter and the problems list, and the diagrams update. You can switch between four views: Structure, Interconnection (with ports and connections), Requirements (with satisfy relations and verdicts) and Behavior (actions and states). The editor is a modeless window floating over the workspace, so the diagram and the other panels stay usable while it is open. Drag its title bar to move it, drag the bottom-right corner to resize it, and double-click the title bar or press **–** to minimize it. **⇤** docks it beside the workspace and **⧉ Float** floats it again. The browser remembers its position and size.
2. **Click a box, a compartment line or a port.** The editor selects that element's source. Moving the cursor in the editor selects the element in the diagram.
3. **Requirements panel.** Capture a requirement with the form; the SysML text is inserted for you. **Check all satisfy relations** runs every `satisfy`, calling any REST analyses the satisfied attributes depend on.
4. **Execute panel.** Run an action or a state machine to play back the execution trace in both the diagram and the editor. A run that calls REST analyses lists every call it made.
5. **Analyses panel.** Register an external endpoint or a formula. **Insert SysML binding** generates a `calc def` with the `ToolExecution` metadata that binds to it.
6. **Library panel.** Browse the component types, the behaviors and their catalog instances. **+ New instance** creates an instance from a form with one field per attribute, or one per parameter for a behavior. A behavior instance is incomplete until every parameter is set (see `cruiseSport`). You can also edit or delete instances and declare new components and behaviors. A component whose attributes are the parameters of a dynamics model shows its performance curves under **Performance curves**, computed by the `componentCurves` analysis. Propellers need `ct0`…`cp2` and are charted against advance ratio. Motors need `kv`, `resistance`, `noLoadCurrent` and `maxCurrent`, and are charted at full throttle on a battery you pick (by default one a system pairs with the motor). Pick an instance to chart it. While you edit or create an instance, the charts follow the form's values.
7. **Configurator panel.** Pick a system architecture. Fill each part slot with a compatible catalog instance, and choose a complete behavior instance for every behavior the architecture performs. Then create the system instance. **Evaluate & check requirements** computes every system instance's derived attributes and checks each applicable requirement def against it, in one run. Under **Controllability**, **▶ Simulate controllability** flies each system's behaviors closed-loop with its flight controller and checks the control requirements. Select a system and **Tune controller** to identify its dynamics from open-loop tests and get proposed gains, then **Save & assign** them as a FlightController instance in the system's `controller` slot.
8. **Specification panel.** The requirement hierarchy as a tree, with IDs, types and parameter values, against every system instance, or every component instance under **Components**. Dots show where each requirement applies. **▶ Check requirements** evaluates every instance where it applies and rolls the verdicts up through the statements. Select a requirement to see its text, type, constraint, parameters and what it applies to.
9. **Missions panel.** Build a mission by clicking behaviors in the order they are performed, then set each step's quantities (distance, altitude…). Reorder steps with ↑ ↓ and change a step's behavior from its drop-down. **Evaluate & check missions** flies every mission with every drone and checks the mission requirements. The mission being edited then shows each drone's verdict, plus the step durations and a timeline for the drone you pick under **Flown by**. **▶ Simulate** runs the mission simulator for the mission being edited (saved or draft) with every drone. It shows a chart of battery charge over time with the reserve line, and a table of energy used (Wh and percent of the battery), mission time, charge left, and peak power against the battery's maximum. When more than one simulation applies, pick one beside the title. **DynamicSimulation** also shows:
   - the energy, average power and peak power of each behavior, for every drone;
   - electrical power over time;
   - for the drone you pick, small-multiple charts of the propeller's torque coefficient, thrust/power (CT/CP) and efficiency against advance ratio, and of the motor's torque, speed, shaft power and efficiency against current. Each behavior's operating point is marked on every chart, and hovering one shows its rpm, thrust, torque, current, throttle and efficiencies.
10. **3-D panel.** Pick a system instance to see its generated geometry. Drag to orbit, scroll to zoom, and click a part to see its component instance (**Show in editor** opens its text). The exploded slider pulls the parts apart. The panel also shows the dimensions and the propeller tip clearance. Overlapping propeller discs are drawn red. The geometry is generated again after every change, so swapping a frame in the Configurator changes the drone on screen.

## Component library and system configuration

Everything is plain SysML v2 text in the project's models. The Library and Configurator panels find components and architectures by two metadata tags, and they edit the text for you:

```sysml
metadata def Component;
metadata def SystemArchitecture;

#Component part def Engine {                  // a component: a tagged part def
    attribute mass : MassValue;
    port torqueOut : TorquePort;
}
abstract #Component part def Transmission { attribute mass : MassValue; }
#Component part def ManualTransmission :> Transmission { attribute gears : Integer; }

package Catalog {
    part i4Engine : Engine { attribute :>> mass = 140 [SI::kg]; }   // a component instance
}

#SystemArchitecture part def CombustionVehicle {    // a system architecture
    part engine : Engine;                           // slots: part usages typed by part defs
    part transmission : Transmission;               // accepts instances of any specialization
    attribute mass : MassValue = engine.mass + transmission.mass + 1150 [SI::kg];
}

package Configurations {
    part economy : CombustionVehicle {                 // a system instance
        part :>> engine :> Catalog::i4Engine;          // each slot subsets a catalog instance,
        part :>> transmission :> Catalog::manual6;     // so it inherits that instance's values
    }
}
```

- A part def is a component if it, or a part def it specializes, has the `#Component` tag. Architectures work the same way with `#SystemArchitecture`.
- A component instance is a part usage declared directly in a package and typed by a component. Each system instance is a part usage typed by an architecture.
- A slot accepts the instances of its type and of every type that specializes it.
- The catalog is built from a single parse of all the project's models, so the library and the architectures can live in different models. Use the **Component library** and **System architecture** templates when you create a model.
- Every change saves a new version of the model that declares the element. The service refuses a change that would add an error, for example a value with an unknown unit, or deleting an instance that a system instance still uses.
- Evaluation is a single `evaluate` run per model. It uses `system.attribute` for each derived attribute and `Requirement(system)` for each requirement def whose subject type the architecture conforms to. A new system instance gets verdicts without any `satisfy` statements.

### Behaviors and missions

Missions use two more metadata tags, `#Behavior` and `#Mission`:

```sysml
abstract #Behavior action def Cruise :> DroneBehavior {   // an abstract behavior
    in :>> powerFactor;                         // restate inherited parameters first (see below)
    in speed : Real;                            // a parameter: set by the system that performs it
    attribute :>> distance;                     // a quantity (no value): set by each mission step
    attribute :>> duration = distance / speed;  // derived
}
package Behaviors {
    action cruise12 : Cruise { in :>> powerFactor = 1.1; in :>> speed = 12; }   // a behavior instance
}

#SystemArchitecture part def Quadcopter :> DeliveryDrone {
    perform action cruise : Cruise;             // a behavior slot
}
part parcel : Quadcopter {
    perform action :>> cruise :> Behaviors::cruise12;   // every behavior slot is required
}

#Mission action def DeliveryMission {           // a mission type
    attribute duration : Real;                  // totals: summed over the steps
    attribute hoverTime : Real;
}
requirement def MissionEnduranceEstimate {      // a mission requirement checked from the totals
    subject drone : DeliveryDrone;
    in hoverTime : Real;                        // named like a total
    require constraint { hoverTime <= drone.endurance * 60 }
}
package Missions {
    action shortHop : DeliveryMission {         // a mission: ordered steps,
        first start;                            // each an abstract behavior with its quantities set
        then action takeoff : Takeoff { attribute :>> altitude = 30; }
        then action cruise : Cruise { attribute :>> distance = 2000; }
        then done;
    }
}
```

- A behavior's `in` parameters describe how a system performs it. Its attributes without a value are quantities that each mission step sets. Its other attributes are derived.
- A behavior instance is complete when every parameter has a value. Only complete instances can be performed. The service refuses a system instance that leaves a behavior slot empty or chooses an incomplete instance.
- In SysML v2, a specialized behavior's own parameters redefine the inherited ones *by position*. A behavior that adds parameters therefore restates the inherited ones first (`in :>> powerFactor;`). **+ New behavior** writes those lines for you.
- To fly a step, a system instance uses the behavior it performs for the step's behavior type. That is the slot whose type is the step's behavior or a behavior it specializes. The flown step is an action that subsets both the performed behavior and the step, `action f1 :> Configurations::parcel.cruise, Missions::shortHop.cruise;`. It takes its parameters from the performed behavior and its quantities from the step, so its derived attributes evaluate with both. If the system performs no behavior of a step's type, it cannot fly the mission.
- A mission's totals are the sums of the same-named attributes over the flown steps, so they depend on the system flying it. Missions on their own cannot run in the Execute panel, because their steps have no parameters until a system flies them.
- A mission requirement is a requirement def with `in` parameters. It applies to every system instance whose architecture conforms to its subject type, and is checked as `Requirement(system, value, …)`. Where the values come from depends on how it is verified (below). The Configurator leaves mission requirements out of its own checks.
- A **mission simulation** is a def bound to an external analysis by `ToolExecution` metadata, with a system as its subject. Its `in` parameters with a value say what the analysis receives from the system. Its `out` parameters name the results the requirements are checked with. As a `verification def`, its objective names the requirements it verifies:

  ```sysml
  requirement def MissionEnergyRequirement {
      subject drone : DeliveryDrone;
      in energyPercent : Real;                                  // a simulation result
      attribute reserve : Real = 0.2;
      require constraint { energyPercent <= 100 * (1 - reserve) }
  }
  verification def MissionSimulation {
      metadata ToolExecution { toolName = "missionSimulator"; uri = "analysis://missionSimulator"; }
      subject drone : DeliveryDrone;
      in hoverPower : Real = drone.hoverPower;                  // W at takeoff mass
      in payloadMass : Real = drone.payloadCapacity / (1 [SI::kg]);
      in batteryEnergy : Real = drone.battery.energy;           // Wh
      in reserve : Real = 0.2;
      ...
      out energyPercent : Real;                                 // results, by name
      out missionTime : Real;
      objective {
          verify requirement energy : MissionEnergyRequirement;
          verify requirement time : MissionTimeRequirement;
      }
  }
  ```

- **How a mission requirement is verified, for each system instance:**
  - **By simulation**, when a mission simulation applies to the system's architecture, verifies the requirement, and has an `out` parameter for each of the requirement's parameters. The requirement is checked with the simulator's results, for example `MissionEnergyRequirement(parcel, 75.2)`. If the simulated flight does not complete (the battery empties), every requirement it verifies fails.
  - **By evaluation**, otherwise, when each parameter is named like a total of the mission type. The requirement is checked with the totals summed over the flown steps.
  - **Not at all**, if neither applies.

  **Evaluate & check missions** reports the verdict and the method for each requirement and drone. The **Verifies** column of the simulation table shows the requirements the simulation verified.

  `POST /projects/{id}/catalog/missions/simulate` flies the mission with every system instance the simulation applies to. It evaluates those parameters with the system as subject, and evaluates every attribute and parameter of each flown step (`duration`, `powerFactor`, `releasesPayload`…). It then calls the analysis through the analysis service, which logs each call under the returned run id. The simulator integrates power over time. Power follows the mass, so the drone is lighter after a step that `releasesPayload`, and the battery delivers it with a loss that grows with load. As a result, it can differ from the totals-based `MissionEnduranceEstimate`, which assumes full mass throughout. On `suburban`, `parcel`, `regional` and `heavyLift` fail the estimate but meet the simulated `MissionEnergyRequirement`, because the return leg is flown without the parcel.
- **Several simulations.** A project can have more than one simulation def. Evaluation runs every simulation that verifies one of a drone's requirements and reports each under `simulations[def]`. The simulate endpoint runs the one named in `simulation`, or by default the first that applies.
- **The dynamic simulation** ([`services/mission-sim/app/dynamics.py`](services/mission-sim/app/dynamics.py), `DroneMissions::DynamicSimulation`) assumes still air and a constant air density. It flies each step with a 0.2 s time step:
  - **Vehicle.** A point mass follows the step's `groundSpeed` and `verticalSpeed`, accelerating at no more than 2 m/s². The rotors carry its weight, its drag `½ ρ CdA v²` and the inertia of each acceleration, and the drone tilts to point the thrust that way.
  - **Propeller.** Each rotor's speed is solved from its curves `CT(J) = ct0 + ct1 J + ct2 J²` and `CP(J) = cp0 + cp1 J + cp2 J²`. J is the advance ratio, from the airspeed along the rotor axis. Torque is `CP ρ n² D⁵ / 2π` and efficiency is `J CT / CP`.
  - **Motor.** A brushless DC model with `kv`, winding `resistance` and `noLoadCurrent` gives the current, voltage, efficiency and throttle.
  - **Battery.** The voltage falls with the state of charge and sags under load.
  - **Ground steps.** A step that is not `airborne` draws only the avionics.

  The propeller, motor and frame catalogs carry these parameters, and the behaviors set the speeds (for example `Cruise` sets `groundSpeed = speed`). The simulation verifies `MissionPeakPowerRequirement` (peak electrical power, transients included, within the battery's maximum) and `ThrottleHeadroomRequirement` (at most 80 % throttle). `regionalLiIon` fails the throttle requirement at 82 %, because its 21.6 V pack sags under load. Descent is treated as hover inflow (no vortex ring state), and edgewise rotor effects are not modelled.
- **Evaluate & check missions** calls `POST /projects/{id}/catalog/missions/evaluate`. The service writes the flown steps of every mission and drone into a scratch package. One evaluate run gives the step totals, the totals-based verdicts and the simulation inputs. The simulations then run through the analysis service, and a second evaluate run checks the requirements they verify. Nothing is stored except the analysis service's call log.

## Geometry

The geometry service (`services/geometry`) turns a system instance into a glTF 2.0 scene. Each part is one mesh. Its glTF node `extras` hold the slot, the element index of a multiple slot, and the component instance with its values. The scene `extras` hold the generator, the metrics (in metres) and any warnings. The service picks a generator from the component types in the architecture's slots:

- **multirotor**: the architecture has slots typed by `Frame`, `Motor` and `Propeller`, or by specializations of them. One arm, motor and propeller is drawn per element of the motor slot (`part :>> motors[6];`), on a circle of the frame's `wheelbase` (mm) in an X layout. Propellers have the propeller's `diameter` (in). Motors are sized from their `mass`. The battery (from its `mass`) sits on the hub. The payload hangs between the landing skids: a box of its `volume` (l), or a winch drum with a tether if it has a `tetherLength`. The service reports the tip clearance between adjacent propellers, the same quantity as the architecture's `propClearance`.
- **generic**: any other architecture. Each slot element is a box sized by its `mass` and laid out in a grid. An open slot is drawn as a grey placeholder.

The service is plain Python with no geometry libraries: `app/gltf.py` builds the meshes and writes GLB. To support another kind of system, add a generator to `app/generators.py` and choose it in `build()`.

## How a model calls REST analyses during execution

OpenSysML can hand a calc or action to an outside program instead of running its SysML body. You mark it with `AnalysisTooling::ToolExecution` metadata:

```sysml
calc def MassRollup {
    metadata ToolExecution { toolName = "massRollup"; uri = "analysis://massRollup"; }
    in engineMass : MassValue[1]       { @ToolVariable { name = "engineMass"; } }
    in tankMass : MassValue[1]         { @ToolVariable { name = "tankMass"; } }
    in transmissionMass : MassValue[1] { @ToolVariable { name = "transmissionMass"; } }
    return total : MassValue[1]        { @ToolVariable { name = "total"; } }
}

part sedan : Vehicle {
    attribute :>> mass = MassRollup(engine.mass, tank.mass, transmission.mass) + 1100 [SI::kg];
}
satisfy sedanMass by sedan;   // evaluating this calls the REST analysis
```

What happens on every execution:

1. The interpreter service reads the analysis registry (`GET /analyses`). For each enabled analysis it writes an OpenSysML **tool manifest** into a fresh directory (permissions 0700) and passes that directory to the binary in `OPENSYSML_TOOLS`.
2. Each manifest runs [`bridge.py`](services/interpreter/app/bridge.py). OpenSysML sends the bridge `{"toolName", "uri", "inputs": {"engineMass": {"value": 180, "unit": "kg"}, …}}` on stdin.
3. The bridge POSTs that request, plus the run id, to `analysis:8003/analyses/{toolName}/invoke` and writes the reply `{"outputs": {"total": {"value": 315, "unit": "kg"}}}` back to OpenSysML.
4. The analysis service runs the builtin or formula itself, or forwards the call to the registered external URL. It converts units and logs the call.

The URL of an external endpoint comes from the registry, not from the model text, so a model cannot make the platform call arbitrary hosts.

An external endpoint receives `POST {"toolName", "uri", "inputs", "runId"}` and must return either `{"outputs": {name: {"value", "unit"?}}}` or `{"error": "..."}`. See [`services/mission-sim/app/main.py`](services/mission-sim/app/main.py), the mission simulator, registered as `missionSimulator`.

To add a Python analysis, decorate a function in [`services/analysis/app/builtins.py`](services/analysis/app/builtins.py):

```python
@analysis("stressCheck", "Von Mises stress", inputs=[{"name": "force", "unit": "N"}, {"name": "area", "unit": "m**2"}],
          outputs=[{"name": "stress"}])
def stress_check(v):
    return {"stress": v["force"] / v["area"]}
```

## REST API (summary)

Interactive OpenAPI docs are served at `/docs` on each service, e.g. http://localhost:8001/docs.

**Geometry service**
- `GET /projects/{id}/geometry.glb?system=QN`: the system instance's scene as binary glTF (`model/gltf-binary`)
- `GET /projects/{id}/geometry?system=QN`: the same description as JSON, without the meshes (generator, metrics, warnings, parts)

**Model service**
- `GET/POST /projects`, `DELETE /projects/{id}`
- `GET/POST /projects/{id}/models`: create from `source`, or from the `empty`, `library` or `architecture` template
- `GET /models/{id}[?version=n]`: source, diagnostics, graph
- `POST /models/{id}/preview`: interpret unsaved text; nothing is stored
- `PUT /models/{id}/source`: save a new version (interpreted and stored)
- `GET /models/{id}/versions`, `GET /models/{id}/elements?metatype=PartUsage&q=engine`
- `GET /models/{id}/requirements`: text, constraints, subject, satisfied-by, latest verdict
- `POST /models/{id}/runs`: `{"kind": "calc|analysis|requirement|constraint|satisfy|action|state|evaluate|validate", "target", "args", "subject", "expression", "advance"}`
- `GET /models/{id}/runs`, `GET /runs/{id}` (includes the REST calls made during the run)
- `GET /projects/{id}/catalog`: components and behaviors, instances, architectures (slots with compatible instances, applicable requirements and mission requirements), system instances, mission types, missions
- `POST /projects/{id}/catalog/instances` `{"component", "name", "values": {attr: expr}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/configurations` `{"architecture", "name", "assignments": {slot: instance}, "behaviors": {behavior slot: behavior instance}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/components` `{"name", "package", "kind"?: "part" | "action", "specializes"?, "abstract"?, "attributes": [{name, type}], "ports": [...], "params"?: [...]}`
- `POST /projects/{id}/catalog/missions` `{"missionType", "name", "steps": [{"behavior", "values": {quantity: expr}}], "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/missions/simulate` `{"missionType", "simulation"?: simulation def, "mission"? | "draft"?: [steps], "systems"?: [system instance]}`: the mission simulated for each system instance (simulation inputs, the analysis' outputs, or why it cannot be flown) and the run id the analysis service logged the calls under. Each simulation also gives its stored `resultId`, the `commitId` simulated, and `reused` when the outputs came from a stored result with the same inputs.
- `POST /projects/{id}/requirements/check`: every requirement instance of the specification, checked for each system or component instance it applies to (`verdicts`), and statements rolled up (`rollup`)
- `POST /projects/{id}/catalog/configurations/simulate` `{"architecture"? | "systems"?: [system instance], "simulation"?}`: run the system simulations (`#SystemSimulation` verification defs) of system instances and check the requirements they verify
- `POST /projects/{id}/catalog/configurations/tune` `{"system", "simulation"?}`: run a tuning analysis; its outputs come back as `proposals` for the component in the system's matching slot (`slot`, `component`, `current`)
- `GET /projects/{id}/results?mission=&system=&limit=`: stored simulation results, newest first, without inputs, timelines or curves
- `GET /results/{id}`: one stored result: everything sent to the simulator, everything it returned, and the verdicts
- `GET /projects/{id}/evidence`: simulation evidence recorded in the project's models, each with its stored result and whether it is still `current`
- `POST /results/{id}/evidence` `{"tag"?: bool}`: record a stored result as verification evidence in the `VerificationEvidence` model, replacing the evidence of the same mission, system and simulation; with `tag`, also tag the simulated commit `verified/<mission>/<system>/<result>`
- `POST /projects/{id}/catalog/missions/evaluate` `{"missionType", "draft"?: [steps]}`: every mission (and the draft) flown by every system instance that performs behaviors: per-step values, totals, mission requirement verdicts with how each was verified (simulation or evaluation) and the values used, or why it cannot be flown
- `DELETE /projects/{id}/catalog/elements?qualifiedName=`

**SysML v2 API** (Systems Modeling API and Services 1.0, REST/HTTP PSM): base URL `http://localhost:8001/sysml-v2`, or `http://localhost:8080/api/sysml-v2` through the gateway
- `GET/POST /projects`, `GET/PUT/DELETE /projects/{projectId}`
- `GET/POST /projects/{projectId}/branches`, `GET/DELETE …/branches/{branchId}`, `POST …/branches/{targetBranchId}/merge?sourceCommitId=`
- `GET/POST /projects/{projectId}/tags`, `GET/DELETE …/tags/{tagId}`
- `GET/POST /projects/{projectId}/commits[?branchId=]`, `GET …/commits/{commitId}`, `…/changes`, `…/changes/{changeId}`, `…/commits/{compareCommitId}/diff?baseCommitId=`
- `GET …/commits/{commitId}/elements`, `…/elements/{elementId}`, `…/elements/{elementId}/relationships?direction=in|out|both`, `…/roots`
- `GET/POST /projects/{projectId}/queries`, `GET/PUT/DELETE …/queries/{queryId}`, `GET …/queries/{queryId}/results[?commitId=]`, `GET/POST /projects/{projectId}/query-results[?commitId=]`
- Collections are paged with `page[size]` (default 100), `page[after]` and `page[before]`. The next and previous pages are given in the `Link` header. Errors are `{"@type": "Error", "description": …}`.

**Interpreter service** (stateless; send the files directly)
- `POST /parse` `{"files": [{"name", "text"}], "includeElements": false}`
- `POST /execute` `{"files", "kind", "target", ...}`
- `POST /render` `{"files", "view": "#interconnection:Pkg::Vehicle", "form": "mermaid"}`
- `GET /engines`: OpenSysML engines and registered tools

**Analysis service**
- `GET/POST /analyses`, `GET/PUT/PATCH(?enabled=)/DELETE /analyses/{name}`
- `POST /analyses/{name}/invoke` `{"inputs": {...}, "uri"?, "runId"?}`
- `GET /invocations?runId=&analysis=&limit=`

## Requirements: types, instances and the specification

Requirements are organised in three kinds, all plain SysML v2 (`DroneRequirements`):

- **Requirement types** are abstract, parameterised requirement defs on an abstract system or component type, their subject. Attributes are **parameters**: the targets and limits an instance sets. `in` parameters are **measured values** that a verification supplies, such as a simulation result or a mission total.
- **Requirement instances** are requirement usages typed by a requirement type, with their parameters set, an ID and their own text.
- **Statements** are requirement usages with only an ID and a doc. They hold more specific statements or instances, which makes the hierarchy.

```sysml
abstract requirement def ThrustToWeight {
    subject drone : DeliveryDrone;
    attribute minRatio : Real;                      // a parameter
    require constraint { drone.thrustToWeight >= minRatio }
}
abstract requirement def MotorEfficiency {           // on an abstract component
    subject motor : Motor;
    attribute minimum : Real;
    require constraint { motor.efficiency >= minimum }
}
package Specification {
    requirement <'STK-1'> deliverParcels {
        doc /* The drone shall deliver parcels of up to 10 kg safely and reliably. */
        requirement <'SYS-1'> flightPerformance {
            doc /* The drone shall have the lift and endurance a delivery flight needs. */
            requirement <'SYS-1.1'> liftMargin : Types::ThrustToWeight { attribute :>> minRatio = 2.0; }
            requirement <'SYS-1.3'> heavyLiftMargin : Types::ThrustToWeight { attribute :>> minRatio = 2.5; }
        }
        requirement <'CMP-1'> componentQuality {
            doc /* Components shall meet the procurement specification. */
            requirement <'CMP-1.1'> motorEfficiency : Types::MotorEfficiency { attribute :>> minimum = 0.82; }
        }
    }
    satisfy deliverParcels.flightPerformance.heavyLiftMargin by DroneArchitectures::Configurations::heavyLift;
}
```

- **Applicability.** An instance applies to the system or component instances its `satisfy` relations name. With none, it applies to every system instance (or component instance) its subject type fits. Above, `liftMargin` applies to every drone, `heavyLiftMargin` only to heavyLift, and `motorEfficiency` to every motor in the catalog.
- **Checking.** `POST /projects/{id}/requirements/check` evaluates each instance as `Instance(subject)` wherever it applies, in one run. Instances whose types need measured values are left to the simulations that verify them (◇). A statement holds for a subject when every instance under it that applies does.
- **Where verdicts show.** The Specification panel shows the hierarchy against systems or components. The Configurator's comparison shows the system-level instances that apply to the architecture, with "n/a" where an instance's `satisfy` relations name other systems.
- **Requirement IDs** are SysML short names (`<'SYS-1.1'>`).
- **In the demo**, courier and parcelLongRange fail the lift margin, courier also fails hover endurance, regionalLiIon fails battery power, and heavyLift fails its stricter heavy-lift margin. Among components, the m2814 motor (80 %) fails motor efficiency and the Li-ion pack (4.5 C) fails battery discharge.
- **Not yet migrated.** Mission requirements (`DroneMissions`) and the control requirements and tests (`DroneControl`) are still requirement defs checked by their simulations. Phases 2 and 3 move them to instances: missions as use cases whose objectives hold mission requirements, applied to the drones a mission is assigned to; control tests as instances with per-system limits.

## Controllability: mixer, control loops and tuning

The `controlSimulator` analysis (`services/mission-sim/app/control.py`) flies a drone the way a flight controller does. It's a 6-DOF rigid body with one rotor per motor. The command mixer turns thrust and body torques into rotor speeds, and a cascade of loops runs down to each motor's ESC:

```
speed, heading ─► velocity loop (PI) ─► tilt and thrust   climb rate ─► vertical loop (PI)
position (hover hold) ─► position loop (P) ─► speed
tilt, heading ─► attitude loop (P), heading loop (P) ─► body-rate commands
body rates ─► rate loops (PID roll and pitch, PI yaw) ─► torques
thrust + torques ─► MIXER ─► thrust per rotor ─► speed command
rotor speed ─► ESC speed loop (PI, per motor), or throttle only ─► motor voltage
```

- **Mixer.** The rotors sit in the X layout of the geometry service and spin in alternate directions (`firstRotorSpin`). The allocation matrix maps rotor thrusts to collective thrust and roll, pitch and yaw torque; the mixer is its pseudo-inverse. When a rotor would leave its range, yaw gives way first, then collective thrust, then roll and pitch.
- **Physics.** The rotors use the propeller's static coefficients and the motor model of the dynamic simulation, with the ESC's current and voltage limits. The body feels each motor's torque, so the yaw reaction includes the rotors spinning up, plus the rotors' gyroscopic torque. Inertia comes from derived attributes of `DeliveryDrone` (`inertiaXX`, `inertiaZZ`, `rotorInertia`), closed-form sums over the components' masses and positions.
- **Tuning** (`ControllerTuning`, an analysis def), inside out with open-loop tests:
  1. A voltage step on one motor with the body held gives the rotor time constant. The ESC speed loop is a PI from it.
  2. Torque steps on a gimbal, with the speed loops closed, give `1/inertia` and the lag of each axis. Every body torque follows the rotor loop, so the lag is at least the rotor loop's time constant. Roll and pitch get a PID and yaw a PI (SIMC rules for integrating processes), lowered until the loop has 45° of phase margin.
  3. The attitude, heading, velocity and position loops each get a quarter of the bandwidth of the loop inside them. The vertical loop is gentler, to keep thrust headroom.

  The out parameters of `ControllerTuning` are named like the attributes of `FlightController`. **Save & assign** writes them to a FlightController instance and puts it in the system's `controller` slot. The gains are design data, so they live in the model; the time responses live in the results store.
- **Control tests are requirements.** Each manoeuvre is a requirement def tagged `#ControlTest`, in `DroneControl`. Its attributes say what to fly, and its `in` parameters which of the test's results it is checked with:

  ```sysml
  #ControlTest requirement def SpeedStepTest {
      subject drone : DeliveryDrone;
      attribute command : String = "speed";        // climbRate, speed, heading or gust
      attribute step : Real = drone.cruise.speed;  // m/s, from the behavior the drone performs
      attribute hold : Real = 9;                   // s
      in settlingTime : Real;
      in overshoot : Real;
      in maxTilt : Real;
      in saturation : Real;
      require constraint { settlingTime <= 5 and overshoot <= 15 and maxTilt <= 35 and saturation <= 5 }
  }
  ```

  Every test starts from a settled hover:
  - `climbRate` steps the vertical speed (a negative step is a descent).
  - `speed` steps the ground speed.
  - `heading` turns by `step` degrees at `rate` deg/s, and settling counts from the end of the turn.
  - `gust` steps the wind while holding position.

  The results a test can be checked with are `riseTime`, `settlingTime`, `overshoot`, `maxTrackingError`, `maxDeviation`, `finalError`, `maxTilt` and `saturation`. The demo has five tests: `ClimbStepTest`, `DescentStepTest`, `SpeedStepTest`, `TurnTest` (90° at the drone's turn rate) and `GustTest` (5 m/s). Each takes its command from the drone's own behaviors, so heavyLift is tested at 2 m/s climb, 10 m/s cruise and 30°/s turns.
- **Verification** (`ControlSimulation`, a verification def) uses the gains of the system's controller. Its objective lists what it verifies: `ControlStabilityRequirement` (phase and gain margins), `ControlAuthorityRequirement` (thrust margin, yaw authority, saturation) and every control test. The model service sends the simulator the tests that apply to the system, with each attribute evaluated for it. The simulator flies exactly those and returns each one's results under `testResults[requirement]`, and each test gets its own verdict. To add a test, define one and verify it in the objective; to change one, edit its attributes or its constraint. A test that cannot be flown (an unknown command, a command with no value) fails on its own, and a test that diverges fails. In the demo, `regionalLiIon` fails control authority: its heavy pack leaves a thrust margin of 1.71 against the 1.8 required.
- **System simulations.** Both defs are tagged `#SystemSimulation`. They simulate a system on its own, from the Configurator, with the performed behaviors' parameters sent as `behaviors`, instead of flying missions. A requirement def that a system simulation verifies is a system requirement, not a mission requirement. Results go into the results store (mission `system`) and can be recorded as evidence.
- **Not modelled:** sensors and state estimation (the controller sees the true state), blade flapping, ground effect, vortex ring state and battery sag. Propellers use their static coefficients. The tuned gains are a sound starting point for flight tests, not flight-ready values.

## Simulation results and verification evidence

Simulation results are stored **outside** the SysML v2 repository, and the model holds only a reference to the ones that matter.

- **Why outside:** a result is derived from a commit. Writing it into the model would make a new commit, so the result would describe the version before the one that holds it. Re-running simulations would also fill the design history with machine commits, and timelines are poor element data. The Systems Modeling API has no results service, so storing them outside matches the standard.
- **Results store.** `models.simulation_result` has one row per mission flown by one system instance in one simulation. A row holds the project commit simulated (head of `main`), the mission (or the steps of a draft), the system instance, the simulation def, the simulator and its registry version, everything sent to the simulator with its hash, and everything returned. It also holds the verdicts and how each requirement was verified. Evaluate and Simulate write it.
- **Reuse.** The same simulator, version and inputs give the same outputs, so a stored result is reused instead of calling the simulator. At the same commit, the existing row is returned. At another commit, a new row copies the outputs and points at the original (`reused_from`), and gets that commit's verdicts.
- **Evidence in the model.** **Record** (in the Missions panel's requirement verification table) writes a verification usage of the simulation into the `VerificationEvidence` model, which is created on first use. Its subject is bound to the system instance, and its `SimulationEvidence` metadata points at the stored result:

  ```sysml
  verification parcel_suburban_MissionSimulation : DroneMissions::MissionSimulation {
      doc /* suburban flown by parcel: holds, 75.2 % of the battery, 16.6 min */
      subject drone = DroneArchitectures::Configurations::parcel;
      metadata SimulationEvidence {
          resultId = "ab5aabda-…";
          uri = "/api/models/results/ab5aabda-…";
          commitId = "49d1364c-…";          // the model version simulated
          mission = "DroneMissions::Missions::suburban";
          verdict = "holds";
          verdicts = "MissionTimeRequirement holds; MissionEnergyRequirement holds";
          recordedAt = "2026-11-08T00:34:53+00:00";
      }
  }
  ```

  Any SysML v2 client sees which result verified what, at which version, and can follow the `uri`. Recording again for the same mission, system and simulation replaces the element; earlier evidence stays in the model's history and in the results store. **tag the simulated version** also tags that commit `verified/<mission>/<system>/<result>`. A draft cannot be recorded, because it is not in the model.
- **Current or outdated.** Evidence is *current* while the newest stored result for its mission, system and simulation has the same simulator inputs, simulator version and verdicts. It is *outdated* once a change to the model (a behavior's speed, a battery, a requirement) changes what the simulation computes or concludes. Checking against the head commit would not work, because recording the evidence is itself a commit. The Missions panel shows the status beside each recorded result. **Stored results** lists the mission's results with links to their data.

## Storage: a SysML v2 API repository

The model service stores everything in the structure the SysML v2 API defines: projects, branches, tags and commits, where each commit holds versions of elements. The Studio maps onto it like this:

| Studio | SysML v2 API |
|---|---|
| Project | Project, with a default branch `main` |
| Model | A root `Namespace` (`@id` = model id, `declaredName` = model name) that owns the model's packages, a `Documentation` (the description) and a `TextualRepresentation` (language `sysml`, body = the text) |
| Saved version | A commit on `main` named `<model> v<n>`, with the save message as its description. It holds only the elements that changed. |
| Deleted model | A commit that deletes the model's elements. Earlier commits keep them. |

- **Element ids** are UUIDs derived from the interpreter's ids, so an element keeps its id from one version to the next. References to the standard library keep the library's ids.
- **Commits from other tools** (for example `%publish` in the SysML v2 Jupyter kernel) are stored as sent. Projects created that way appear in the Studio with no models. A commit that changes the text (`TextualRepresentation`) of a Studio model becomes a new version of that model.
- **An invalid text** is still saved as a version. Its commit changes only the text, so the elements stay as they were in the last valid version.

Example: every part definition at the head of a project.

```bash
curl -s -X POST http://localhost:8001/sysml-v2/projects/$PROJECT/query-results \
  -H 'Content-Type: application/json' \
  -d '{"@type": "Query", "select": ["declaredName"],
       "where": {"@type": "PrimitiveConstraint", "property": "@type", "operator": "instanceOf", "value": ["PartDefinition"]}}'
```

| Table | Contents |
|---|---|
| `models.project`, `models.branches`, `models.tags`, `models.queries` | API projects, branches, tags and saved queries |
| `models.commits` | Commits with their previous commits |
| `models.data_versions` | The changes of each commit: one row per created, updated or deleted (null `payload`) element, as SysML v2 API JSON (`jsonb`, with a GIN index) |
| `models.commit_elements` | For each commit, every element it holds and the version it has there. Reading a commit is a single join, however long the history. |
| `models.model`, `models.model_version` | Studio models and versions. Each version keeps its text, validity, diagnostics and diagram graph, and points at its commit. |
| `models.run` | Execution requests and results: checks, values, trace |
| `models.seed` | Demo projects already seeded, so a deleted demo is not re-created |
| `analysis.analysis` | Analysis registry: kind, inputs and outputs with units, endpoint or formulas |
| `analysis.invocation` | Every analysis call with its inputs, outputs, status, duration and `run_id` |

**Upgrading.** On first start, a database from an earlier release is migrated: every project gets a `main` branch, and every saved version becomes a commit, in save order, with its elements from the old `models.element` table. The old table is left as it was and is no longer used. Once you have checked the migration, you can drop it (`DROP TABLE models.element`).

Models in the same project can import each other's packages. The other models' latest versions are loaded alongside as context.

## Development without Docker

You need Python 3.12+, Node 22+, PostgreSQL, and the OpenSysML `sysml` binary on your PATH. Download it from the [releases page](https://github.com/Open-MBEE/OpenSysML/releases), or install with `brew install Open-MBEE/tap/opensysml`.

```bash
export DATABASE_URL=postgresql://sysml:sysml@localhost:5432/sysml
(cd services/analysis && pip install -r requirements.txt && SEED_ANALYSES=seed/analyses.json uvicorn app.main:app --port 8003) &
(cd services/interpreter && pip install -r requirements.txt && ANALYSIS_SERVICE_URL=http://localhost:8003 uvicorn app.main:app --port 8002) &
(cd services/model && pip install -r requirements.txt && INTERPRETER_SERVICE_URL=http://localhost:8002 \
   ANALYSIS_SERVICE_URL=http://localhost:8003 EXAMPLES_DIR=../../examples uvicorn app.main:app --port 8001) &
(cd services/geometry && pip install -r requirements.txt && MODEL_SERVICE_URL=http://localhost:8001 uvicorn app.main:app --port 8004) &
(cd services/mission-sim && pip install -r requirements.txt && uvicorn app.main:app --port 8011) &
(cd frontend && npm install && npm run dev)   # http://localhost:5173 (Vite proxies /api/*)
```

The seeded `missionSimulator` points at `http://mission-sim:8011/simulate` `dynamicsSimulator` at `http://mission-sim:8011/dynamics` and `componentCurves` at `http://mission-sim:8011/curves`. Outside Docker, run that service and change the URL in the Analyses panel.

## Tests

```bash
# interpreter tests run the real OpenSysML binary
cd services/interpreter && pytest
# analysis + model tests need PostgreSQL
DATABASE_URL=postgresql://sysml:sysml@localhost:5432/sysml pytest services/analysis/tests services/model/tests
# geometry and mission simulator tests need nothing else
cd services/geometry && pytest
cd services/mission-sim && pytest
# or, inside the running stack:
docker compose exec interpreter pytest
```

## Notes and limits

- **Interpreter.** OpenSysML v0.11.0 is pinned (`OPENSYSML_VERSION` build arg). It is called as one CLI process per request. Parse and validate together take about 0.3–0.7 s for the example model. The adapter ([`opensysml.py`](services/interpreter/app/opensysml.py)) is small, so it can be replaced by OpenSysML's `sysml-grpc` service for long-lived, cached sessions. OpenSysML calls its SysML v2 API JSON export experimental.
- **Diagrams.** The diagrams are drawn by this app from the interpreted element graph, using ELK for layout. They are not SysML v2 standard-conformant view rendering. OpenSysML's own renderings are available from `POST /render`.
- **Solvers.** The interpreter image includes `z3`, which enables OpenSysML's `smt` and `solve` engines.
- **SysML v2 API.** The behaviour follows the reference implementation (SysML-v2-API-Services) where that differs from the OpenAPI document, so that existing clients work. Tested with the SysML v2 Jupyter kernel 0.55.0 (`%publish`, `%projects`, `%load`).
  - `Commit.previousCommit` is a single `{"@id"}`, as the reference implementation returns it. Set `SYSML_API_PREVIOUS_COMMIT=array` for the array form of the OpenAPI document, which also lists the second parent of a merge commit.
  - A change with no `payload` is a deletion. Some clients leave out null fields.
  - Not implemented: `/meta/datatypes`, element data reached through a `ProjectUsage` (`excludeUsed` is accepted and has no effect), and the `/x/named` extensions.
  - Payloads are stored as sent. Derived properties are not computed and references are not checked.
  - Project names must be unique (409).
  - Each commit copies the project's element index (`models.commit_elements`, about 60 bytes per element), so storage grows with elements × commits.
- **Not production-hardened.** There is no authentication or user management yet. Add an auth proxy (OIDC) in front of nginx before deploying for a team.
