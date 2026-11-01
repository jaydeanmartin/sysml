# SysML v2 Studio: microservices platform

This project lets you write a SysML v2 model as text in the browser and see it drawn as diagrams while you type. It stores every version in PostgreSQL and runs the model with the **OpenMBEE OpenSysML** interpreter. While a model runs, its calcs and actions can call analyses over a REST API.

```mermaid
flowchart LR
  subgraph Browser
    UI["React UI<br/>editor · live diagrams · requirements · execution"]
  end
  UI -->|/api/*| GW["nginx gateway<br/>(frontend container)"]
  GW -->|/api/models| MS["model service :8001<br/>projects · versions · elements · runs"]
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
| `frontend` | 8080 | React UI (Vite build) served by nginx. nginx also routes `/api/models`, `/api/interpreter` and `/api/analysis` to the services. |
| `model` | 8001 | Projects and models. Every save is a new, unchangeable version. The interpreted elements are stored as SysML v2 API JSON, one row per element. Also records runs and serves requirement traceability. |
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
- `GET/POST /projects`, `DELETE /projects/{id}`
- `GET/POST /projects/{id}/models`: create from `source`, or from the `empty` or `vehicle` template
- `GET /models/{id}[?version=n]`: source, diagnostics, graph
- `POST /models/{id}/preview`: interpret unsaved text; nothing is stored
- `PUT /models/{id}/source`: save a new version (interpreted and stored)
- `GET /models/{id}/versions`, `GET /models/{id}/elements?metatype=PartUsage&q=engine`
- `GET /models/{id}/requirements`: text, constraints, subject, satisfied-by, latest verdict
- `POST /models/{id}/runs`: `{"kind": "calc|analysis|requirement|constraint|satisfy|action|state|evaluate|validate", "target", "args", "subject", "expression", "advance"}`
- `GET /models/{id}/runs`, `GET /runs/{id}` (includes the REST calls made during the run)
- `GET /projects/{id}/catalog`: components, instances, architectures (slots with compatible instances, applicable requirements), system instances
- `POST /projects/{id}/catalog/instances` `{"component", "name", "values": {attr: expr}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/configurations` `{"architecture", "name", "assignments": {slot: instance}, "doc"?, "package"?, "replace"?}`
- `POST /projects/{id}/catalog/components` `{"name", "package", "specializes"?, "abstract"?, "attributes": [{name, type}], "ports": [...]}`
- `DELETE /projects/{id}/catalog/elements?qualifiedName=`

**Interpreter service** (stateless; send the files directly)
- `POST /parse` `{"files": [{"name", "text"}], "includeElements": false}`
- `POST /execute` `{"files", "kind", "target", ...}`
- `POST /render` `{"files", "view": "#interconnection:Pkg::Vehicle", "form": "mermaid"}`
- `GET /engines`: OpenSysML engines and registered tools

**Analysis service**
- `GET/POST /analyses`, `GET/PUT/PATCH(?enabled=)/DELETE /analyses/{name}`
- `POST /analyses/{name}/invoke` `{"inputs": {...}, "uri"?, "runId"?}`
- `GET /invocations?runId=&analysis=&limit=`

## Data model (PostgreSQL)

| Table | Contents |
|---|---|
| `models.project`, `models.model` | Projects and models. `head_version` points at the current version. |
| `models.model_version` | The full SysML text of each save, with validity, diagnostics, diagram graph and element count |
| `models.element` | Each interpreted element as SysML v2 API JSON (`data jsonb`, with a GIN index), plus `metatype`, `qualified_name` and `owner_id` |
| `models.run` | Execution requests and results: checks, values, trace |
| `models.seed` | Demo projects already seeded, so a deleted demo is not re-created |
| `analysis.analysis` | Analysis registry: kind, inputs and outputs with units, endpoint or formulas |
| `analysis.invocation` | Every analysis call with its inputs, outputs, status, duration and `run_id` |

Example query: every part usage in the latest version of each model.

```sql
SELECT m.name, e.qualified_name FROM models.element e
JOIN models.model_version v ON v.id = e.version_id
JOIN models.model m ON m.id = v.model_id AND v.version = m.head_version
WHERE e.metatype = 'PartUsage';
```

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

The seeded `thermalSolver` points at `http://demo-external:8010`. Outside Docker, run `services/demo-external` and change the URL in the Analyses tab.

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
- **Not production-hardened.** There is no authentication or user management yet. Add an auth proxy (OIDC) in front of nginx before deploying for a team.
