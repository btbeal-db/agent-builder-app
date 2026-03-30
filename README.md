# Agent Builder

Visual drag-and-drop [LangGraph](https://langchain-ai.github.io/langgraph/) agent builder for Databricks. Build, preview, and deploy AI agents — no code required.

**Built-in node types:** LLM, Router, Vector Search, Genie, UC Function, Human Input

## Prerequisites

- Databricks workspace with Unity Catalog enabled and [Apps user token passthrough](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) enabled

## Deploy from Git (recommended)

The easiest way to deploy is directly from this GitHub repository — no local clone or build tools needed.

1. In your Databricks workspace, go to **Compute → Apps**
2. Click **Create App** and give it a name
3. Under **Git repository**, paste this repo's URL and select your Git provider
4. For private repos, click **Configure Git credential** to add access
5. Click **Create app**
6. On the app details page, click **Deploy → From Git**
7. Set the **Git reference** to `main` and **Reference type** to `Branch`
8. Click **Deploy**

For more details, see [Deploy from a Git repository](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy/#deploy-from-a-git-repository).

## Local Development (optional)

If you want to run locally or contribute changes:

### Prerequisites

- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v0.230+
- Node.js 18+ and npm
- Python 3.11 and [uv](https://docs.astral.sh/uv/)

### Setup

```bash
git clone <repo-url> && cd agent-builder-app
databricks auth login --host https://your-workspace.cloud.databricks.com
uv sync
cd frontend && npm install && cd ..
```

### Run locally

```bash
# Terminal 1: backend
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2: frontend with hot reload
cd frontend && npm run dev
```

The frontend dev server proxies `/api` requests to the backend on port 8000.

### Deploy via CLI

```bash
# First time — creates the app and provisions compute (~2-3 min)
./deploy.sh --profile DEFAULT --init

# Subsequent deploys — syncs code + redeploys (~30 sec)
./deploy.sh --profile DEFAULT
```

Replace `DEFAULT` with the name of your Databricks CLI profile (run `databricks auth profiles` to list them).

## Architecture

```
frontend/              React/Vite UI (builds to backend/static/)
backend/               FastAPI app + LangGraph agent engine
  nodes/               Pluggable node types (auto-discovered)
  mlflow_model.py      MLflow pyfunc wrapper for serving deployed agents
demo/                  Optional: sample data setup script
app.yaml               Databricks Apps runtime config
databricks.yml         Databricks Asset Bundle definition
deploy.sh              Build + deploy helper script
```

When deployed as a **Databricks App**, the platform automatically injects workspace credentials and the user's identity token (OBO). The FastAPI backend serves both the API and the built frontend static files.

## Deploying Agents (from the UI)

The app lets you visually build agent graphs and deploy them as Model Serving endpoints. The deploy flow:

1. **Log** — saves the agent as an MLflow model with its graph definition as an artifact
2. **Register** — registers the model version in Unity Catalog
3. **Serve** — creates a Model Serving endpoint with AI Gateway and inference tables

**Required permissions:**
- `CREATE MODEL` on the target UC catalog/schema
- `CREATE SERVING ENDPOINT` on the workspace
- Access to any resources referenced by your nodes (serving endpoints, vector search indexes, Genie rooms, UC functions)

## Demo Data (optional)

To set up sample vector search indexes and Genie rooms for testing:

```bash
python demo/setup_demo.py
```

See `demo/README.md` for options (custom catalog/schema, teardown, etc.).

## Adding Custom Nodes

See [CONTRIB.md](CONTRIB.md) for how to create and register new node types.

## Troubleshooting

| Issue | Fix |
|---|---|
| App deploy fails with "user token passthrough not enabled" | Ask your workspace admin to enable Apps user token passthrough |
| `requirements-serving.txt` not found during CLI deploy | Run `uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11` |
| Stale deployment state / workspace mismatch | Run `./deploy.sh dev --clean` to clear Terraform state |
| Auth errors (CLI deploy) | Run `databricks auth env` to verify credentials, then `databricks auth login` to refresh |
