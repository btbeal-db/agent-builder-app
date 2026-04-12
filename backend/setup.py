"""One-time MLflow experiment setup for per-user SP access.

Each user creates a workspace directory for their MLflow experiments, then
grants the app's service principal "Can Manage" on it.  This module provides
the API endpoints that walk the user through that flow and persist the result
in a workspace file (in the user's own directory) so setup only happens once.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time

import mlflow
from databricks.sdk.service.workspace import ExportFormat, ImportFormat
from fastapi import APIRouter, HTTPException

from .auth import get_sp_workspace_client, get_workspace_client
from .schema import (
    SetupInfoResponse,
    SetupStatusResponse,
    SetupValidateRequest,
    SetupValidateResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Config filename stored inside the user's experiment directory (written by SP).
_SETUP_FILENAME = ".agent-builder-setup.json"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _config_path(experiment_path: str) -> str:
    """Return the workspace path for the setup config inside the experiment dir."""
    return f"{experiment_path.rstrip('/')}/{_SETUP_FILENAME}"


def _read_user_config(email: str) -> dict | None:
    """Try to find a setup config in the user's default experiment directory.

    Uses the SP client (which has Can Manage on the experiment folder).
    Tries the default convention path: /Users/{email}/agent-sweet.
    """
    default_experiment = f"/Users/{email}/agent-sweet"
    path = _config_path(default_experiment)
    try:
        sp = get_sp_workspace_client()
        resp = sp.workspace.export(path=path, format=ExportFormat.AUTO)
        if resp.content:
            return json.loads(base64.b64decode(resp.content))
    except Exception:
        return None
    return None


def _write_user_config(experiment_path: str, email: str) -> None:
    """Write setup config inside the experiment directory using the SP client."""
    path = _config_path(experiment_path)
    data = {
        "user_email": email,
        "experiment_path": experiment_path,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    sp = get_sp_workspace_client()
    sp.workspace.import_(
        path=path,
        content=base64.b64encode(json.dumps(data, indent=2).encode()).decode(),
        format=ImportFormat.AUTO,
        overwrite=True,
    )


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

    config = _read_user_config(email)
    if config and config.get("experiment_path"):
        return SetupStatusResponse(
            setup_complete=True,
            user_email=email,
            sp_display_name=sp_name,
            experiment_path=config["experiment_path"],
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

    # Persist the setup record as a workspace file in the user's directory
    try:
        _write_user_config(req.experiment_path, email)
    except Exception as exc:
        logger.warning("Failed to persist setup record: %s", exc)
        return SetupValidateResponse(
            success=False,
            error=(
                f"Setup validated but could not save your config. "
                f"The OBO token may lack the 'workspace' scope. Error: {exc}"
            ),
        )

    return SetupValidateResponse(success=True, experiment_id=experiment_id)
