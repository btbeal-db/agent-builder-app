# Contributing

## Developer Setup

### Prerequisites

- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v0.230+
- Node.js 18+ and npm
- Python 3.11 and [uv](https://docs.astral.sh/uv/)

### Getting started

```bash
git clone <repo-url> && cd agent-sweet

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

**Auth in local dev:** There is no OBO token locally. `_get_mcp_client()` falls through to `get_data_client()` which uses your Databricks CLI credentials (from `databricks auth login`). MCP URLs are built from `DATABRICKS_HOST` in your environment. You can also paste a PAT in the builder banner for testing.

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
  tools.py             LangChain tool factory (all tools route through managed MCP)
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

This is important to understand before contributing. The app uses three credential types.

### Managed MCP routing (the key design decision)

All data-access nodes (Vector Search, Genie, UC Functions) route through [Databricks managed MCP servers](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp) instead of calling the SDK directly. This is the mechanism that eliminates the PAT requirement for preview.

**Background:** The direct SDK APIs require OBO scopes (`vector-search`, `genie`, etc.) that Databricks Apps cannot configure — the platform rejects them. We verified this empirically with a diagnostic endpoint. However, Databricks _does_ support `mcp.*` OBO scopes that route requests through the MCP proxy gateway:

| Node type | MCP URL pattern | OBO scope |
|---|---|---|
| Vector Search | `/api/2.0/mcp/vector-search/{catalog}/{schema}/{index}` | `mcp.vectorsearch` |
| Genie | `/api/2.0/mcp/genie/{room_id}` | `mcp.genie` |
| UC Functions | `/api/2.0/mcp/functions/{catalog}/{schema}/{function}` | `mcp.functions` |
| External MCP | `/api/2.0/mcp/external/{connection}` | `mcp.external` |

These scopes are declared in `databricks.yml` under `user_api_scopes`. The OBO token from the Apps proxy carries these scopes, and the MCP servers enforce Unity Catalog permissions server-side.

**If direct SDK OBO scopes (`vector-search`, `genie`, etc.) become available in the future**, the MCP routing can be replaced with direct SDK calls — the auth fallback path through `get_data_client()` is still in place. This would eliminate the ~1-2s per-tool MCP protocol overhead (see Latency section below).

### Dual execution paths (SDK vs MCP)

Serving endpoints don't need MCP — their credentials (user OBO or SP) work with the SDK directly. Only the app preview needs MCP because the Apps OBO token only has `mcp.*` scopes. So we maintain two execution paths to avoid unnecessary latency:

| Context | `is_serving()` | Tool path | Why |
|---|---|---|---|
| App preview | `False` | Managed MCP | Apps OBO token only has `mcp.*` scopes |
| Serving — OBO | `True` | Direct SDK | `ModelServingUserCredentials` works with SDK |
| Serving — Passthrough | `True` | Direct SDK | SP credentials work with SDK |

`is_serving()` is a process-global flag in `auth.py`, set once by `mlflow_model.py` at model load time. In the app process it stays `False`. The tool factories (`_make_vector_search_tool`, etc.) and node `execute()` methods branch on this flag.

Each tool factory has two implementations suffixed `_sdk` and `_mcp` (e.g., `_make_vector_search_tool_sdk` and `_make_vector_search_tool_mcp`). The public function routes between them. Same pattern in the node files (`_execute_sdk` / `_execute_mcp` methods).

### MCP latency (app preview only)

MCP adds ~1-2s fixed overhead per tool call (session setup). This only affects the playground, not deployed endpoints. Measured April 2026:

| Query type | MCP (preview) | Direct SDK (serving) | Delta |
|---|---|---|---|
| VS + Genie (fast Genie) | 21.6s | 16.6s | +5.0s |
| Genie-heavy | 15.9s | 12.0s | +3.9s |
| Long Genie query | 22.3s | 21.7s | +0.6s |

The overhead is constant per tool call, not proportional to execution time. Optimization opportunities (session reuse, parallel execution) are tracked in #14.

### Reversibility

If Databricks adds direct SDK OBO scopes (`vector-search`, `genie`, etc.) for Apps, the MCP path can be removed entirely — just delete the `_mcp` variants, the `is_serving()` checks, and the MCP URL builders. The `_sdk` implementations would become the only path.

### VS configuration via `_meta`

Vector Search config options (reranker, columns, score threshold, filters, query type) are passed to the MCP server via the MCP spec's [`_meta` parameter](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp-meta-param), not as tool arguments. This separates user-preset configuration from LLM-generated query parameters. See `_build_vs_meta()` in `tools.py`. Genie and UC Functions do not currently support `_meta` parameters.

### Credential types

#### OBO (On-Behalf-Of) Token — primary for data access

Injected by the Apps proxy via `x-forwarded-access-token`. `OBOMiddleware` stores it in a contextvar. `_get_mcp_client()` in `tools.py` prefers this token for managed MCP URLs and Databricks Apps URLs.

**Used for:** All data access in preview (VS, Genie, UC Functions via managed MCP), user identity resolution.

**Important SDK behavior:** The Databricks SDK loads `DATABRICKS_CLIENT_ID`/`SECRET` from the environment into its Config even when you pass `token=`. If both are present, the server treats the request as an SP OAuth call (wrong scopes). `get_workspace_client()` masks these env vars before creating the client and passes `auth_type="pat"` to force bearer-token auth. Both are required.

#### User PAT (deploy operations only)

Users paste a PAT in the deploy modal for UC model registration and serving endpoint creation. Stored in browser memory only — no localStorage, no cookies, no disk. On the backend, held in a `ContextVar` for the request lifetime and cleared in `finally`.

**Used for:** UC model registration, serving endpoint creation (OBO scopes for these don't work).

#### Service Principal (SP)

Env vars `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` injected by the platform. Use `get_sp_workspace_client()`.

**Used for:** LLM calls (FMAPI rejects OBO tokens), MLflow experiment logging, setup config persistence.

### Summary

| Operation | Auth | Why |
|---|---|---|
| Preview — VS, Genie, UC Functions | OBO (via managed MCP) | `mcp.*` scopes in `databricks.yml` |
| Preview — MCP Server nodes | OBO > PAT | `DatabricksOAuthClientProvider`; managed URLs prefer OBO |
| Preview — LLM inference | SP | FMAPI rejects OBO and PAT |
| User identity | OBO | `iam.current-user:read` scope works |
| Folder creation + SP `Can Manage` grant (first-sign-in setup) | OBO | `workspace.workspace` scope; user owns folders under `/Users/{their_email}/...` |
| MLflow experiment logging | SP | No OBO scope for MLflow |
| Setup config persistence | SP | Workspace file write (SP has Can Manage) |
| UC model registration | PAT | No UC write OBO scopes |
| Serving endpoint creation | PAT | `model-serving` OBO scope unreliable |

### Implementation details

- `is_serving()` in `auth.py` — process-global flag set by `mlflow_model.py`. Tool factories and node `execute()` methods branch on this to choose SDK vs MCP path.
- `_managed_mcp_url()` in `tools.py` builds MCP URLs from node config. Ensures `https://` prefix since `DATABRICKS_HOST` may not include it in Apps environments.
- `_persist_mcp_tool_metadata()` in `main.py` runs at deploy time: discovers MCP tools and persists both `discovered_tools` (tool schemas) and `mcp_server_url` (fully-qualified URL) into the graph artifact. Serving containers use the `_sdk` path so these are mainly for the MCP fallback.
- `create_pat_client()` in `auth.py` handles PAT-authenticated `WorkspaceClient` creation with env var masking. For `mlflow.register_model()`, we run in a subprocess because MLflow caches `DatabricksConfig` via `@lru_cache` — env-var masking in the same process doesn't override it.

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

Tool functions route through managed MCP using `_get_mcp_client()` — OBO preferred for managed MCP URLs, `get_data_client()` fallback for everything else.

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

The app declares OBO scopes in `databricks.yml` under `user_api_scopes`. These are the scopes the user's OAuth token will carry when they log in to the app.

**Currently configured scopes:**

| Scope | Purpose |
|---|---|
| `mcp.vectorsearch` | Vector Search queries via managed MCP |
| `mcp.genie` | Genie space queries via managed MCP |
| `mcp.functions` | UC function execution via managed MCP |
| `mcp.external` | External MCP connections (UC connection proxy) |

Default scopes (`iam.current-user:read`, `iam.access-control:read`) are included automatically.

**When you change scopes**, users must log out and back in (or use an incognito window) to get a new OAuth token with the updated scopes. This is a common gotcha — if preview returns 403 after a scope change, it's usually a stale token.

**Discovering available scopes:**

```bash
databricks api get /oidc/.well-known/oauth-authorization-server | jq '.scopes_supported'
```

**Important:** Not all scopes listed in the OAuth discovery endpoint work as OBO scopes for Apps. Direct SDK scopes like `vector-search`, `genie`, `unity-catalog`, and `model-serving` appear in the list but do not reliably grant access — this is why all data access routes through managed MCP (`mcp.*` scopes) rather than the direct SDK. If direct SDK OBO scopes become available in the future, the MCP routing can be replaced to eliminate the protocol overhead.

## Quick Reference

| What | Where | Required? |
|---|---|---|
| Node class with `@register` | `backend/nodes/your_node.py` | Yes |
| Icon mapping | `frontend/src/components/NodeIcon.tsx` | Only if new icon |
| Resource declarations | `backend/main.py` + `backend/notebook_gen.py` | Only if external resources |
| Tool factory | `backend/tools.py` | Only if tool-compatible |
| OAuth scopes | `databricks.yml` + `deploy.sh` | Only if new API access |
