# Contributing

## Developer Setup

### Prerequisites

- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v0.230+
- Node.js 18+ and npm
- Python 3.11 and [uv](https://docs.astral.sh/uv/)

### Getting started

```bash
git clone <repo-url> && cd agent-builder-app

# Authenticate with your workspace
databricks auth login --host https://your-workspace.cloud.databricks.com

# Install Python dependencies
uv sync

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### Running locally

```bash
# Terminal 1: backend (auto-reloads on save)
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2: frontend with hot reload
cd frontend && npm run dev
```

The Vite dev server runs on `:3000` and proxies `/api` requests to the backend on `:8000`.

**Auth in local dev:** There is no OBO token locally. `get_workspace_client()` falls back to your Databricks CLI credentials (from `databricks auth login`). You can also paste a PAT in the builder banner — this works the same locally as in production.

### Building for deployment

```bash
cd frontend && npm run build    # outputs to backend/static/
```

The FastAPI backend serves the built frontend as static files in production.

## Project Structure

```
backend/
  main.py              FastAPI app, API endpoints, middleware
  auth.py              OBO token handling + WorkspaceClient creation
  schema.py            Pydantic models (graph, deploy, preview)
  graph_builder.py     Compiles GraphDef → LangGraph StateGraph
  mlflow_model.py      MLflow ResponsesAgent wrapper for Model Serving
  notebook_gen.py      Generates deployment notebooks
  tools.py             LangChain tool factory (VS, Genie, UC Function)
  ai_chat.py           AI chat assistant for graph building
  lakebase.py          Lakebase provisioning + Postgres connection pool
  nodes/               Pluggable node types (auto-discovered)
    base.py            BaseNode ABC + config field types
    llm_node.py        LLM node (ChatDatabricks)
    router_node.py     Conditional routing node
    vector_search_node.py
    genie_node.py
    uc_function_node.py
    mcp_node.py        MCP server integration
    human_input_node.py

frontend/
  src/
    App.tsx            Main app shell
    api.ts             Backend API client
    types.ts           TypeScript interfaces
    StateContext.tsx    Global state (nodes, edges, etc.)
    components/        UI components (canvas, panels, modals)

app.yaml               Databricks Apps runtime config
databricks.yml         Asset Bundle definition (app name, scopes)
deploy.sh              Build + deploy helper script
pyproject.toml         Python package definition (hatchling)
```

## Authentication Model

This is important to understand before contributing. The app uses three credential types, and the reasons for each are rooted in platform limitations — not design preference.

### Why the PAT?

Databricks Apps inject an on-behalf-of (OBO) OAuth token for each logged-in user. In theory, this token should let the app call any Databricks API as the user. In practice, many critical OBO scopes either don't exist or don't work:

| Scope needed | Status (as of April 2026) |
|---|---|
| `vector-search` | **Not a valid OBO scope.** The Vector Search API requires it, but the Apps platform rejects it as an invalid scope when configured. |
| `unity-catalog` | **Scope exists but returns 403.** Catalog read scopes (`catalog.*:read`) work; write operations and `tables.get` do not. |
| `model-serving` (write) | **Unreliable.** Appears in OAuth discovery but doesn't grant access. |
| `mlflow` / `ml.experiments` | **Does not exist** as an OBO scope. |

We verified this empirically: a bare-bones `/api/test-vs` endpoint (still in `main.py`) creates a `WorkspaceClient` directly from the OBO token and calls `query_index`. Every auth combination (with/without `auth_type="pat"`, with/without env var masking) returns the same scope error. The test endpoint is kept for future regression testing as the platform evolves.

Because of these gaps, the app asks users to provide a Personal Access Token (PAT) via a banner in the builder UI. The PAT has full permissions under the user's identity, bypassing all scope limitations. **If these OBO scopes become available in the future, the PAT requirement can be removed** — the fallback path through `get_workspace_client()` (OBO) is already in place.

### Credential types

#### User PAT (preferred for data access)

Users paste their PAT in the builder banner. It's stored in a React `useState` (browser memory only — no localStorage, no cookies, no disk) and sent with each preview/deploy request. On the backend, it's held in a `ContextVar` for the request lifetime and explicitly cleared in `finally`.

`get_data_client()` in `auth.py` checks for a PAT first, then falls back to OBO. All data-access nodes (Vector Search, Genie) and their tool equivalents call `get_data_client()`.

**Used for:** Preview/playground (VS, Genie, UC Functions), model registration, schema creation, serving endpoint creation.

#### OBO (On-Behalf-Of) Token

Injected by the Apps proxy via `x-forwarded-access-token`. `OBOMiddleware` stores it in a contextvar. `get_workspace_client()` creates a user-scoped `WorkspaceClient` from it, masking SP env vars and passing `auth_type="pat"` (both required — see below).

**Used for:** User identity resolution (setup status, `current_user.me()`), SQL queries, Genie (when no PAT is provided). Also serves as the fallback in `get_data_client()` when no PAT is available — works for APIs with valid OBO scopes, fails for VS.

**Important SDK behavior:** The Databricks SDK loads `DATABRICKS_CLIENT_ID`/`SECRET` from the environment into its Config even when you pass `token=`. If both are present, the server treats the request as an SP OAuth call (wrong scopes). `get_workspace_client()` masks these env vars before creating the client and passes `auth_type="pat"` to force bearer-token auth. Both are required.

#### Service Principal (SP)

Env vars `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` injected by the platform. Use `get_sp_workspace_client()`.

**Used for:** LLM calls (FMAPI rejects OBO tokens), MLflow experiment logging, setup config persistence.

### Summary

| Operation | Auth | Why |
|---|---|---|
| Preview — VS, Genie, UC Functions | PAT > OBO | PAT preferred; OBO lacks `vector-search` scope |
| Preview — LLM inference | SP | FMAPI rejects OBO and PAT |
| User identity | OBO | `iam.current-user:read` scope works |
| MLflow experiment logging | SP | No OBO scope for MLflow |
| Setup config persistence | SP | Workspace file write (SP has Can Manage) |
| UC model registration | PAT | No UC write OBO scopes |
| Serving endpoint creation | PAT | `model-serving` OBO scope unreliable |

### Implementation detail

`create_pat_client()` in `auth.py` handles PAT-authenticated `WorkspaceClient` creation with env var masking and `auth_type="pat"`. For `mlflow.register_model()`, we run in a subprocess because MLflow caches `DatabricksConfig` via `@lru_cache` — env-var masking in the same process doesn't override it. See `_register_model_with_pat()` in `main.py`.

## Lakebase Connection Handling

Conversational agents need persistent state. The app uses Lakebase (Databricks-managed PostgreSQL) as a checkpoint store. Understanding the connection pattern matters if you're modifying the deploy flow or `mlflow_model.py`.

### Why not a static connection string?

Lakebase Autoscaling uses OAuth tokens that expire after 1 hour. A `postgresql://user:token@host/db` URI baked into an env var stops working after the first hour. Model Serving endpoints are long-running, so static credentials don't work.

### The pattern: ConnectionPool with token refresh

Instead of a static connection string, the serving endpoint gets three env vars:

| Env var | Example | Purpose |
|---|---|---|
| `LAKEBASE_ENDPOINT` | `projects/my-agent/branches/production/endpoints/primary` | Resource path for `generate_database_credential()` |
| `LAKEBASE_HOST` | `ep-abc123.database.us-east-1.databricks.com` | Postgres hostname |
| `LAKEBASE_DATABASE` | `checkpoints` | Postgres database name |

At serving time (`mlflow_model.py`), the model creates a `psycopg_pool.ConnectionPool` with a custom `psycopg.Connection` subclass. Each time the pool opens a new connection, the subclass calls `WorkspaceClient().postgres.generate_database_credential(endpoint=...)` to get a fresh 1-hour OAuth token and passes it as the password.

Key facts:
- **Open connections survive token expiry.** Lakebase enforces expiry only at login time, not on established connections.
- **The pool handles rotation transparently.** Existing connections keep working; new ones get fresh tokens.
- **`WorkspaceClient()` auto-detects credentials** in the Model Serving environment (SP identity).
- **`PostgresSaver` accepts `ConnectionPool` directly** — LangGraph calls `pool.connection()` for each checkpoint read/write.

A `LAKEBASE_CONN_STRING` fallback exists for non-Lakebase Postgres instances but should be avoided for Lakebase deployments.

### The deploy flow

1. User chooses "Create new" or "Use existing" in the Deploy modal
2. If "Create new": the deploy endpoint calls `provision_lakebase()` (in `backend/lakebase.py`) using the user's PAT to create a project + database
3. The three env vars are injected on the `ServedEntityInput` when creating the serving endpoint
4. If the user provides a raw connection string instead, it's injected as `LAKEBASE_CONN_STRING` (legacy path)

### Auth summary for Lakebase

| When | Who | How |
|---|---|---|
| Provisioning (deploy time) | User | PAT-authenticated `WorkspaceClient` |
| Token generation (serving time) | Service Principal | SP credentials auto-detected by `WorkspaceClient()` |
| Postgres connection (serving time) | SP identity | OAuth token as password, SP email as username |

## Branching Model

| Branch | Purpose | Merges from | Protected |
|---|---|---|---|
| `main` | Production — deployed to the Databricks App | `dev` | Yes |
| `dev` | Integration — all feature work lands here first | feature branches | Yes |
| `your-name/description` | Feature branches | — | No |

**Workflow:** Create a feature branch from `dev`, open a PR back to `dev`. After testing on `dev`, promote to `main` via a separate PR.

### Branch protections

Both `dev` and `main` require:

- A pull request (no direct push)
- CI checks passing (`Frontend Build` + `Backend Tests`)
- 1 approval from a reviewer (repo admins can bypass)

## CI Pipeline

CI runs on every push and PR to `dev` and `main` (`.github/workflows/ci.yml`).

| Job | What it does |
|---|---|
| **Frontend Build** | `npm ci` + `npm run build` (includes TypeScript type checking) |
| **Backend Tests** | `uv sync --dev` + `pytest -m "not integration"` |

Tests marked `@pytest.mark.integration` (e.g., those requiring live Databricks credentials) are skipped in CI. Run them locally:

```bash
uv run pytest  # runs all tests including integration
```

## Pull Requests

### Guidelines

- **One feature per PR.** Don't bundle unrelated changes. A new node type, a bug fix, and a UI tweak should be three separate PRs.
- **Keep diffs small.** If a feature touches many files, consider breaking it into stacked PRs (e.g., backend first, then frontend).
- **Don't refactor while fixing.** If you notice nearby code that could be improved, open a separate PR for it.
- **Test before opening.** Run the app locally and verify your change works in the preview canvas. If you're adding a node, test it with at least one graph that exercises it.
- **Run tests locally.** `uv run pytest -m "not integration" -q` should pass before you open a PR.
- **Update CONTRIB.md** if your change adds new patterns, config field types, or resource declarations that other contributors need to know about.

### Branch naming

Use `your-name/short-description` — e.g., `brennan.beal/add-sql-warehouse-node`.

### PR template

The repo includes a PR template at `.github/pull_request_template.md`. Fill it out when opening a PR — don't delete sections, mark them N/A if they don't apply.

---

## Adding a New Node Type

The agent builder uses auto-discovery — drop a file in `backend/nodes/` and it appears in the UI automatically.

### 1. Create the node file

Add `backend/nodes/your_node.py`:

```python
from __future__ import annotations
from typing import Any
from .base import BaseNode, NodeConfigField
from . import register

@register
class YourNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "your_node"

    @property
    def display_name(self) -> str:
        return "Your Node"

    @property
    def description(self) -> str:
        return "One-line description shown in the palette."

    @property
    def category(self) -> str:
        return "action"  # model | retrieval | action | control

    @property
    def icon(self) -> str:
        return "puzzle"  # must be in NodeIcon.tsx ICON_MAP

    @property
    def color(self) -> str:
        return "#6366f1"  # hex color for the node header

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(name="param1", label="Param 1"),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        # ... your logic ...
        return {
            writes_to: result,
            "messages": [{"role": "system", "content": "...", "node": "your_node"}],
        }
```

That's it for the backend. The `@register` decorator + `pkgutil` auto-import in `__init__.py` handles discovery. The node will appear in the palette and config panel automatically.

### 2. Add icon mapping (if using a new icon)

In `frontend/src/components/NodeIcon.tsx`, add your icon to `ICON_MAP`:

```tsx
import { YourIcon } from "lucide-react";
// ...
const ICON_MAP = {
  // ...existing...
  "your-icon": YourIcon,
};
```

Unmapped icons fall back to the puzzle piece. Browse options at [lucide.dev](https://lucide.dev).

### 3. Add resource declarations (if accessing Databricks resources)

If your node references external resources (endpoints, tables, functions, etc.), update `backend/main.py` so Model Serving provisions credentials:

1. Add to `resource_map` in `_extract_resources()`
2. Add to `init_param` mapping
3. Add to `_RESOURCE_MAP` in `backend/notebook_gen.py` (for generated deploy notebooks)

Available resource classes:

| Class | Config field example | Used by |
|---|---|---|
| `DatabricksServingEndpoint` | `endpoint`, `endpoint_name` | LLM, Vector Search |
| `DatabricksVectorSearchIndex` | `index_name` | Vector Search |
| `DatabricksGenieSpace` | `room_id` | Genie |
| `DatabricksFunction` | `function_name` | UC Function |
| `DatabricksTable` | `table_name` | — |

### 4. Add as a tool (if tool-compatible)

If your node can also be used as a tool attached to an LLM node:

1. Set `tool_compatible = True` on the node class
2. Add a tool factory function in `backend/tools.py` (e.g., `_make_your_tool()`)
3. Register it in the `make_tools()` switch statement

Tool functions use `get_data_client()` for data access — PAT when available, OBO fallback.

## Config Field Types

The config panel renders fields automatically based on `field_type`:

| `field_type` | Renders as | Notes |
|---|---|---|
| `"text"` | Text input | Default |
| `"number"` | Number input | |
| `"textarea"` | Multi-line text | |
| `"select"` | Dropdown | Provide `options` list |
| `"state_variable"` | Dropdown of state vars | Supports dot-path for structured fields |
| `"schema_editor"` | Structured field editor | For LLM structured output |
| `"route_editor"` | Route builder | Router nodes only |

## Reading from State

Use `resolve_state()` from `base.py` when your node reads from a user-selected state variable. It handles both flat keys (`"input"`) and dot-paths into structured fields (`"output.query_filters"`):

```python
from .base import resolve_state

query = resolve_state(state, config.get("query_from", "input"))
```

## Router Nodes

If your node branches the graph:

1. Add `is_router = True` property
2. Implement `get_route_names(config) -> list[str]`
3. Return `{"_route": chosen_key}` from `execute()`

See `backend/nodes/router_node.py` for reference.

## Execute Contract

Your `execute()` receives:
- `state` — full agent state dict
- `config` — user-supplied config values, plus `_writes_to` (target state field) and `_target_field` (the `StateFieldDef`)

It must return a dict with:
- `writes_to: value` — the result to write into the target state field
- `"messages": [...]` — list of message dicts for the execution trace (each with `role`, `content`, `node`)

## App Scopes

When adding integrations that call new Databricks APIs via OBO, you may need to add OAuth scopes in `databricks.yml` under `user_api_scopes`.

**Currently configured scopes:**

| Scope | Purpose |
|---|---|
| `sql` | SQL warehouse queries, statement execution |
| `serving.serving-endpoints` | Query serving endpoints (LLM nodes) |
| `catalog.catalogs:read` | Validate catalog access at deploy time |
| `catalog.schemas:read` | Validate schema access at deploy time |
| `catalog.tables:read` | Read UC tables |
| `vectorsearch.vector-search-endpoints` | Vector Search endpoint access |
| `vectorsearch.vector-search-indexes` | Vector Search index queries |

**Discovering available scopes:** Fetch the full list from your workspace's OAuth discovery endpoint:

```bash
databricks api get /oidc/.well-known/oauth-authorization-server | jq '.scopes_supported'
```

**Important:** Not all scopes listed in the OAuth discovery endpoint work as OBO scopes for Apps. In particular, `vector-search` (required by the VS API) is not a valid configurable scope, and `mlflow`, `unity-catalog`, and `model-serving` appear in the list but do not reliably grant access. This is why the app uses PATs for data-access operations and SP for MLflow — see the Authentication Model section above.

If you get a 403 with "required scopes: X", first check whether the scope is actually available as an OBO scope (it may not be). The `/api/test-vs` diagnostic endpoint in `main.py` can help verify.

## Quick Reference

| What | Where | Required? |
|---|---|---|
| Node class with `@register` | `backend/nodes/your_node.py` | Yes |
| Icon mapping | `frontend/src/components/NodeIcon.tsx` | Only if new icon |
| Resource declarations | `backend/main.py` + `backend/notebook_gen.py` | Only if external resources |
| Tool factory | `backend/tools.py` | Only if tool-compatible |
| OAuth scopes | `databricks.yml` + `deploy.sh` | Only if new API access |
