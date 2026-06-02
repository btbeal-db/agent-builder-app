"""One-time MLflow experiment setup for per-user SP access.

Every user gets the same convention folder ``/Users/{email}/agent-sweet``;
this module creates it, grants the app's service principal Can Manage on
it, and persists a config file inside so setup is detected on subsequent
sign-ins. The path is fixed by design — letting users pick their own
breaks discovery on return visits and silently orphans their previous
folder if they re-prompt with a different value.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time

import mlflow
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel
from databricks.sdk.service.workspace import ExportFormat, ImportFormat
from fastapi import APIRouter, HTTPException

from .auth import get_sp_workspace_client, get_workspace_client
from .schema import (
    SetupInfoResponse,
    SetupStatusResponse,
    SetupValidateResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Config filename stored inside the user's experiment directory (written by SP).
_SETUP_FILENAME = ".agent-sweet-setup.json"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _experiment_path_for(email: str) -> str:
    """The single, conventional experiment folder for a user."""
    return f"/Users/{email}/agent-sweet"


def _config_path(experiment_path: str) -> str:
    """Return the workspace path for the setup config inside the experiment dir."""
    return f"{experiment_path.rstrip('/')}/{_SETUP_FILENAME}"


def _read_user_config(email: str) -> dict | None:
    """Read setup config from the user's experiment folder via the SP client."""
    path = _config_path(_experiment_path_for(email))
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
    experiment_path = _experiment_path_for(email)

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
        experiment_path=experiment_path,
    )


@router.get("/info", response_model=SetupInfoResponse)
def setup_info():
    """Return user email and SP identity for the setup wizard."""
    email = _get_user_email()
    sp_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    sp_name = _get_sp_display_name()
    return SetupInfoResponse(user_email=email, sp_display_name=sp_name, sp_id=sp_id)


@router.post("/auto-setup", response_model=SetupValidateResponse)
def auto_setup():
    """Create the experiment folder + grant SP access via OBO, then validate.

    The path is always ``/Users/{email}/agent-sweet`` — fixed by design so
    the discovery on subsequent sign-ins is unambiguous. The OBO token has
    the ``workspace.workspace`` scope (declared in ``databricks.yml``) and
    the user owns folders under ``/Users/{their_email}/...``, so both
    ``mkdirs`` and the permissions grant run without admin help.
    """
    sp_app_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    if not sp_app_id:
        return SetupValidateResponse(
            success=False,
            error="App service principal client ID is not configured (DATABRICKS_CLIENT_ID is unset).",
        )

    email = _get_user_email()
    experiment_path = _experiment_path_for(email)

    try:
        user_w = get_workspace_client()
    except Exception as exc:
        return SetupValidateResponse(
            success=False,
            error=f"Could not establish workspace access: {exc}",
        )

    # 1. Create the folder. ``mkdirs`` is idempotent.
    try:
        user_w.workspace.mkdirs(experiment_path)
    except Exception as exc:
        logger.warning("Auto-setup: mkdirs(%s) failed: %s", experiment_path, exc)
        return SetupValidateResponse(
            success=False,
            error=f"Could not create folder '{experiment_path}'. Error: {exc}",
        )

    # 2. Look up the folder's object_id so we can target its ACL.
    try:
        status = user_w.workspace.get_status(experiment_path)
        directory_id = str(status.object_id) if status.object_id is not None else ""
    except Exception as exc:
        return SetupValidateResponse(
            success=False,
            error=f"Could not look up folder '{experiment_path}' after creating it: {exc}",
        )
    if not directory_id:
        return SetupValidateResponse(
            success=False,
            error=f"Folder '{experiment_path}' has no object_id — cannot set permissions.",
        )

    # 3. Grant the app SP ``CAN_MANAGE``. ``permissions.update`` is additive
    # (PATCH semantics) so existing grants for the user are preserved.
    try:
        user_w.permissions.update(
            request_object_type="directories",
            request_object_id=directory_id,
            access_control_list=[
                AccessControlRequest(
                    service_principal_name=sp_app_id,
                    permission_level=PermissionLevel.CAN_MANAGE,
                ),
            ],
        )
    except Exception as exc:
        logger.warning("Auto-setup: permissions.update failed for %s: %s", directory_id, exc)
        return SetupValidateResponse(
            success=False,
            error=(
                f"Could not grant the app service principal access to "
                f"'{experiment_path}'. Error: {exc}"
            ),
        )

    return validate_setup()


@router.post("/validate", response_model=SetupValidateResponse)
def validate_setup():
    """Validate that the SP can access the experiment path, then persist the record."""
    email = _get_user_email()
    experiment_path = _experiment_path_for(email)

    # Validate SP access by creating a test experiment inside the user's folder.
    # Following the Genesis Workbench pattern: call workspace.mkdirs() first to
    # ensure the directory exists from the SP's perspective, then set_experiment().
    # The SP can do this because it has "Can Manage" on the folder — it doesn't
    # need access to the parent directory (e.g. /Users/user@company.com).
    test_path = f"{experiment_path}/__setup_test__"
    try:
        sp = get_sp_workspace_client()

        # Ensure the directory is visible to the SP via Workspace API
        sp.workspace.mkdirs(experiment_path)

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
        logger.warning("SP cannot access directory at %s: %s", experiment_path, exc)
        return SetupValidateResponse(
            success=False,
            error=(
                f"The service principal cannot access '{experiment_path}'. "
                f"Make sure you granted 'Can Manage' permission on the folder. Error: {exc}"
            ),
        )

    # Persist the setup record as a workspace file in the user's directory
    try:
        _write_user_config(experiment_path, email)
    except Exception as exc:
        logger.warning("Failed to persist setup record: %s", exc)
        return SetupValidateResponse(
            success=False,
            error=(
                f"Setup validated but could not save your config to "
                f"'{experiment_path}'. Error: {exc}"
            ),
        )

    return SetupValidateResponse(success=True, experiment_id=experiment_id)
