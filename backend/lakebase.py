"""Lakebase provisioning helpers for conversational agent checkpointing.

Provides a function to create a Lakebase Autoscaling project + database and
return the connection details needed by the serving endpoint.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists
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


def provision_lakebase(
    w: WorkspaceClient,
    project_id: str,
    model_name: str,
    sp_client_id: str,
) -> LakebaseConfig:
    """Create a Lakebase project + database and return connection details.

    The project is shared (one compute endpoint, scale-to-zero).  Each agent
    gets its own database derived from ``model_name``, e.g.
    ``catalog.schema.my_agent`` → database ``my-agent-checkpoints``.

    A Lakebase role is also created for the app's service principal so that
    the serving endpoint can authenticate to Postgres at runtime.

    Parameters
    ----------
    w:
        Authenticated ``WorkspaceClient`` (typically PAT-based so the
        resources are owned by the deploying user).
    project_id:
        Short identifier for the project (e.g. ``"agent-sweet"``).  Must be
        3-63 chars, lowercase, starting with a letter.
    model_name:
        Unity Catalog model name (``catalog.schema.model``).  Used to derive
        a unique database name for this agent.
    sp_client_id:
        The app's service principal application/client ID.  A Lakebase role
        is created for this SP so the serving endpoint can connect.

    Returns
    -------
    LakebaseConfig
        The endpoint path, host, and database name needed as env vars on
        the serving endpoint.
    """
    database_id = _model_name_to_database_id(model_name)
    display_name = f"{_DEFAULT_PROJECT_DISPLAY_PREFIX} – {project_id}"

    # ── 1. Create project or reuse existing ─────────────────────────
    branch_path = f"projects/{project_id}/branches/{_DEFAULT_BRANCH}"
    endpoint_path = f"{branch_path}/endpoints/{_DEFAULT_ENDPOINT}"

    try:
        logger.info("Creating Lakebase project %s", project_id)
        project_op = w.postgres.create_project(
            project=Project(spec=ProjectSpec(display_name=display_name)),
            project_id=project_id,
        )
        project_op.wait()
        logger.info("Project %s ready", project_id)
    except ResourceAlreadyExists:
        logger.info("Project %s already exists, reusing", project_id)

    # ── 2. Resolve the primary endpoint and wait for it to be ACTIVE
    ep = w.postgres.get_endpoint(name=endpoint_path)
    if ep.status and ep.status.current_state != EndpointStatusState.ACTIVE:
        import time

        for _ in range(60):
            time.sleep(5)
            ep = w.postgres.get_endpoint(name=endpoint_path)
            if ep.status and ep.status.current_state == EndpointStatusState.ACTIVE:
                break
        else:
            raise TimeoutError(
                f"Endpoint {endpoint_path} did not become ACTIVE within 5 minutes"
            )

    host = ep.status.hosts.host
    logger.info("Endpoint %s active at %s", endpoint_path, host)

    # ── 3. Create a role for the app's SP so it can connect at serving time
    try:
        logger.info("Creating Lakebase role for SP %s", sp_client_id)
        role_op = w.postgres.create_role(
            parent=branch_path,
            role=Role(
                spec=RoleRoleSpec(
                    identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
                    postgres_role=sp_client_id,
                ),
            ),
            role_id=sp_client_id,
        )
        role_op.wait()
        logger.info("SP role created")
    except ResourceAlreadyExists:
        logger.info("SP role already exists, reusing")

    # ── 4. Resolve the owner role for the deploying user ──────────
    user_email = w.current_user.me().user_name
    owner_role = None
    for role in w.postgres.list_roles(parent=branch_path):
        if role.status and role.status.postgres_role == user_email:
            owner_role = role.name
            break
    if not owner_role:
        raise ValueError(
            f"No Lakebase role found for {user_email} on {branch_path}. "
            f"Ensure you have access to this project."
        )

    # ── 5. Create the checkpoints database or reuse existing ──────
    try:
        logger.info("Creating database %s in %s (owner: %s)", database_id, branch_path, user_email)
        db_op = w.postgres.create_database(
            parent=branch_path,
            database=Database(
                spec=DatabaseDatabaseSpec(
                    postgres_database=database_id,
                    role=owner_role,
                ),
            ),
            database_id=database_id,
        )
        db_op.wait()
        logger.info("Database %s ready", database_id)
    except ResourceAlreadyExists:
        logger.info("Database %s already exists, reusing", database_id)

    return LakebaseConfig(
        endpoint=endpoint_path,
        host=host,
        database=database_id,
    )


def resolve_lakebase(
    w: WorkspaceClient,
    project_id: str,
    model_name: str,
    sp_client_id: str,
) -> LakebaseConfig:
    """Resolve connection details for an existing Lakebase project.

    Like :func:`provision_lakebase` but skips project creation — only looks up
    the endpoint host, ensures the SP role exists, and creates the per-agent
    database if it doesn't exist yet.
    """
    database_id = _model_name_to_database_id(model_name)
    branch_path = f"projects/{project_id}/branches/{_DEFAULT_BRANCH}"
    endpoint_path = f"{branch_path}/endpoints/{_DEFAULT_ENDPOINT}"

    # Verify the project exists
    w.postgres.get_project(name=f"projects/{project_id}")

    # Get endpoint host
    ep = w.postgres.get_endpoint(name=endpoint_path)
    host = ep.status.hosts.host

    # Ensure the SP role exists
    try:
        w.postgres.create_role(
            parent=branch_path,
            role=Role(
                spec=RoleRoleSpec(
                    identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
                    postgres_role=sp_client_id,
                ),
            ),
            role_id=sp_client_id,
        ).wait()
    except ResourceAlreadyExists:
        pass

    # Ensure the per-agent database exists
    user_email = w.current_user.me().user_name
    owner_role = None
    for role in w.postgres.list_roles(parent=branch_path):
        if role.status and role.status.postgres_role == user_email:
            owner_role = role.name
            break

    if owner_role:
        try:
            w.postgres.create_database(
                parent=branch_path,
                database=Database(
                    spec=DatabaseDatabaseSpec(
                        postgres_database=database_id,
                        role=owner_role,
                    ),
                ),
                database_id=database_id,
            ).wait()
        except ResourceAlreadyExists:
            pass

    return LakebaseConfig(
        endpoint=endpoint_path,
        host=host,
        database=database_id,
    )
