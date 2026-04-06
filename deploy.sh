#!/usr/bin/env bash
set -euo pipefail

# ── Agent Sweet — Admin Setup Script ────────────────────────────────────────
#
# Run this ONCE per workspace to provision the app infrastructure.
# After setup, deploy code changes from Git in the Databricks Apps UI,
# or re-run with --deploy-only to push a new git ref.
#
# Usage:
#   ./deploy.sh --profile MY_PROFILE
#   ./deploy.sh --profile MY_PROFILE --deploy-only
#
# Prerequisites:
#   1. Databricks CLI v0.230+ authenticated to your workspace
#
# What this script does:
#   1. Deploys the DAB (creates the Job + App)
#   2. Configures the App (resources, scopes, git repo) via apps update
#   3. Starts the App
#   4. Deploys the App from the specified Git branch

# ── Defaults ────────────────────────────────────────────────────────────────
GIT_BRANCH="main"
GIT_REPO="https://github.com/btbeal-db/agent-sweet.git"
TARGET="dev"
PROFILE=""
DEPLOY_ONLY=false

# ── Parse arguments ─────────────────────────────────────────────────────────
args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in
    --profile=*) PROFILE="${args[$i]#--profile=}" ;;
    --profile)   [[ $((i+1)) -lt ${#args[@]} ]] && PROFILE="${args[$((i+1))]}" ;;
    --branch=*)  GIT_BRANCH="${args[$i]#--branch=}" ;;
    --branch)    [[ $((i+1)) -lt ${#args[@]} ]] && GIT_BRANCH="${args[$((i+1))]}" ;;
    --target=*)  TARGET="${args[$i]#--target=}" ;;
    --target)    [[ $((i+1)) -lt ${#args[@]} ]] && TARGET="${args[$((i+1))]}" ;;
    --deploy-only) DEPLOY_ONLY=true ;;
  esac
done

# ── Validate ────────────────────────────────────────────────────────────────
command -v databricks >/dev/null 2>&1 || { echo "ERROR: 'databricks' CLI not found."; exit 1; }

if [[ -n "$PROFILE" ]]; then
  export DATABRICKS_CONFIG_PROFILE="$PROFILE"
  echo "── Using Databricks CLI profile: $PROFILE"
elif [[ -z "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  echo "ERROR: No profile specified. Use --profile <name> or set DATABRICKS_CONFIG_PROFILE."
  exit 1
fi

APP_NAME="agent-sweet-${TARGET}"
echo "── Target: $TARGET  App: $APP_NAME"
echo "── Git branch: $GIT_BRANCH"

# ── Deploy-only mode: just push a new git deployment ────────────────────────
if [[ "$DEPLOY_ONLY" == true ]]; then
  echo "── Deploying app from git branch: $GIT_BRANCH"
  databricks apps deploy "$APP_NAME" --json "{\"git_source\": {\"branch\": \"$GIT_BRANCH\"}}"
  echo "── Done!"
  exit 0
fi

# ── 1. Deploy bundle (creates Job + App) ──────────────────────────────────
echo "── Deploying bundle..."
databricks bundle deploy -t "$TARGET"

# ── 2. Configure app (resources, scopes, git repo) ───────────────────────
# DABs terraform provider doesn't reliably write resources/scopes, and
# doesn't support git_repository yet. We set everything in one apps update
# call since each update replaces the full config.
echo "── Resolving deploy job ID..."
JOB_ID=$(databricks bundle summary -t "$TARGET" -o json | python3 -c "
import sys, json
print(json.load(sys.stdin)['resources']['jobs']['deploy_job']['id'])
")
echo "  Deploy Job ID: $JOB_ID"

echo "── Configuring app (resources, scopes, git repo)..."
databricks apps update "$APP_NAME" --json "{
  \"resources\": [{
    \"name\": \"deploy-job\",
    \"job\": {
      \"id\": \"$JOB_ID\",
      \"permission\": \"CAN_MANAGE_RUN\"
    }
  }],
  \"user_api_scopes\": [
    \"catalog.catalogs:read\",
    \"catalog.schemas:read\",
    \"catalog.tables:read\",
    \"dashboards.genie\",
    \"serving.serving-endpoints\",
    \"sql\",
    \"vectorsearch.vector-search-endpoints\",
    \"vectorsearch.vector-search-indexes\"
  ],
  \"git_repository\": {
    \"url\": \"$GIT_REPO\",
    \"provider\": \"gitHub\"
  }
}" > /dev/null

# ── 3. Start the App ─────────────────────────────────────────────────────
echo "── Starting app (this may take a few minutes)..."
databricks apps start "$APP_NAME" 2>/dev/null || true

for i in {1..30}; do
  STATE=$(databricks apps get "$APP_NAME" -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('compute_status',{}).get('state',''))" 2>/dev/null || echo "")
  if [[ "$STATE" == "ACTIVE" ]]; then
    echo "  App is running."
    break
  fi
  echo "  Waiting... ($STATE)"
  sleep 10
done

# ── 4. Deploy App from Git ───────────────────────────────────────────────
echo "── Deploying app from git branch: $GIT_BRANCH"
databricks apps deploy "$APP_NAME" --json "{\"git_source\": {\"branch\": \"$GIT_BRANCH\"}}"

# ── Done ────────────────────────────────────────────────────────────────────
APP_URL=$(databricks apps get "$APP_NAME" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url','(check workspace)'))" 2>/dev/null || echo "(check workspace)")

echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  App URL: $APP_URL"
echo "  Deploy Job ID: $JOB_ID"
echo ""
echo "  Next steps:"
echo "    1. Open the app and verify it works"
echo "    2. Grant the deploy Job's SP access to catalogs/schemas"
echo "       where users will register models (USE_CATALOG,"
echo "       USE_SCHEMA, CREATE_MODEL)"
echo "    3. For code changes: push to Git, then either:"
echo "       - Deploy from the Databricks Apps UI"
echo "       - Run: ./deploy.sh --profile $PROFILE --deploy-only"
echo "══════════════════════════════════════════════════════════════════"
