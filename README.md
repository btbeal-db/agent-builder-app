# Agent Builder

Visual drag-and-drop [LangGraph](https://langchain-ai.github.io/langgraph/) agent builder for Databricks. Build, preview, and deploy AI agents — no code required.

**Built-in node types:** LLM, Router, Vector Search, Genie, UC Function, Human Input

## Prerequisites

- Databricks workspace with Unity Catalog enabled
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v0.230+
- Node.js 18+ and npm
- Python 3.11 and [uv](https://docs.astral.sh/uv/)

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url> && cd agent-builder-app
databricks auth login --host https://your-workspace.cloud.databricks.com
```

### 2. Install dependencies

```bash
uv sync
cd frontend && npm install && cd ..
```

### 3. Deploy to Databricks

```bash
# First time — creates the app and provisions compute (~2-3 min)
./deploy.sh --profile DEFAULT --init

# Subsequent deploys — syncs code + redeploys (~30 sec)
./deploy.sh --profile DEFAULT
```

Replace `DEFAULT` with the name of your Databricks CLI profile (run `databricks auth profiles` to list them).

### 4. Local development (optional)

```bash
# Terminal 1: backend
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2: frontend with hot reload
cd frontend && npm run dev
```

The frontend dev server proxies `/api` requests to the backend on port 8000.

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
| `requirements-serving.txt` not found during deploy | Run `uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11` |
| Stale deployment state / workspace mismatch | Run `./deploy.sh dev --clean` to clear Terraform state |
| Auth errors | Run `databricks auth env` to verify credentials, then `databricks auth login` to refresh |
| Frontend not loading after deploy | Ensure you ran `cd frontend && npm run build` (or use `deploy.sh` which does this automatically) |
