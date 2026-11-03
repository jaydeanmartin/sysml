# SysML v2 Studio: microservices platform

This project lets you write a SysML v2 model as text in the browser and see it drawn as diagrams while you type. It stores every version in PostgreSQL, in a repository that any SysML v2 API client can read and write through the standard **SysML v2 API** (OMG Systems Modeling API and Services). It runs the model with the **OpenMBEE OpenSysML** interpreter. While a model runs, its calcs and actions can call analyses over a REST API.

```mermaid
flowchart LR
  subgraph Browser
    UI["React UI<br/>editor · live diagrams · requirements · execution"]
  end
  UI -->|/api/*| GW["nginx gateway<br/>(frontend container)"]
  GW -->|/api/models| MS["model service :8001<br/>projects · versions · elements · runs"]
  GW -->|/api/sysml-v2| MS
  CL["SysML v2 API clients<br/>(Jupyter %publish, tools)"] -->|/sysml-v2| MS
  GW -->|/api/analysis| AS["analysis service :8003<br/>registry · REST invoke · call log"]
  GW -->|/api/interpreter| IS["interpreter service :8002<br/>OpenSysML wrapper"]
  MS -->|parse / execute| IS
  IS -->|spawns| OS[["OpenSysML sysml CLI<br/>(Go, SysML v2 + KerML)"]]
  OS -->|ToolExecution → bridge.py| AS
  AS -->|kind=external| EXT["any REST endpoint<br/>(demo-external :8010)"]
  MS --- PG[(PostgreSQL<br/>schemas: models, analysis)]
  AS --- PG
```

| Service | Port | Responsibility |
|---|---|---|
| `frontend` | 8080 | React UI (Vite build) served by nginx. nginx also routes `/api/models`, `/api/sysml-v2`, `/api/interpreter` and `/api/analysis` to the services. |
| `model` | 8001 | Projects and models. Every save is a new, unchangeable version, stored as a commit of the project's SysML v2 API repository. The service serves that repository through the standard SysML v2 API at `/sysml-v2`. Also records runs and serves requirement traceability. |
| `interpreter` | 8002 | Wraps the OpenSysML binary. Endpoints: validate/parse → diagnostics + diagram graph, execute (calc, analysis case, requirement, constraint, satisfy, action, state machine, expression), and OpenSysML's own rendering (Mermaid, DOT, PlantUML, D2). |
| `analysis` | 8003 | Registry of analyses (`builtin` Python functions, `formula`, `external` REST endpoints). `POST /analyses/{name}/invoke` runs one. Every call is logged together with the id of the run that made it. |
| `demo-external` | 8010 | An example third-party REST analysis (a thermal solver). It is registered in the registry as `thermalSolver`. |
| `postgres` | 5432 | PostgreSQL 16. The model and analysis services each own a schema. |

## Quick start

```bash
docker compose up --build
# open http://localhost:8080
```

On first start the model service creates two demo projects:

- **Demo / Vehicle** ([`examples/vehicle.sysml`](examples/vehicle.sysml)) covers requirements, part and port definitions, connections, instances, an action, a state machine, and calcs/analyses that run over REST.
- **Delivery Drones** is a configurable parcel delivery drone. Its library ([`examples/drone_library.sysml`](examples/drone_library.sysml)) has frames from 450 to 1600 mm, six batteries, four motors, four propellers, and parcel boxes and winches carrying 1 to 15 kg. The quad, hexa and octo architectures ([`examples/drone_architectures.sysml`](examples/drone_architectures.sysml)) set the number of rotors through the multiplicity of the `motors` and `propellers` slots (`part :>> motors[6];`). Each architecture derives takeoff mass, thrust-to-weight, hover power, endurance and propeller-tip clearance from the parts chosen. Six requirement defs check every drone. Of the six configured drones, `parcel`, `regional` and `heavyLift` pass every check. `courier`, `parcelLongRange` and `regionalLiIon` each fail one or two, for different reasons. The library also declares abstract behaviors with parameters: takeoff, cruise, hover, winch, landing and drop-off. Its behavior catalog holds performance profiles, such as `cruise12` (12 m/s, power factor 1.1). Every architecture performs takeoff, cruise, hover, land and release; the Octocopter also performs winch lowering. Each drone performs a profile for each of them. The four delivery missions ([`examples/drone_missions.sysml`](examples/drone_missions.sysml)) are ordered lists of behaviors with distances and altitudes. Each drone flies a mission with its own profiles, and a mission energy requirement checks the result. All six drones can fly `shortHop`. Only `parcelLongRange` has the battery for `rural`. Only `heavyLift` can fly `winchDrop`, because it is the only drone that performs winch lowering.

Things to try in the UI. The left sidebar opens each panel; **«** collapses it to icons. Its **Editor** item shows or minimizes the text editor.

