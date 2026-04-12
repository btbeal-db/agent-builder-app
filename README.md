# Agent Sweet

Visual drag-and-drop [LangGraph](https://langchain-ai.github.io/langgraph/) agent builder for Databricks. Build, preview, and deploy AI agents — no code required.

**Built-in node types:** LLM, Router, Vector Search, Genie, UC Function, Human Input

## Deploy

1. In your Databricks workspace, go to **Compute > Apps**
2. Click **Create App** and give it a name
3. Under **Git repository**, paste this repo's URL
4. Click **Deploy > From Git**, set the branch to `main`, and deploy

That's it. The app is live. See [Databricks Apps docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy/#deploy-from-a-git-repository) for details.

## First-Time Setup

Each user completes a one-time setup before deploying agents. Open the app and navigate to the **Setup** page.

### Step 1: Create an experiment folder

In your Databricks workspace, create a folder under your user directory (e.g. `/Users/you@company.com/agent-sweet`). This is where your MLflow experiments will live.

### Step 2: Grant the app access

The Setup page will show the app's service principal name and ID. In your workspace, right-click the folder you created, go to **Permissions**, search for the service principal, and grant it **Can Manage**.

### Step 3: Validate

Click **Validate** on the Setup page. The app confirms it can write to your folder.

That's it. You're ready to build and deploy agents.

## Using the App

### Build

Drag nodes onto the canvas, wire them together, and configure each node. Define your agent's state model in the left panel.

### Preview

Click **Playground** to test your agent with live data. Previews use your own identity — you only see data you have access to.

### Deploy

Click **Deploy** and choose a deploy mode:

- **Log Only** — saves the agent as an MLflow model in your experiment folder. No PAT required. You can then register and deploy the model yourself from the Databricks UI (see [Deploy models from Model Registry](https://docs.databricks.com/en/machine-learning/manage-model-lifecycle/index.html)).
- **Log & Register** or **Full Deploy** — also registers in Unity Catalog and (optionally) creates a serving endpoint. These modes require a Personal Access Token (PAT).

**About your PAT:**
- Generate one at **Settings > Developer > Access tokens** in your Databricks workspace ([docs](https://docs.databricks.com/en/dev-tools/auth/pat.html))
- Your PAT is used only for the duration of the deploy request — the app never stores or logs it
- Treat your PAT like a password: don't share it, don't paste it into chat or email, and set a short expiration when possible
- If you're not comfortable providing a PAT, use **Log Only** mode and register/deploy from the Databricks UI instead

## Security and Governance

Agent Sweet respects your existing Unity Catalog permissions. Here's how credentials work:

- **Playground** uses your identity (OBO token). You only see data you already have access to.
- **Model registration and endpoint creation** use your Personal Access Token. Models are registered in catalogs you choose, that you have access to, under your identity.
- **MLflow experiment logging** uses the app's service principal, scoped to folders you've explicitly shared. The SP cannot access anything you haven't granted it.
- **Collaboration** is built in. If teammates complete setup, you can load each other's deployed graph definitions, iterate on them, and deploy to your own experiments. The SP's access boundary is the setup grant — nothing more.

### Deployed endpoint permissions

When you deploy an agent, the app declares every external resource your graph references (serving endpoints, Vector Search indexes, Genie rooms, UC functions, and tables) as [MLflow model resources](https://docs.databricks.com/en/machine-learning/manage-model-lifecycle/index.html). At serving time, Model Serving uses **on-behalf-of (OBO) credentials** — each caller's request runs with their own identity and permissions. Your agent doesn't get blanket access to data; each caller only reaches what they're already allowed to see.

The app never creates shadow admin roles, never bypasses UC permissions, and never stores or logs your PAT.

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
```

See [CONTRIB.md](CONTRIB.md) for the full developer guide.

## Troubleshooting

| Issue | Fix |
|---|---|
| Setup validation fails | Make sure you created a **folder** (not an MLflow experiment) and granted the SP "Can Manage" |
| Registration fails with auth error | Check that your PAT is valid and you have `CREATE MODEL` on the target catalog/schema |
| Endpoint creation fails | Verify your PAT has `CREATE SERVING ENDPOINT` permissions |
| `requirements-serving.txt` not found | Run `uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11` |
