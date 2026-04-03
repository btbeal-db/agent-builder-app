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

**Auth in local dev:** There is no OBO token locally. `get_workspace_client()` falls back to your Databricks CLI credentials (from `databricks auth login`). This means local preview uses your identity directly.

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
  nodes/               Pluggable node types (auto-discovered)
    base.py            BaseNode ABC + config field types
    llm_node.py        LLM node (ChatDatabricks)
    router_node.py     Conditional routing node
    vector_search_node.py
    genie_node.py
    uc_function_node.py
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

This is important to understand before contributing:

- **App auth (OBO):** When running as a Databricks App, each request includes the user's OAuth token via `x-forwarded-access-token`. The `OBOMiddleware` in `main.py` stores it in a contextvar. `get_workspace_client()` in `auth.py` uses this token to create a user-scoped `WorkspaceClient`.

- **SP credentials masking:** The SDK rejects having both a token and client credentials present. `get_workspace_client()` temporarily masks `DATABRICKS_CLIENT_ID`/`SECRET` from the env when creating an OBO client. Do NOT pass `auth_type` to `WorkspaceClient` — let the SDK auto-detect.

- **LLM calls use SP credentials:** LLM nodes call Foundation Model API endpoints using the app's service principal (default env vars). FMAPI does not accept OBO tokens.

- **Data-access calls use OBO:** Vector Search, Genie, UC Function nodes all call `get_workspace_client()` which uses the user's token for per-user access control.

- **Known limitation:** The `mlflow` OAuth scope is not available for Databricks Apps. All MLflow operations (log, register) must be done by the user in their own environment, not from the app. This is why deployment generates a notebook rather than deploying directly.

## Pull Requests

### Guidelines

- **One feature per PR.** Don't bundle unrelated changes. A new node type, a bug fix, and a UI tweak should be three separate PRs.
- **Keep diffs small.** If a feature touches many files, consider breaking it into stacked PRs (e.g., backend first, then frontend).
- **Don't refactor while fixing.** If you notice nearby code that could be improved, open a separate PR for it.
- **Test before opening.** Run the app locally and verify your change works in the preview canvas. If you're adding a node, test it with at least one graph that exercises it.
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

Tool functions use `get_workspace_client()` for data access — same OBO auth as nodes.

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

When adding integrations that call new Databricks APIs via OBO, you may need to add OAuth scopes. Scopes must be added in **two** places:

1. `databricks.yml` — for documentation (the SDK doesn't propagate these yet)
2. `deploy.sh` — the API call that actually sets scopes on the app

Known valid scopes: `catalog.catalogs:read`, `catalog.schemas:read`, `catalog.tables:read`, `dashboards.genie`, `serving.serving-endpoints`, `serving.serving-endpoints-data-plane`, `sql`, `vectorsearch.vector-search-endpoints`, `vectorsearch.vector-search-indexes`

If you get a 403 with "required scopes: X", add scope `X` to both places.

## Quick Reference

| What | Where | Required? |
|---|---|---|
| Node class with `@register` | `backend/nodes/your_node.py` | Yes |
| Icon mapping | `frontend/src/components/NodeIcon.tsx` | Only if new icon |
| Resource declarations | `backend/main.py` + `backend/notebook_gen.py` | Only if external resources |
| Tool factory | `backend/tools.py` | Only if tool-compatible |
| OAuth scopes | `databricks.yml` + `deploy.sh` | Only if new API access |
