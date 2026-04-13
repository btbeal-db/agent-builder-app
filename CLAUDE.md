# Agent Builder App

Visual drag-and-drop LangGraph agent builder for Databricks. Users design, test, and deploy AI agents without writing code. Built on LangGraph, MLflow, and Databricks infrastructure (Vector Search, Genie, UC Functions, Lakebase).

## Target User

Data scientists, analysts, and ML practitioners who are comfortable with Databricks but not necessarily writing LangGraph code. They know what a "retrieval agent" or "routing pipeline" is conceptually, but want to wire one up visually rather than hand-coding state graphs. Assume familiarity with concepts like Unity Catalog, serving endpoints, and MLflow runs -- but not with LangGraph internals, OAuth flows, or infrastructure provisioning.

## Stack

- **Frontend**: React 18 + TypeScript, Vite, @xyflow/react (graph canvas), Lucide icons
- **Backend**: FastAPI, Pydantic, LangGraph, MLflow, databricks-sdk, databricks-langchain
- **Deployment**: Databricks Apps (auto-managed compute, OBO token injection)

## User Journey

### 1. Setup (one-time, per user)

User creates a workspace folder (e.g. `/Users/{email}/agent-sweet`), then grants the app's service principal "Can Manage" on that folder. The app validates by creating a test MLflow experiment and persists a `.agent-builder-setup.json` config file in the folder.

- `GET /api/setup/status` -- checks if setup is complete (reads config from workspace file)
- `GET /api/setup/info` -- returns SP display name + user email (for the setup wizard UI)
- `POST /api/setup/validate` -- validates folder access, creates experiment, persists config

### 2. Build

Drag nodes onto the canvas, connect them, define a state model. Available node types:

- **LLM** -- ChatDatabricks, system prompt, structured output, conversational mode, tool calling
- **Router** -- conditional branching on a state field (keyword match, boolean, fallback)
- **Vector Search** -- query a VS index with optional reranking and filters
- **Genie** -- query a Databricks Genie room, formats SQL results as text
- **UC Function** -- call a Unity Catalog function with JSON parameters
- **Human Input** -- pause the graph, prompt the user, resume with their answer

State model: user-defined fields (`str`, `int`, `float`, `bool`, `list[str]`, `structured`). The `messages` field uses LangGraph's `add_messages` reducer for multi-turn history.

### 3. Preview / Playground

`POST /api/graph/preview` compiles the GraphDef into a LangGraph StateGraph and runs it with in-memory checkpointing. Returns output, full execution trace, state snapshot, and MLflow spans. Supports multi-turn (thread_id) and human-in-the-loop (resume_value).

### 4. Deploy

`POST /api/graph/deploy` -- streamed SSE with steps: validate, provision Lakebase (if conversational), log model, register in UC, create serving endpoint.

Three modes:
- **Log Only** -- no PAT required, registers later via UI
- **Log & Register** -- requires PAT, registers model in UC
- **Full Deploy** -- requires PAT, registers + creates serving endpoint

## Authentication Model

Three credential types, each serving a distinct purpose:

### OBO (On-Behalf-Of) Token

Injected by Databricks Apps in the `x-forwarded-access-token` header on every request. Stored per-request via `ContextVar` in middleware.

**Used for**: preview/playground (user sees only their data), user identity, Vector Search, Genie, UC Functions.

**Key constraint**: The Databricks SDK loads all env vars into its Config, including SP creds. If both OBO token and `client_id`/`client_secret` are present, the server treats the request as an SP OAuth call (wrong scopes). `get_workspace_client()` temporarily masks `DATABRICKS_CLIENT_ID`/`SECRET` from the environment AND passes `auth_type="pat"` to force bearer-token auth. Both are required.

**Scopes** (declared in `databricks.yml`): `sql`, `serving.serving-endpoints`, `catalog.*:read`, `vectorsearch.*`.

### Service Principal (SP)

Env vars `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` auto-injected by Apps platform.

**Used for**: LLM calls (FMAPI rejects OBO), MLflow experiment logging (no `ml.experiments` OBO scope exists), setup config persistence, graph loading.

**Why SP for MLflow?** No OBO scopes exist for MLflow operations. The SP is scoped: during setup the user grants "Can Manage" on their experiment folder. The SP has no access to folders it hasn't been granted.

### Personal Access Token (PAT)

