"""One-time MLflow experiment setup for per-user SP access.

Each user creates a workspace directory for their MLflow experiments, then
grants the app's service principal "Can Manage" on it.  This module provides
the API endpoints that walk the user through that flow and persist the result
in a Delta table so setup only happens once.
"""

from __future__ import annotations

import logging
import os
import time

import mlflow
from databricks.sdk.service.iam import (
    AccessControlRequest,
    PermissionLevel,
)
from databricks.sdk.service.sql import (
    Disposition,
    Format,
    StatementState,
)
from fastapi import APIRouter, HTTPException

from .auth import get_sp_workspace_client, get_workspace_client
from .schema import (
    SetupGrantRequest,
    SetupGrantResponse,
    SetupInfoResponse,
    SetupStatusResponse,
    SetupValidateRequest,
    SetupValidateResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_setup_table() -> str:
    """Return the fully-qualified config table name from env."""
    table = os.environ.get("SETUP_TABLE", "")
    if not table:
        raise HTTPException(
            status_code=503,
            detail=(
                "SETUP_TABLE env var not configured. "
                "Set setup_catalog, setup_schema, and sql_warehouse_id in databricks.yml and redeploy."
            ),
        )
    return table


def _get_warehouse_id() -> str:
    """Return the SQL warehouse ID from env."""
    wh = os.environ.get("SQL_WAREHOUSE_ID", "")
    if not wh:
        raise HTTPException(
            status_code=503,
            detail=(
                "SQL_WAREHOUSE_ID env var not configured. "
                "Set sql_warehouse_id in databricks.yml and redeploy."
            ),
        )
    return wh


def ensure_setup_table() -> None:
    """Create the setup config table if it doesn't exist.

    Called once at app startup.  Silently skips if env vars are not set
    (e.g. local development).
    """
    table = os.environ.get("SETUP_TABLE", "")
    wh = os.environ.get("SQL_WAREHOUSE_ID", "")
    if not table or not wh:
        logger.info("SETUP_TABLE or SQL_WAREHOUSE_ID not set — skipping table creation")
        return

    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"  user_email STRING NOT NULL,"
            f"  experiment_path STRING NOT NULL,"
            f"  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()"
            f") USING DELTA"
            f" COMMENT 'Per-user MLflow experiment setup for Agent Builder'",
            warehouse_id=wh,
        )
        logger.info("Setup table ready: %s", table)
    except Exception as exc:
        logger.warning("Could not create setup table (will retry on first use): %s", exc)


def _execute_sql(statement: str, *, warehouse_id: str | None = None) -> list[list[str]]:
    """Execute a SQL statement via the SP and return result rows."""
    wh = warehouse_id or _get_warehouse_id()
    sp = get_sp_workspace_client()
    resp = sp.statement_execution.execute_statement(
        warehouse_id=wh,
        statement=statement,
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
    )
    # Poll until complete (most queries finish instantly)
    while resp.status and resp.status.state in (
        StatementState.PENDING,
        StatementState.RUNNING,
    ):
        time.sleep(0.5)
        resp = sp.statement_execution.get_statement(resp.statement_id)

    if resp.status and resp.status.state == StatementState.FAILED:
        err = resp.status.error
        msg = err.message if err else "SQL execution failed"
        raise HTTPException(status_code=500, detail=f"SQL error: {msg}")

    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []


def _get_user_email() -> str:
    """Get the current user's email via OBO."""
    try:
        w = get_workspace_client()
        me = w.current_user.me()
        return me.user_name or me.display_name or ""
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Could not determine user identity. Are you logged in?",
        )


