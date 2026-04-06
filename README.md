# Agent Sweet

Visual drag-and-drop [LangGraph](https://langchain-ai.github.io/langgraph/) suite of tools to build agents on Databricks with no code required.

**Built-in node types:** LLM, Router, Vector Search, Genie, UC Function, Human Input

## Prerequisites

- Databricks workspace with Unity Catalog enabled
- [Apps user token passthrough](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) enabled
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v0.230+

## One-Time Admin Setup

A workspace admin must run `deploy.sh` once to create the app, deploy job, permissions, and API scopes. After this, users can build and deploy agents from the UI with no additional setup.

### 1. Authenticate the Databricks CLI

```bash
databricks auth login --host https://your-workspace.cloud.databricks.com --profile MY_PROFILE
```

### 2. Run the setup script

```bash
./deploy.sh --profile MY_PROFILE
```

This single command:

1. **Uploads the deploy notebook** to `/Shared/agent-sweet/deploy_notebook`
2. **Deploys the DAB** — creates the deploy Job and App with resources, scopes, and env vars (see `databricks.yml`)
3. **Links the App** to the [Agent Sweet Git repo](https://github.com/btbeal-db/agent-sweet.git)
4. **Deploys the App** from the `main` branch

The script is idempotent — `databricks bundle deploy` handles create-or-update for all resources.

### 3. Grant the deploy Job permissions

The deploy Job's service principal needs permissions on any catalog/schema that users will deploy models to. Users specify the target catalog and schema at deploy time in the UI.

```sql
-- Grant on each catalog/schema you want users to deploy to
GRANT USE_CATALOG ON CATALOG my_catalog TO `job-sp-name`;
GRANT USE_SCHEMA ON SCHEMA my_catalog.my_schema TO `job-sp-name`;
GRANT CREATE_MODEL ON SCHEMA my_catalog.my_schema TO `job-sp-name`;
```

The Job SP also needs permissions to create serving endpoints. The script does **not** create schemas — users must select an existing one.

### 4. Verify

Open the App URL printed at the end of the script. You should see the agent builder canvas. Try building a simple LLM node and running it in the playground to confirm OBO auth is working.

### Updating the App

After pushing code changes to Git, redeploy with:

```bash
./deploy.sh --profile MY_PROFILE --deploy-only
```

Or deploy from the Databricks Apps UI: **App details → Deploy → From Git → Branch: `main`**.

### Script Options

| Flag | Description |
|---|---|
| `--profile <name>` | Databricks CLI profile to use (required) |
| `--branch <name>` | Git branch to deploy (default: `main`) |
| `--target <name>` | DAB target (default: `dev`) |
| `--deploy-only` | Skip setup, just deploy from Git |

## How Agent Deployment Works

When a user clicks **Deploy** in the UI, the app doesn't deploy directly. Instead, it triggers a background Databricks Job so that users don't need MLflow, catalog, or serving permissions.

The flow:

1. **Validate** — the app compiles the graph locally to catch errors early
2. **Submit Job** — the user specifies a catalog and schema, then the app triggers the deploy Job, which:
   - Installs the app package from Git (same branch the app is running)
   - Logs the agent as an MLflow model with resource declarations
   - Registers the model in the user-specified Unity Catalog schema
   - Creates a Model Serving endpoint with AI Gateway and inference tables
3. **Poll** — the app polls the Job for completion and shows the result

## Authentication & Security Model

There are three distinct auth contexts in this app. Understanding them is important for workspace admins.

### 1. App (playground/preview)

When users build and test agents in the browser, the app runs data-access calls (Vector Search, Genie, UC Functions) using the **user's identity** via the OBO token (`x-forwarded-access-token`). Users can only query resources they personally have access to. LLM calls use the app's service principal (Foundation Model API doesn't accept OBO tokens).

### 2. Deploy Job

The deploy Job runs as the **Job owner's service principal**, not the user. It needs:
- `USE CATALOG` + `USE SCHEMA` + `CREATE MODEL` on each catalog/schema users will deploy to
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

## Local Development (optional)

If you want to run locally or contribute changes:

### Prerequisites

- Node.js 18+ and npm
- Python 3.11 and [uv](https://docs.astral.sh/uv/)

### Setup

```bash
git clone https://github.com/btbeal-db/agent-sweet.git && cd agent-sweet
databricks auth login --host https://your-workspace.cloud.databricks.com
uv sync
cd frontend && npm install && cd ..
```

### Run locally

```bash
# Terminal 1: backend
DATABRICKS_CONFIG_PROFILE=MY_PROFILE uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2: frontend with hot reload
cd frontend && npm run dev
```

The frontend dev server proxies `/api` requests to the backend on port 8000.

## Architecture

```
frontend/              React/Vite UI (builds to backend/static/)
backend/               FastAPI app + LangGraph agent engine
  nodes/               Pluggable node types (auto-discovered)
  deploy_notebook.py   Notebook run by the deploy Job
  deploy_helpers.py    Resource extraction + code path helpers
  mlflow_model.py      MLflow pyfunc wrapper for serving deployed agents
demo/                  Optional: sample data setup script
app.yaml               Databricks Apps runtime config
databricks.yml         DAB definition (Job, App resources, scopes, env vars)
deploy.sh              One-time admin setup + deploy script
```

When deployed as a **Databricks App**, the platform automatically injects workspace credentials and the user's identity token (OBO). The FastAPI backend serves both the API and the built frontend static files.

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
| Deploy modal says "App not configured" | Run `deploy.sh` — the DAB injects `DEPLOY_JOB_ID` as an app env var |
| Deploy Job fails with pip install error | Ensure the Git repo is accessible from the Job cluster (public repo, or configure Git credentials) |
| Endpoint container build fails | Check `requirements-serving.txt` targets Python 3.10 (`uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.10`) |
| Vector Search / Genie 403 errors in playground | Run `deploy.sh` again to set scopes, or add them manually via `databricks api patch` |
| App deploy fails with "user token passthrough not enabled" | Ask your workspace admin to enable Apps user token passthrough |
