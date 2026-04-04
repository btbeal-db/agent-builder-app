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

The app lets you visually build agent graphs and deploy them as Model Serving endpoints. Deployment is handled by a background Databricks Job (configured during app setup) so that users don't need direct MLflow or catalog permissions.

The deploy flow:

1. **Validate** — the app compiles the graph locally
2. **Submit Job** — the app triggers a pre-configured Job that:
   - Installs the app package (from the same Git branch the app is running)
   - Logs the agent as an MLflow model with resource declarations
   - Registers the model in the configured Unity Catalog schema
   - Creates a Model Serving endpoint with AI Gateway and inference tables
3. **Poll** — the app polls the Job for completion and shows the result

### Admin Setup

Before users can deploy, a workspace admin must:

1. Set `DEPLOY_CATALOG` and `DEPLOY_SCHEMA` in `app.yaml`
2. Run `./deploy.sh --profile <name> --init` to create the Job and grant permissions
3. Or configure manually: create a Job pointing to the deploy notebook, add it as an App Resource, and set `DEPLOY_JOB_ID` in `app.yaml`

See [deploy.sh](deploy.sh) for the full automated setup.

## Authentication & Security Model

There are three distinct auth contexts in this app. Understanding them is important for workspace admins.

### 1. App (playground/preview)

When users build and test agents in the browser, the app runs data-access calls (Vector Search, Genie, UC Functions) using the **user's identity** via the OBO token (`x-forwarded-access-token`). Users can only query resources they personally have access to. LLM calls use the app's service principal (Foundation Model API doesn't accept OBO tokens).

### 2. Deploy Job

The deploy Job runs as the **Job owner's service principal**, not the user. It needs:
- `USE CATALOG` + `USE SCHEMA` + `CREATE MODEL` on the target catalog/schema
- Permissions to create serving endpoints

The deploying user's email is tagged on the MLflow run (`deployed_by`) for provenance tracking.

### 3. Deployed Model (serving endpoint)

The deployed agent uses **automatic authentication passthrough** — Model Serving provisions a scoped service principal with access to each declared resource (VS indexes, Genie rooms, LLM endpoints, etc.). This means:

- **Any user who can query the endpoint** gets access to the agent's declared resources through the endpoint's SP
- Resource access is determined at deploy time by what the user configured in the graph, not by the caller's identity
- The Job SP must have sufficient access for Model Serving to validate and provision the resource declarations

### Security Considerations

| Concern | Mitigation |
|---|---|
| User deploys agent referencing resources they don't own | The app validates the graph in the playground using OBO — if a user can't query a resource in preview, they'll know before deploying. Model Serving also validates resource access at endpoint creation time. |
| Deploy Job has broad permissions | The Job SP only needs `CREATE MODEL` on one configured catalog/schema. Resource provisioning is handled by Model Serving, not the Job. |
| Anyone can query a deployed endpoint | Secure endpoints using [endpoint permissions](https://docs.databricks.com/en/security/auth/tokens.html). Restrict who can query each endpoint. |
| Provenance / audit | Each MLflow run is tagged with `deployed_by` (user email), `agent_name`, and `endpoint_name`. |

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
| Deploy modal says "App not configured" | Set `DEPLOY_CATALOG`, `DEPLOY_SCHEMA`, and `DEPLOY_JOB_ID` in `app.yaml` |
| Deploy Job fails with pip install error | Ensure the Git repo is public, or configure Git credentials on the Job cluster |
| Endpoint container build fails | Check `requirements-serving.txt` targets Python 3.10 (`uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.10`) |
| Vector Search / Genie 403 errors in playground | Add missing scopes via `databricks api patch` — see `deploy.sh` for the full list |
| App deploy fails with "user token passthrough not enabled" | Ask your workspace admin to enable Apps user token passthrough |
| Stale deployment state / workspace mismatch | Run `./deploy.sh dev --clean` to clear Terraform state |