def _get_sp_display_name() -> str:
    """Best-effort SP display name; falls back to client ID."""
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "unknown")
    try:
        sp = get_sp_workspace_client()
        me = sp.current_user.me()
        return me.display_name or me.user_name or client_id
    except Exception:
        return client_id


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=SetupStatusResponse)
def setup_status():
    """Check whether the current user has completed MLflow experiment setup."""
    email = _get_user_email()
    sp_name = _get_sp_display_name()

    try:
        table = _get_setup_table()
        rows = _execute_sql(
            f"SELECT experiment_path FROM {table} "
            f"WHERE user_email = '{email}' "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
    except HTTPException as e:
        if e.status_code == 503:
            return SetupStatusResponse(
                setup_complete=False,
                user_email=email,
                sp_display_name=sp_name,
            )
        raise

    if rows:
        return SetupStatusResponse(
            setup_complete=True,
            user_email=email,
            sp_display_name=sp_name,
            experiment_path=rows[0][0],
        )

    return SetupStatusResponse(
        setup_complete=False,
        user_email=email,
        sp_display_name=sp_name,
    )


@router.get("/info", response_model=SetupInfoResponse)
def setup_info():
    """Return user email and SP identity for the setup wizard."""
    email = _get_user_email()
    sp_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    sp_name = _get_sp_display_name()
    return SetupInfoResponse(user_email=email, sp_display_name=sp_name, sp_id=sp_id)


@router.post("/grant-access", response_model=SetupGrantResponse)
def grant_access(req: SetupGrantRequest):
    """Try to grant the SP 'Can Manage' on the user's experiment directory."""
    sp_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    sp_name = _get_sp_display_name()
    host = os.environ.get("DATABRICKS_HOST", "")

    try:
        w = get_workspace_client()

        # Resolve workspace object ID for the directory
        obj = w.workspace.get_status(req.experiment_path)
        if obj is None or obj.object_id is None:
            return SetupGrantResponse(
                success=False,
                manual_instructions=(
                    f"Could not find a workspace object at '{req.experiment_path}'. "
                    "Make sure you created the folder first."
                ),
            )

        # Grant the SP CAN_MANAGE via the Permissions API.
        # Workspace folders use the "directories" object type.
        w.permissions.update(
            "directories",
            str(obj.object_id),
            access_control_list=[
                AccessControlRequest(
                    service_principal_name=sp_id,
                    permission_level=PermissionLevel.CAN_MANAGE,
                ),
            ],
        )
        return SetupGrantResponse(success=True)

    except Exception as exc:
        logger.warning("Auto-grant failed: %s", exc)
        manual = (
            f"Automatic permission grant failed. Please do this manually:\n\n"
            f"1. Open your Databricks workspace\n"
            f"2. Navigate to Workspace > find '{req.experiment_path}'\n"
            f"3. Right-click the folder > Permissions\n"
            f"4. Search for '{sp_name}' (ID: {sp_id})\n"
            f"5. Set permission to 'Can Manage'\n"
            f"6. Click Save"
        )
        if host:
            manual += f"\n\nWorkspace URL: {host}"
        return SetupGrantResponse(success=False, manual_instructions=manual)


@router.post("/validate", response_model=SetupValidateResponse)
def validate_setup(req: SetupValidateRequest):
    """Validate that the SP can access the experiment path, then persist the record."""
    email = _get_user_email()

    # Validate SP access by creating a test experiment inside the user's folder.
    # Following the Genesis Workbench pattern: call workspace.mkdirs() first to
    # ensure the directory exists from the SP's perspective, then set_experiment().
    # The SP can do this because it has "Can Manage" on the folder — it doesn't
    # need access to the parent directory (e.g. /Users/user@company.com).
    test_path = f"{req.experiment_path.rstrip('/')}/__setup_test__"
    try:
        sp = get_sp_workspace_client()

        # Ensure the directory is visible to the SP via Workspace API
        sp.workspace.mkdirs(req.experiment_path)

        # Now create a test experiment inside it
        mlflow.set_tracking_uri("databricks")
        os.environ["MLFLOW_TRACKING_URI"] = "databricks"

        prev_token = os.environ.pop("MLFLOW_TRACKING_TOKEN", None)
        try:
            experiment = mlflow.set_experiment(test_path)
            experiment_id = experiment.experiment_id

            # Clean up the test experiment
            try:
                mlflow.delete_experiment(experiment_id)
            except Exception:
                pass  # non-critical
        finally:
            if prev_token is not None:
                os.environ["MLFLOW_TRACKING_TOKEN"] = prev_token

    except Exception as exc:
        logger.warning("SP cannot access directory at %s: %s", req.experiment_path, exc)
        return SetupValidateResponse(
            success=False,
            error=(
                f"The service principal cannot access '{req.experiment_path}'. "
                f"Make sure you granted 'Can Manage' permission on the folder. Error: {exc}"
            ),
        )

    # Persist the setup record in the Delta table
    try:
        table = _get_setup_table()
        escaped_email = email.replace("'", "''")
        escaped_path = req.experiment_path.replace("'", "''")
        _execute_sql(
            f"MERGE INTO {table} t "
            f"USING (SELECT '{escaped_email}' AS user_email, '{escaped_path}' AS experiment_path) s "
            f"ON t.user_email = s.user_email "
            f"WHEN MATCHED THEN UPDATE SET "
            f"  experiment_path = s.experiment_path, updated_at = CURRENT_TIMESTAMP() "
            f"WHEN NOT MATCHED THEN INSERT (user_email, experiment_path) "
            f"  VALUES (s.user_email, s.experiment_path)"
        )
    except Exception as exc:
        logger.warning("Failed to persist setup record: %s", exc)
        # Still return success — the SP can access the experiment even if
        # we couldn't save the record (user will just need to set up again)

    return SetupValidateResponse(success=True, experiment_id=experiment_id)
