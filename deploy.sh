#!/usr/bin/env bash
set -euo pipefail

# ── Agent Builder Deploy Script ──────────────────────────────────────────────
# Usage:
#   ./deploy.sh --profile MY_PROFILE --init   # first-time deploy
#   ./deploy.sh --profile MY_PROFILE           # redeploy (after init)
#   ./deploy.sh dev --profile MY_PROFILE       # specify target + profile
#   ./deploy.sh dev --clean                    # clear stale state
#
# Before first deploy:
#   1. Set DEPLOY_CATALOG and DEPLOY_SCHEMA in app.yaml
#   2. Run: ./deploy.sh --profile MY_PROFILE --init

# ── Prerequisites ────────────────────────────────────────────────────────────
command -v databricks >/dev/null 2>&1 || { echo "ERROR: 'databricks' CLI not found. See: https://docs.databricks.com/dev-tools/cli/install.html"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: 'node' not found. Install Node.js 18+."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: 'npm' not found."; exit 1; }

TARGET="${1:-dev}"
CLEAN=false
INIT=false
PROFILE=""

# Parse first positional arg as target only if it doesn't start with --
if [[ "${1:-}" == --* ]]; then
  TARGET="dev"
fi

args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in
    --clean) CLEAN=true ;;
    --init)  INIT=true ;;
    --profile=*) PROFILE="${args[$i]#--profile=}" ;;
    --profile)   [[ $((i+1)) -lt ${#args[@]} ]] && PROFILE="${args[$((i+1))]}" ;;
  esac
done

if [[ -n "$PROFILE" ]]; then
  export DATABRICKS_CONFIG_PROFILE="$PROFILE"
  echo "── Using Databricks CLI profile: $PROFILE"
elif [[ -z "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  echo "ERROR: No profile specified. Use --profile <name> or set DATABRICKS_CONFIG_PROFILE."
  exit 1
fi

APP_NAME="agent-builder-${TARGET}"
echo "── Target: $TARGET  App: $APP_NAME"

# ── 1. Build frontend ───────────────────────────────────────────────────────
echo "── Building frontend..."
(cd frontend && [[ -d node_modules ]] || npm install && npm run build)

# ── 1b. Ensure requirements-serving.txt exists ───────────────────────────────
if [[ ! -f requirements-serving.txt ]]; then
  echo "── Generating requirements-serving.txt..."
  uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11
fi

# ── 2. Optionally clear stale Terraform state ────────────────────────────────
STATE_FILE=".databricks/bundle/$TARGET/terraform/terraform.tfstate"
if [[ "$CLEAN" == true ]] && [[ -f "$STATE_FILE" ]]; then
  echo "── Clearing stale deployment state..."
  rm "$STATE_FILE"
fi

# ── 3. Upload deploy notebook ────────────────────────────────────────────────
NOTEBOOK_PATH="/Shared/agent-builder/deploy_notebook"
echo "── Uploading deploy notebook to ${NOTEBOOK_PATH}..."
databricks workspace mkdirs /Shared/agent-builder 2>/dev/null || true
databricks workspace import "${NOTEBOOK_PATH}" \
  --file backend/deploy_notebook.py \
  --language PYTHON \
  --format SOURCE \
  --overwrite 2>/dev/null || echo "  (notebook upload failed — will retry after bundle deploy)"

# ── 4. Deploy bundle (creates Job + App + wires resources) ───────────────────
echo "── Deploying bundle..."
databricks bundle deploy -t "$TARGET"

# ── 5. Set user API scopes (SDK doesn't propagate these from databricks.yml) ─
echo "── Setting user API scopes..."
databricks api patch /api/2.0/apps/"$APP_NAME" --json '{
  "user_api_scopes": [
    "catalog.catalogs:read",
    "catalog.schemas:read",
    "catalog.tables:read",
    "dashboards.genie",
    "serving.serving-endpoints",
    "serving.serving-endpoints-data-plane",
    "sql",
    "vectorsearch.vector-search-endpoints",
    "vectorsearch.vector-search-indexes"
  ]
}' > /dev/null 2>&1 || echo "  (could not set scopes — app may not exist yet)"

# ── 6. Grant app's SP access to catalog/schema (first-time only) ─────────────
if [[ "$INIT" == true ]]; then
  # Read catalog/schema from app.yaml
  CATALOG=$(python3 -c "
import yaml
with open('app.yaml') as f:
    d = yaml.safe_load(f)
for e in d.get('env', []):
    if e['name'] == 'DEPLOY_CATALOG':
        print(e['value'])
        break
" 2>/dev/null || echo "")
  SCHEMA=$(python3 -c "
import yaml
with open('app.yaml') as f:
    d = yaml.safe_load(f)
for e in d.get('env', []):
    if e['name'] == 'DEPLOY_SCHEMA':
        print(e['value'])
        break
" 2>/dev/null || echo "")

  if [[ -n "$CATALOG" ]] && [[ "$CATALOG" != "CHANGE_ME" ]] && [[ -n "$SCHEMA" ]] && [[ "$SCHEMA" != "CHANGE_ME" ]]; then
    # Use the SP client_id for grants (SP names with spaces don't work with the CLI)
    APP_SP_CLIENT_ID=$(databricks apps get "$APP_NAME" 2>/dev/null | python3 -c "
import sys, json
print(json.load(sys.stdin).get('service_principal_client_id', ''))
" 2>/dev/null || echo "")

    if [[ -n "$APP_SP_CLIENT_ID" ]]; then
      echo "── Granting app SP access to ${CATALOG}.${SCHEMA}..."
      databricks api patch "/api/2.0/unity-catalog/permissions/catalog/${CATALOG}" --json "{
        \"changes\": [{
          \"principal\": \"${APP_SP_CLIENT_ID}\",
          \"add\": [\"USE_CATALOG\"]
        }]
      }" > /dev/null 2>&1 || echo "  (could not grant USE_CATALOG)"

      databricks api patch "/api/2.0/unity-catalog/permissions/schema/${CATALOG}.${SCHEMA}" --json "{
        \"changes\": [{
          \"principal\": \"${APP_SP_CLIENT_ID}\",
          \"add\": [\"USE_SCHEMA\", \"CREATE_MODEL\"]
        }]
      }" > /dev/null 2>&1 || echo "  (could not grant USE_SCHEMA/CREATE_MODEL)"
    fi
  else
    echo "  WARNING: DEPLOY_CATALOG/DEPLOY_SCHEMA not set in app.yaml. Update them and redeploy."
  fi
fi

# ── 7. Trigger app deployment ────────────────────────────────────────────────
BUNDLE_PATH=$(databricks bundle summary -t "$TARGET" 2>&1 | grep "Path:" | awk '{print $2}')
SOURCE_PATH="${BUNDLE_PATH}/files"

if [[ "$INIT" == true ]]; then
  echo "── Initializing app (first-time setup)..."
  databricks bundle run agent_builder -t "$TARGET"
else
  echo "── Redeploying app..."
  databricks apps deploy "$APP_NAME" --source-code-path "$SOURCE_PATH"
fi

echo ""
echo "── Done!"
echo "── App URL: $(databricks apps get "$APP_NAME" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url','(check your workspace)'))" 2>/dev/null || echo '(check your workspace)')"
