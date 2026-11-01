# SysML v2 Studio: microservices platform

This project lets you write a SysML v2 model as text in the browser and see it drawn as diagrams while you type. It stores projects, models and every version in a **SysML v2 API server** (the OMG Systems Modeling API & Services pilot implementation) and runs the model with the **OpenMBEE OpenSysML** interpreter. While a model runs, its calcs and actions can call analyses over a REST API.

```mermaid
flowchart LR
  subgraph Browser
    UI["React UI<br/>editor · live diagrams · requirements · execution"]
  end
  UI -->|/api/*| GW["nginx gateway<br/>(frontend container)"]
  GW -->|/api/models| MS["model service :8001<br/>projects · versions · interpretation · runs"]
  GW -->|/api/sysml-v2| API["SysML v2 API server :9000<br/>projects · commits · elements"]
  MS -->|projects, commits, queries| API
  GW -->|/api/analysis| AS["analysis service :8003<br/>registry · REST invoke · call log"]
  GW -->|/api/interpreter| IS["interpreter service :8002<br/>OpenSysML wrapper"]
  MS -->|parse / execute| IS
  IS -->|spawns| OS[["OpenSysML sysml CLI<br/>(Go, SysML v2 + KerML)"]]
  OS -->|ToolExecution → bridge.py| AS
  AS -->|kind=external| EXT["any REST endpoint<br/>(demo-external :8010)"]
  MS --- PG[(PostgreSQL<br/>schemas: sysml_api, models, analysis)]
  API --- PG
  AS --- PG
```