User provides at deploy time. Never stored, used only for the duration of the request.

**Used for**: UC model registration, schema creation, serving endpoint creation.

**Why PAT?** No UC write OBO scopes exist (only `catalog.*:read`). OBO scopes for `mlflow`, `unity-catalog`, and `model-serving` appear in the OAuth discovery endpoint but don't actually grant access in practice (as of April 2026).

**Implementation detail**: `mlflow.register_model()` is run in a subprocess because MLflow caches `DatabricksConfig` via `@lru_cache` -- env-var masking in the same process doesn't override it.

| Operation | Credential | Why |
|---|---|---|
| Preview / Playground | OBO | User sees only their data |
| LLM inference (ChatDatabricks) | SP | FMAPI rejects OBO |
| Vector Search | SP | No OBO vector-search scope; indexes auto-granted as app resources |
| MLflow experiment logging | SP | No OBO scope for MLflow |
| Setup config persistence | SP | Workspace file write |
| UC model registration | PAT | No UC write OBO scopes |
| Serving endpoint creation | PAT | model-serving scope unreliable |

## Known Gotchas

- **MLflow artifact downloads from Apps**: `mlflow.artifacts.download_artifacts()` follows presigned URL redirects to `storage.cloud.databricks.com`, which is unreachable from Databricks Apps networking. DBFS root is also disabled. We removed the run-ID-based loading feature in favor of direct JSON paste/import.
- **Lakebase OAuth token expiry**: Autoscaling projects use tokens that expire after 1 hour. Serving endpoints use a `ConnectionPool` with a custom `Connection` subclass that calls `generate_database_credential()` for fresh tokens on each new connection.
- **CPU serving tracing**: Endpoints need `ENABLE_MLFLOW_TRACING=true` and `MLFLOW_EXPERIMENT_ID` env vars explicitly. `autolog()` alone is insufficient.
- **OBO + SP env var conflict**: The SDK loads `client_id`/`client_secret` from env into its Config even with `auth_type="pat"`. If both are present, the server treats it as an SP OAuth call (wrong scopes). Must mask SP env vars before creating OBO client AND pass `auth_type="pat"`. Always restore via `finally`.

## Dev Preferences

- Prefer existing dependencies over new ones when the SDK already covers the functionality.
- Don't add migration/compatibility layers for loading older graph JSON -- keep it simple, let the user fix stale field names manually.
- Keep deploy flow using SP credentials until OBO scopes for MLflow/UC actually work.

## Project Structure

```
backend/
  main.py          -- FastAPI app, routes, deploy flow, middleware
  auth.py          -- OBO/SP/PAT token handling, WorkspaceClient factories
  schema.py        -- Pydantic models (GraphDef, NodeDef, etc.)
  graph_builder.py -- Compiles GraphDef -> LangGraph StateGraph, runs it
  mlflow_model.py  -- MLflow ResponsesAgent wrapper (entry point for serving)
  lakebase.py      -- Lakebase provisioning + Postgres connection pool
  setup.py         -- Setup endpoints
  tools.py         -- Tool factories (VS, Genie, UC Function)
  nodes/           -- Pluggable node types (auto-discovered via pkgutil)
    base.py        -- BaseNode ABC, NodeConfigField, resolve_state()
    llm_node.py, router_node.py, vector_search_node.py, etc.

frontend/src/
  App.tsx           -- Main shell, view routing, state management
  api.ts            -- Backend API client
  types.ts          -- TypeScript interfaces
  components/
    Canvas.tsx      -- @xyflow graph editor
    ConfigPanel.tsx -- Node config form (auto-renders by field_type)
    DeployModal.tsx -- Deploy wizard
    SetupPage.tsx   -- Setup wizard
    ChatPlayground.tsx -- Preview/test agent

app.yaml           -- Databricks Apps runtime config
databricks.yml     -- Asset Bundle definition (app + OBO scopes)
deploy.sh          -- Build frontend + bundle deploy + app deploy
```

## Adding a New Node Type

1. Create `backend/nodes/your_node.py` with a class extending `BaseNode`, decorated with `@register`
2. Implement `node_type`, `display_name`, `config_fields`, and `execute(state, config)`
3. If it accesses Databricks resources: add resource extraction in `main.py:_extract_resources()` and OBO scopes in `databricks.yml`
4. If it can be an LLM tool: set `tool_compatible = True` and add a factory in `tools.py`
