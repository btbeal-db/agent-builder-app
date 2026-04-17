# Agent Sweet

Visual drag-and-drop [LangGraph](https://langchain-ai.github.io/langgraph/) agent builder for Databricks. Build, preview, and deploy AI agents — no code required.

**Built-in node types:** LLM, Router, Vector Search, Genie, UC Function, MCP Server, Human Input

## Deploy

No custom scopes, no infrastructure, no bundle variables. Just link the repo:

1. In your Databricks workspace, go to **Compute > Apps**
2. Click **Create App** and give it a name
3. Under **Git repository**, paste this repo's URL
4. Click **Deploy > From Git**, set the branch to `main`, and deploy

The app is live. See [Databricks Apps docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy/#deploy-from-a-git-repository) for details.

## First-Time Setup

Each user completes two steps before building agents:

### 1. MLflow experiment folder (one-time, on the Setup page)

This lets the app log MLflow models on your behalf when you deploy.

1. Create a folder under your user directory (e.g. `/Users/you@company.com/agent-sweet`)
2. The Setup page shows the app's service principal name — grant it **Can Manage** on your folder
3. Click **Validate** to confirm

### 2. Connect your PAT (each session, in the builder banner)

Click **Connect PAT** and paste a Personal Access Token. This lets the app access your workspace resources under your identity. See [Using the App](#using-the-app) for details.

## Using the App

### Connect your PAT

Before building, click **Connect PAT** in the banner at the top of the builder. Paste a Personal Access Token — this lets the app access your workspace resources (Vector Search indexes, Genie rooms, UC functions) under your identity.

**How to generate a PAT:** Go to **Settings > Developer > Access tokens** in your Databricks workspace ([docs](https://docs.databricks.com/en/dev-tools/auth/pat.html)).

**Safety:** Your token is held in browser memory only — it is never stored to disk, never logged, and is cleared when you close the tab or refresh. Treat it like a password: don't share it, and set a short expiration when possible.

### Build

Drag nodes onto the canvas, wire them together, and configure each node. Define your agent's state model in the left panel.

### Preview

Click **Playground** to test your agent with live data. If you've connected your PAT, previews run under your identity — you only see data you have access to.

### Deploy

Click **Deploy** and choose a deploy mode:

- **Log Only** — saves the agent as an MLflow model in your experiment folder. No PAT required.
- **Log & Register** or **Full Deploy** — also registers in Unity Catalog and (optionally) creates a serving endpoint.

If you've already connected your PAT in the banner, the deploy modal will pre-fill it.

## Security and Governance

Agent Sweet respects your existing Unity Catalog permissions. Here's how credentials work:

- **Your PAT** authenticates playground previews and deployment operations. The app uses it to access workspace resources (Vector Search, Genie, UC) under your identity. It is held in browser memory only for the duration of your session — never stored, never logged.
- **MLflow experiment logging** uses the app's service principal, scoped to folders you've explicitly shared during setup. The SP cannot access anything you haven't granted it.
- **Collaboration** is built in. If teammates complete setup, you can load each other's deployed graph definitions, iterate on them, and deploy to your own experiments.

### Why a PAT?

Databricks Apps support on-behalf-of (OBO) tokens for some APIs, but several critical scopes — including Vector Search, Unity Catalog writes, and model serving — are not available as OBO scopes. Rather than using the app's service principal as a proxy (which would require granting the SP access to every resource every user might reference), the app uses your PAT so that your own permissions apply directly. This is a platform limitation, not a design choice — if these OBO scopes become available in the future, the PAT requirement can be removed.

### Deployed endpoint permissions

When you deploy an agent, the app declares every external resource your graph references (serving endpoints, Vector Search indexes, Genie rooms, UC functions, and tables) as [MLflow model resources](https://docs.databricks.com/en/machine-learning/manage-model-lifecycle/index.html). At serving time, Model Serving uses **on-behalf-of (OBO) credentials** — each caller's request runs with their own identity and permissions. Your agent doesn't get blanket access to data; each caller only reaches what they're already allowed to see.

The app never creates shadow admin roles, never bypasses UC permissions, and never stores or logs your PAT.

## MCP Server Tools

The **MCP Server** node connects your agent to any [Model Context Protocol](https://modelcontextprotocol.io/) server and exposes its tools to LLM nodes. Drop an MCP Server onto an LLM node to give the LLM access to all of the server's tools — one URL is all you need.

### Supported server types

| Type | URL pattern | Example |
|---|---|---|
| **Managed MCP** | `<host>/api/2.0/mcp/functions/<catalog>/<schema>` | UC functions, Vector Search indexes, Genie rooms hosted by Databricks |
| **External MCP** | `<host>/api/2.0/mcp/external/<connection>` | Third-party servers (GitHub, Slack, etc.) proxied through a [Unity Catalog connection](https://docs.databricks.com/aws/en/generative-ai/mcp/external-mcp) |
| **Custom MCP** | Any Streamable HTTP URL | Your own MCP servers, including [FastMCP](https://gofastmcp.com/) apps deployed on Databricks Apps |

All three types work for both preview and deploy. Auth is handled automatically using `DatabricksOAuthClientProvider`.

### How it works

1. **Drag** an MCP Server node onto an LLM node (or add it as a standalone graph node)
2. **Paste** the server URL and optionally filter which tools to expose
3. **Preview** — the app discovers available tools from the server and binds them to the LLM
4. **Deploy** — tool metadata (names, descriptions, schemas) is persisted in the model artifact so the served endpoint never needs to re-contact the server for tool discovery

### Configuration

| Field | Required | Description |
|---|---|---|
| **Server URL** | Yes | The MCP server endpoint URL |
| **Tool Filter** | No | Comma-separated list of tool names to expose (empty = all) |
| **Tool Description** | No | Custom description telling the LLM when to use this tool |

When used as a standalone node (not attached to an LLM), you also specify a **Tool Name** to call directly and an **Input from** state variable.

## Conversational Agents and Lakebase

Agents with conversational (multi-turn) LLM nodes need persistent state between requests. Model Serving is stateless, so the app uses [Lakebase](https://docs.databricks.com/aws/en/oltp/) (Databricks-managed PostgreSQL) as a checkpoint store via LangGraph's `PostgresSaver`.

### How it works

At deploy time, the app configures the serving endpoint with Lakebase connection details. At serving time, the model creates a `psycopg` connection pool with a custom connection class that mints a fresh OAuth token on every new connection via `WorkspaceClient().postgres.generate_database_credential()`. Existing connections remain valid after the token expires — Lakebase enforces expiry only at login — so the pool handles rotation transparently.

### Deploy options

When deploying a full endpoint, you have three choices for Lakebase:

| Option | What you provide | What happens |
|---|---|---|
| **Create new** (recommended) | A project ID (e.g. `agent-sweet`) | The app provisions an Autoscaling Lakebase project and creates a per-agent database (e.g. `my-agent-checkpoints`) using your PAT. Multiple agents can share one project. |
| **Use existing** | Endpoint path, host, and database name | The app uses your existing Lakebase instance. Get these values from the Lakebase project page in your workspace. |
| **Connection string** (advanced) | A `postgresql://` URI | Static credential passed as-is. Note: Lakebase OAuth tokens expire after 1 hour, so this is mainly useful for non-Lakebase Postgres instances. |

### Prerequisites

Lakebase requires a workspace with serverless support.

### Default parameters

Auto-provisioned projects use these defaults:

- **Tier:** Autoscaling (0.5–1 CU, scale-to-zero enabled)
- **Branch:** `production` (created automatically)
- **Endpoint:** `primary` read-write (created automatically)
- **Database:** `{catalog}-{schema}-{model}-ckpt` (derived from your full UC model name, e.g. `catalog-schema-my-agent-ckpt`)
- **PostgreSQL version:** Latest supported (currently 16)

These are appropriate for checkpoint storage workloads. For production use with higher throughput, scale the endpoint via the Lakebase project page or CLI.

## Local Development

```bash
# Backend
uv run uvicorn backend.main:app --reload --port 8000

# Frontend (proxies /api to :8000)
cd frontend && npm run dev

# Run tests
uv run pytest -m "not integration" -q
```

CI runs `Frontend Build` and `Backend Tests` on every PR to `dev` and `main`. See [CONTRIB.md](CONTRIB.md) for the full developer guide.

## Troubleshooting

| Issue | Fix |
|---|---|
| Setup validation fails | Make sure you created a **folder** (not an MLflow experiment) and granted the SP "Can Manage" |
| Registration fails with auth error | Check that your PAT is valid and you have `CREATE MODEL` on the target catalog/schema |
| Endpoint creation fails | Verify your PAT has `CREATE SERVING ENDPOINT` permissions |
| `requirements-serving.txt` not found | Run `uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11` |
