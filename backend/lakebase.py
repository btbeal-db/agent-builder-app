"""Lakebase provisioning helpers for conversational agent checkpointing.

Provides a function to create or resolve a Lakebase Autoscaling project +
database and return the connection details needed by the serving endpoint.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists, ResourceConflict
from databricks.sdk.service.postgres import (
    Database,
    DatabaseDatabaseSpec,
    EndpointStatusState,
    Project,
    ProjectSpec,
    Role,
    RoleIdentityType,
    RoleRoleSpec,
)

logger = logging.getLogger(__name__)

# Sensible defaults for agent checkpointing workloads.
_DEFAULT_PROJECT_DISPLAY_PREFIX = "Agent Sweet"
_DEFAULT_BRANCH = "production"
_DEFAULT_ENDPOINT = "primary"


def _model_name_to_database_id(model_name: str) -> str:
    """Derive a Postgres database name from a UC model name.

    ``catalog.schema.my_agent`` → ``catalog-schema-my-agent-ckpt``

    Uses the full UC path so that different agents in the same project
    never collide, even if they share the same model name suffix.

    Database IDs must be 4-63 chars, lowercase, DNS-safe (RFC-1123).
    """
    # Normalize: lowercase, dots/underscores/spaces to hyphens, strip non-DNS chars
    db_id = re.sub(r"[^a-z0-9-]", "-", model_name.lower()).strip("-")
    db_id = re.sub(r"-+", "-", db_id)  # collapse consecutive hyphens
    db_id = f"{db_id}-ckpt"
    # Enforce 4-63 char limit (trim from the left to keep the unique suffix)
    if len(db_id) > 63:
        db_id = db_id[-63:].lstrip("-")
    return db_id


@dataclass
class LakebaseConfig:
    """Everything a serving endpoint needs to connect to Lakebase."""

    endpoint: str  # projects/{id}/branches/{branch}/endpoints/{ep}
    host: str  # Postgres hostname
    database: str  # Postgres database name


# ── Shared helpers ───────────────────────────────────────────────────────────


def _wait_for_endpoint(w: WorkspaceClient, endpoint_path: str) -> str:
    """Poll until the endpoint is ACTIVE and return its host."""
    ep = w.postgres.get_endpoint(name=endpoint_path)
    if ep.status and ep.status.current_state == EndpointStatusState.ACTIVE:
        return ep.status.hosts.host

    for _ in range(60):
        time.sleep(5)
        ep = w.postgres.get_endpoint(name=endpoint_path)
        if ep.status and ep.status.current_state == EndpointStatusState.ACTIVE:
            return ep.status.hosts.host

    raise TimeoutError(
        f"Endpoint {endpoint_path} did not become ACTIVE within 5 minutes"
    )



def _ensure_sp_role(
    w: WorkspaceClient, branch_path: str, sp_client_id: str,
) -> None:
    """Create a SERVICE_PRINCIPAL role on the branch if it doesn't exist."""
    for role in w.postgres.list_roles(parent=branch_path):
        if role.status and role.status.postgres_role == sp_client_id:
            logger.info("SP role already exists for %s", sp_client_id)
            return

    # role_id must match ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ — prefix the
    # UUID with "sp-" so it always starts with a letter.
    role_id = f"sp-{sp_client_id}"
    logger.info("Creating Lakebase role for SP %s (role_id=%s)", sp_client_id, role_id)
    w.postgres.create_role(
        parent=branch_path,
        role=Role(
            spec=RoleRoleSpec(
                identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
                postgres_role=sp_client_id,
            ),
        ),
        role_id=role_id,
    ).wait()
    logger.info("SP role created")