1. **Edit the text.** The model is re-interpreted about 0.5 s after you stop typing. Diagnostics appear in the gutter and the problems list, and the diagrams update. You can switch between four views: Structure, Interconnection (with ports and connections), Requirements (with satisfy relations and verdicts) and Behavior (actions and states). The editor is a modeless window floating over the workspace, so the diagram and the other panels stay usable while it is open. Drag its title bar to move it, drag the bottom-right corner to resize it, and double-click the title bar or press **–** to minimize it. **⇤** docks it beside the workspace and **⧉ Float** floats it again. The browser remembers its position and size.
2. **Click a box, a compartment line or a port.** The editor selects that element's source. Moving the cursor in the editor selects the element in the diagram.
3. **Requirements panel.** Capture a requirement with the form; the SysML text is inserted for you. **Check all satisfy relations** runs every `satisfy`. `sedan.mass` and `sedan.range` are computed by REST analyses during that check.
4. **Execute panel.** Run `VehicleModel::sedanCooling`. This analysis case calls the external `thermalSolver` endpoint. The panel lists every REST call made during the run. Running `VehicleModel::Drive` or `VehicleStates` plays back the execution trace in both the diagram and the editor.
5. **Analyses panel.** Register an external endpoint or a formula. **Insert SysML binding** generates a `calc def` with the `ToolExecution` metadata that binds to it.
6. **Library panel** (Delivery Drones project). Browse the component types, the behaviors and their catalog instances. **+ New instance** creates an instance from a form with one field per attribute, or one per parameter for a behavior. A behavior instance is incomplete until every parameter is set (see `cruiseSport`). You can also edit or delete instances and declare new components and behaviors.
7. **Configurator panel.** Pick a system architecture. Fill each part slot with a compatible catalog instance, and choose a complete behavior instance for every behavior the architecture performs. Then create the system instance. **Evaluate & check requirements** computes every system instance's derived attributes and checks each applicable requirement def against it, in one run.
8. **Missions panel.** Build a mission by clicking behaviors in the order they are performed, then set each step's quantities (distance, altitude…). Reorder steps with ↑ ↓ and change a step's behavior from its drop-down. **Evaluate & check missions** flies every mission with every drone and checks the mission requirements. The mission being edited then shows each drone's verdict, plus the step durations and a timeline for the drone you pick under **Flown by**.

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
requirement def MissionEnergyRequirement {      // a mission requirement
    subject drone : DeliveryDrone;
    in hoverTime : Real;                        // named like a total
    require constraint { drone.hoverPower * hoverTime / 3600 <= drone.battery.energy * 0.8 }
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
- A mission requirement is a requirement def with `in` parameters. It applies to every system instance whose architecture conforms to its subject type, if each parameter is named like a total of the mission type. It is checked as `Requirement(system, total, …)`. The Configurator leaves mission requirements out of its own checks.
- **Evaluate & check missions** calls `POST /projects/{id}/catalog/missions/evaluate`. The service writes the flown steps of every mission and drone into a scratch package. It then evaluates them with the project's models in a single run and stores nothing.

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

An external endpoint receives `POST {"toolName", "uri", "inputs", "runId"}` and must return either `{"outputs": {name: {"value", "unit"?}}}` or `{"error": "..."}`. See [`services/demo-external/app.py`](services/demo-external/app.py).

To add a Python analysis, decorate a function in [`services/analysis/app/builtins.py`](services/analysis/app/builtins.py):

```python
@analysis("stressCheck", "Von Mises stress", inputs=[{"name": "force", "unit": "N"}, {"name": "area", "unit": "m**2"}],
          outputs=[{"name": "stress"}])
def stress_check(v):
    return {"stress": v["force"] / v["area"]}
```

## REST API (summary)

Interactive OpenAPI docs are served at `/docs` on each service, e.g. http://localhost:8001/docs.

**Model service**
- `GET/POST /projects`, `DELETE /projects/{id}`
- `GET/POST /projects/{id}/models`: create from `source`, or from the `empty` or `vehicle` template
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
- `POST /projects/{id}/catalog/missions/evaluate` `{"missionType", "draft"?: [steps]}`: every mission (and the draft) flown by every system instance that performs behaviors: per-step values, totals, mission requirement verdicts, or why it cannot be flown
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
(cd frontend && npm install && npm run dev)   # http://localhost:5173 (Vite proxies /api/*)
```

The seeded `thermalSolver` points at `http://demo-external:8010`. Outside Docker, run `services/demo-external` and change the URL in the Analyses panel.

## Tests

```bash
# interpreter tests run the real OpenSysML binary
cd services/interpreter && pytest
# analysis + model tests need PostgreSQL
DATABASE_URL=postgresql://sysml:sysml@localhost:5432/sysml pytest services/analysis/tests services/model/tests
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