| Service | Port | Responsibility |
|---|---|---|
| `frontend` | 8080 | React UI (Vite build) served by nginx. nginx also routes `/api/models`, `/api/interpreter`, `/api/analysis` and `/api/sysml-v2` to the services. |
| `model` | 8001 | Projects and models, stored in the SysML v2 API server. Every save is a commit carrying the model's text and the interpreted elements that changed. Also records runs and serves requirement traceability. |
| `sysml-api` | 9000 | The [SysML v2 API & Services](https://github.com/Systems-Modeling/SysML-v2-API-Services) pilot implementation (release 2025-02): the standard REST API for projects, branches, commits, elements and queries. Any SysML v2 API client can read the models from it. |
| `interpreter` | 8002 | Wraps the OpenSysML binary. Endpoints: validate/parse → diagnostics + diagram graph, execute (calc, analysis case, requirement, constraint, satisfy, action, state machine, expression), and OpenSysML's own rendering (Mermaid, DOT, PlantUML, D2). |
| `analysis` | 8003 | Registry of analyses (`builtin` Python functions, `formula`, `external` REST endpoints). `POST /analyses/{name}/invoke` runs one. Every call is logged together with the id of the run that made it. |
| `demo-external` | 8010 | An example third-party REST analysis (a thermal solver). It is registered in the registry as `thermalSolver`. |
| `postgres` | 5432 | PostgreSQL 16. The SysML v2 API server keeps its tables in schema `sysml_api`. The model service (runs only) and the analysis service each own a schema. |

## Quick start

```bash
docker compose up --build
# open http://localhost:8080
```

The first build compiles the SysML v2 API server from source with sbt, which takes a while. If you already have an image of the pilot implementation, for example one built from [its Dockerfile](https://github.com/Systems-Modeling/SysML-v2-API-Services), reuse it. The image must hold the distribution under `/usr/src/myapp/scripts`:

```bash
SYSML_API_PILOT_IMAGE=sysmlapiserver:latest docker compose up --build
```

Upgrading from a release that stored models in PostgreSQL: on first start the model service copies every project into the SysML v2 API server, with each saved version as a commit and the original model ids and dates. Runs stay attached. The old tables are left in place (see [`legacy.py`](services/model/app/legacy.py)).

On first start the model service creates two demo projects:

- **Demo / Vehicle** ([`examples/vehicle.sysml`](examples/vehicle.sysml)) covers requirements, part and port definitions, connections, instances, an action, a state machine, and calcs/analyses that run over REST.
- **Product Line** has a component library ([`examples/component_library.sysml`](examples/component_library.sysml)) and system architectures with configured system instances ([`examples/architectures.sysml`](examples/architectures.sysml)).

Things to try in the UI:

1. **Edit the text.** The model is re-interpreted about 0.5 s after you stop typing. Diagnostics appear in the gutter and the problems list, and the diagrams update. You can switch between four views: Structure, Interconnection (with ports and connections), Requirements (with satisfy relations and verdicts) and Behavior (actions and states).
2. **Click a box, a compartment line or a port.** The editor selects that element's source. Moving the cursor in the editor selects the element in the diagram.
3. **Requirements tab.** Capture a requirement with the form; the SysML text is inserted for you. **Check all satisfy relations** runs every `satisfy`. `sedan.mass` and `sedan.range` are computed by REST analyses during that check.
4. **Execute tab.** Run `VehicleModel::sedanCooling`. This analysis case calls the external `thermalSolver` endpoint. The tab lists every REST call made during the run. Running `VehicleModel::Drive` or `VehicleStates` plays back the execution trace in both the diagram and the editor.
5. **Analyses tab.** Register an external endpoint or a formula. **Insert SysML binding** generates a `calc def` with the `ToolExecution` metadata that binds to it.
6. **Library tab** (Product Line project). Browse the component types and their catalog instances. **+ New instance** creates an instance from a form with one field per attribute. You can also edit or delete instances and declare new components.
7. **Configurator tab.** Pick a system architecture and fill each slot with a compatible catalog instance, then create the system instance. **Evaluate & check requirements** computes every system instance's derived attributes and checks each applicable requirement def against it, in one run.

## Component library and system configuration

Everything is plain SysML v2 text in the project's models. The Library and Configurator tabs find components and architectures by two metadata tags, and they edit the text for you:

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
- `GET/POST /projects`, `DELETE /projects/{id}` (the ids are the SysML v2 API project ids)
- `GET/POST /projects/{id}/models`: create from `source`, or from the `empty` or `vehicle` template
- `GET /models/{id}[?version=n]`: source, diagnostics, graph, and the `commitId` of that version
- `POST /models/{id}/preview`: interpret unsaved text; nothing is stored
- `PUT /models/{id}/source`: save a new version (interpreted and stored)
- `GET /models/{id}/versions`, `GET /models/{id}/elements?metatype=PartUsage&q=engine` (the model's elements as the SysML v2 API server holds them)
- `GET /models/{id}/requirements`: text, constraints, subject, satisfied-by, latest verdict
- `POST /models/{id}/runs`: `{"kind": "calc|analysis|requirement|constraint|satisfy|action|state|evaluate|validate", "target", "args", "subject", "expression", "advance"}`
- `GET /models/{id}/runs`, `GET /runs/{id}` (includes the REST calls made during the run)
- `GET /projects/{id}/catalog`: components, instances, architectures (slots with compatible instances, applicable requirements), system instances
- `POST /projects/{id}/catalog/instances` `{"component", "name", "values": {attr: expr}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/configurations` `{"architecture", "name", "assignments": {slot: instance}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/components` `{"name", "package", "specializes"?, "abstract"?, "attributes": [{name, type}], "ports": [...]}`
- `DELETE /projects/{id}/catalog/elements?qualifiedName=`

**SysML v2 API server** (standard API, http://localhost:9000/docs/, or `/api/sysml-v2/...` through the gateway)
- `GET /projects`, `GET /projects/{id}/branches`, `GET /projects/{id}/commits`, `GET /projects/{id}/commits/{commit}/changes`
- `GET /projects/{id}/commits/{commit}/elements[/{element}]`, `GET .../roots`
- `POST /projects/{id}/query-results?commitId=` with a `Query` (e.g. all `PartUsage`s)

**Interpreter service** (stateless; send the files directly)
- `POST /parse` `{"files": [{"name", "text"}], "includeElements": false}`
- `POST /execute` `{"files", "kind", "target", ...}`
- `POST /render` `{"files", "view": "#interconnection:Pkg::Vehicle", "form": "mermaid"}`
- `GET /engines`: OpenSysML engines and registered tools

**Analysis service**
- `GET/POST /analyses`, `GET/PUT/PATCH(?enabled=)/DELETE /analyses/{name}`
- `POST /analyses/{name}/invoke` `{"inputs": {...}, "uri"?, "runId"?}`
- `GET /invocations?runId=&analysis=&limit=`

## Storage

### SysML v2 API server

| Studio | SysML v2 API |
|---|---|
| Project | `Project`. Its default branch `main` is the history. |
| Model | A root `Namespace` whose `@id` is the model id and whose `declaredName` is the model name. It owns the model's packages, a `Documentation` (the description) and a `TextualRepresentation` with `language = "sysml"` whose `body` is the model text. |
| Version | A `Commit` on `main` that changed the model. Its description is the save message followed by the trailers `Model-Id`, `Model-Name`, `Valid` and `Elements`. Version *n* of a model is the *n*th commit with its `Model-Id`. |
| Interpreted elements | The OpenSysML `api-json` elements of the model. Their ids are derived from qualified names, so they map to stable UUIDs: an element keeps its identity across commits, and a commit carries only the elements that changed (plus deletions). |

The text is the source of truth. The diagnostics and the diagram graph are derived from it by the interpreter and cached by the model service. Read a model's text back with any API client:

```bash
curl -s -X POST "localhost:9000/projects/$PROJECT/query-results?commitId=$COMMIT" -H 'Content-Type: application/json' -d '{
  "@type": "Query", "select": ["body"],
  "where": {"@type": "PrimitiveConstraint", "inverse": false, "operator": "=", "property": "@type", "value": ["TextualRepresentation"]}}'
```

### PostgreSQL

| Table | Contents |
|---|---|
| `sysml_api.*` | The SysML v2 API server's own tables (created by its Hibernate mapping) |
| `models.run` | Execution requests and results: checks, values, trace |
| `models.seed` | Demo projects already seeded, so a deleted demo is not re-created, and legacy projects already migrated |
| `models.deleted_project` | Projects deleted in the studio (see the limits below) |
| `analysis.analysis` | Analysis registry: kind, inputs and outputs with units, endpoint or formulas |
| `analysis.invocation` | Every analysis call with its inputs, outputs, status, duration and `run_id` |

Models in the same project can import each other's packages. The other models' latest texts are loaded alongside as context. They are all in the same API project, so references between models resolve in its commits.

## Development without Docker

You need Python 3.12+, Node 22+, PostgreSQL, a SysML v2 API server (e.g. `docker compose up -d postgres sysml-api`), and the OpenSysML `sysml` binary on your PATH. Download it from the [releases page](https://github.com/Open-MBEE/OpenSysML/releases), or install with `brew install Open-MBEE/tap/opensysml`.

```bash
export DATABASE_URL=postgresql://sysml:sysml@localhost:5432/sysml
(cd services/analysis && pip install -r requirements.txt && SEED_ANALYSES=seed/analyses.json uvicorn app.main:app --port 8003) &
(cd services/interpreter && pip install -r requirements.txt && ANALYSIS_SERVICE_URL=http://localhost:8003 uvicorn app.main:app --port 8002) &
(cd services/model && pip install -r requirements.txt && SYSML_API_URL=http://localhost:9000 INTERPRETER_SERVICE_URL=http://localhost:8002 \
   ANALYSIS_SERVICE_URL=http://localhost:8003 EXAMPLES_DIR=../../examples uvicorn app.main:app --port 8001) &
(cd frontend && npm install && npm run dev)   # http://localhost:5173 (Vite proxies /api/*)
```

The seeded `thermalSolver` points at `http://demo-external:8010`. Outside Docker, run `services/demo-external` and change the URL in the Analyses tab.

## Tests

```bash
# interpreter tests run the real OpenSysML binary
cd services/interpreter && pytest
# analysis + model tests need PostgreSQL; the model tests also need a SysML v2 API server
DATABASE_URL=postgresql://sysml:sysml@localhost:5432/sysml SYSML_API_URL=http://localhost:9000 \
  pytest services/analysis/tests services/model/tests
# or, inside the running stack:
docker compose exec interpreter pytest
```

## Notes and limits

- **SysML v2 API server.** These limits come from the pilot implementation:
  - It holds no standard library, and it refuses references to elements it does not hold. References to library elements (such as `ISQ::MassValue`) are therefore left out of the stored elements. The text still has them.
  - It cannot delete a project that has commits. Deleting a project in the studio commits the removal of its models and hides the project; its history stays in the server. Test runs leave such hidden projects behind.
  - It does not check `previousCommit` against the branch head. The model service serializes the commits of a project itself, so it runs as a single process.
  - Commits made by other API clients appear in the project's state, but only commits with a `Model-Id` trailer are listed as versions.

- **Interpreter.** OpenSysML v0.11.0 is pinned (`OPENSYSML_VERSION` build arg). It is called as one CLI process per request. Parse and validate together take about 0.3–0.7 s for the example model. The adapter ([`opensysml.py`](services/interpreter/app/opensysml.py)) is small, so it can be replaced by OpenSysML's `sysml-grpc` service for long-lived, cached sessions. OpenSysML calls its SysML v2 API JSON export experimental.
- **Diagrams.** The diagrams are drawn by this app from the interpreted element graph, using ELK for layout. They are not SysML v2 standard-conformant view rendering. OpenSysML's own renderings are available from `POST /render`.
- **Solvers.** The interpreter image includes `z3`, which enables OpenSysML's `smt` and `solve` engines.
- **Not production-hardened.** There is no authentication or user management yet. Add an auth proxy (OIDC) in front of nginx before deploying for a team.