def _ensure_database(
    w: WorkspaceClient, branch_path: str, database_id: str,
    sp_client_id: str,
) -> None:
    """Create the per-agent database if it doesn't exist.

    The app's SP is set as owner so it can create checkpoint tables at
    serving time.  If the database already exists with a different owner,
    ownership is transferred to the SP.
    """
    from google.protobuf.field_mask_pb2 import FieldMask

    sp_role = f"{branch_path}/roles/sp-{sp_client_id}"
    db_path = f"{branch_path}/databases/{database_id}"

    for db in w.postgres.list_databases(parent=branch_path):
        if db.name == db_path:
            # Ensure the SP owns it
            current_owner = db.status.role if db.status else None
            if current_owner != sp_role:
                logger.info("Transferring database %s ownership to SP", database_id)
                w.postgres.update_database(
                    name=db_path,
                    database=Database(
                        name=db_path,
                        spec=DatabaseDatabaseSpec(role=sp_role),
                    ),
                    update_mask=FieldMask(paths=["spec.role"]),
                ).wait()
            else:
                logger.info("Database %s already exists with correct owner", database_id)
            return

    logger.info("Creating database %s in %s (owner: SP)", database_id, branch_path)
    w.postgres.create_database(
        parent=branch_path,
        database=Database(
            spec=DatabaseDatabaseSpec(
                postgres_database=database_id,
                role=sp_role,
            ),
        ),
        database_id=database_id,
    ).wait()
    logger.info("Database %s ready", database_id)


# ── Public API ───────────────────────────────────────────────────────────────


def provision_lakebase(
    w: WorkspaceClient,
    project_id: str,
    model_name: str,
    sp_client_id: str,
) -> LakebaseConfig:
    """Create a Lakebase project + per-agent database and return connection
    details.

    Idempotent — reuses existing project, SP role, and database if they
    already exist.  Multiple agents can share the same project; each gets
    its own database derived from the full UC model name.
    """
    database_id = _model_name_to_database_id(model_name)
    branch_path = f"projects/{project_id}/branches/{_DEFAULT_BRANCH}"
    endpoint_path = f"{branch_path}/endpoints/{_DEFAULT_ENDPOINT}"

    # 1. Create project (or reuse)
    project_exists = any(
        p.name == f"projects/{project_id}"
        for p in w.postgres.list_projects()
    )
    if project_exists:
        logger.info("Project %s already exists, reusing", project_id)
    else:
        display_name = f"{_DEFAULT_PROJECT_DISPLAY_PREFIX} – {project_id}"
        logger.info("Creating Lakebase project %s", project_id)
        w.postgres.create_project(
            project=Project(spec=ProjectSpec(display_name=display_name)),
            project_id=project_id,
        ).wait()
        logger.info("Project %s ready", project_id)

    # 2–4. Shared: wait for endpoint, ensure SP role, ensure database
    host = _wait_for_endpoint(w, endpoint_path)
    _ensure_sp_role(w, branch_path, sp_client_id)
    _ensure_database(w, branch_path, database_id, sp_client_id)

    return LakebaseConfig(endpoint=endpoint_path, host=host, database=database_id)


def resolve_lakebase(
    w: WorkspaceClient,
    project_id: str,
    model_name: str,
    sp_client_id: str,
) -> LakebaseConfig:
    """Resolve connection details for an existing Lakebase project.

    Like :func:`provision_lakebase` but skips project creation.
    Still ensures the SP role and per-agent database exist.
    """
    database_id = _model_name_to_database_id(model_name)
    branch_path = f"projects/{project_id}/branches/{_DEFAULT_BRANCH}"
    endpoint_path = f"{branch_path}/endpoints/{_DEFAULT_ENDPOINT}"

    # Verify the project exists (raises if not)
    w.postgres.get_project(name=f"projects/{project_id}")

    # Shared: get host, ensure SP role, ensure database
    host = _wait_for_endpoint(w, endpoint_path)
    _ensure_sp_role(w, branch_path, sp_client_id)
    _ensure_database(w, branch_path, database_id, sp_client_id)

    return LakebaseConfig(endpoint=endpoint_path, host=host, database=database_id)
