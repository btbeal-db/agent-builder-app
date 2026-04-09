#!/usr/bin/env bash
set -euo pipefail

# ── Agent Builder Deploy Script ──────────────────────────────────────────────
# Usage:
#   ./deploy.sh --profile MY_PROFILE --init   # first-time deploy
#   ./deploy.sh --profile MY_PROFILE           # redeploy (after init)
#   ./deploy.sh dev --profile MY_PROFILE       # specify target + profile
#   ./deploy.sh dev --clean                    # clear stale state
#
# Bundle variables (catalog, schema, warehouse) are set in databricks.yml.
# Override at deploy time with:
#   databricks bundle deploy -t dev --var="setup_catalog=my_catalog" --var="sql_warehouse_id=abc123"
#
# Normal flow (after first init):
#   1. Builds frontend → backend/static/
#   2. bundle deploy   → syncs files, creates schema, sets env vars on the app
#   3. apps deploy     → tells the running app to pick up new code (no compute restart)
#   4. On first request the app auto-creates the setup config table if it doesn't exist

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

for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=true ;;
    --init)  INIT=true ;;
    --profile=*) PROFILE="${arg#--profile=}" ;;
  esac
done

# Handle --profile VALUE (space-separated) form
args=("$@")
for i in "${!args[@]}"; do
  if [[ "${args[$i]}" == "--profile" ]] && [[ $((i+1)) -lt ${#args[@]} ]]; then
    PROFILE="${args[$((i+1))]}"
  fi
done

# Set the profile for all databricks CLI commands in this script
if [[ -n "$PROFILE" ]]; then
  export DATABRICKS_CONFIG_PROFILE="$PROFILE"
  echo "── Using Databricks CLI profile: $PROFILE"
elif [[ -z "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  echo "ERROR: No profile specified. Use --profile <name> or set DATABRICKS_CONFIG_PROFILE."
  echo "  Example: ./deploy.sh --profile DEFAULT --init"
  echo "  Run 'databricks auth profiles' to see available profiles."
  exit 1
fi

APP_NAME="agent-builder-${TARGET}"
echo "── Target: $TARGET  App: $APP_NAME"

# 1. Build frontend
echo "── Building frontend..."
(cd frontend && [[ -d node_modules ]] || npm install && npm run build)

# 1b. Ensure requirements-serving.txt exists
if [[ ! -f requirements-serving.txt ]]; then
  echo "── Generating requirements-serving.txt..."
  uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11
fi

# 2. Optionally clear stale Terraform state (needed when switching workspaces)
STATE_FILE=".databricks/bundle/$TARGET/terraform/terraform.tfstate"
if [[ "$CLEAN" == true ]] && [[ -f "$STATE_FILE" ]]; then
  echo "── Clearing stale deployment state..."
  rm "$STATE_FILE"
fi

# 3. Sync files + update app config (scopes, env vars, schema creation)
echo "── Syncing bundle..."
databricks bundle deploy -t "$TARGET"

# 4. Trigger app redeployment
BUNDLE_PATH=$(databricks bundle summary -t "$TARGET" 2>&1 | grep "Path:" | awk '{print $2}')
SOURCE_PATH="${BUNDLE_PATH}/files"

if [[ "$INIT" == true ]]; then
  # First time only: creates the app resource and provisions compute
  echo "── Initializing app (first-time setup)..."
  databricks bundle run agent_builder -t "$TARGET"
else
  # Redeploy: tells the running app to pick up new code — no compute restart
  echo "── Redeploying app..."
  databricks apps deploy "$APP_NAME" --source-code-path "$SOURCE_PATH"
fi

echo "── Done!"
echo "── App URL: $(databricks apps get "$APP_NAME" 2>/dev/null | grep -oP 'https://[^\s"]+' | head -1 || echo '(check your workspace)')"
