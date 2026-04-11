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
)

logger = logging.getLogger(__name__)

# Sensible defaults for agent checkpointing workloads.
_DEFAULT_PROJECT_DISPLAY_PREFIX = "Agent Sweet"
_DEFAULT_BRANCH = "production"
_DEFAULT_ENDPOINT = "primary"


def _model_name_to_database_id(model_name: str) -> str:
    """Derive a Postgres database name from a UC model name.

    ``catalog.schema.my_agent`` → ``my-agent-checkpoints``

    Database IDs must be 4-63 chars, lowercase, DNS-safe (RFC-1123).
    """
    # Take the last part of catalog.schema.model_name
    short = model_name.split(".")[-1]
    # Normalize: lowercase, underscores/spaces to hyphens, strip non-DNS chars
    db_id = re.sub(r"[^a-z0-9-]", "-", short.lower()).strip("-")
    db_id = re.sub(r"-+", "-", db_id)  # collapse consecutive hyphens
    db_id = f"{db_id}-checkpoints"
    # Enforce 4-63 char limit
    return db_id[:63]


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
) -> LakebaseConfig:
    """Create a Lakebase project + database and return connection details.

    The project is shared (one compute endpoint, scale-to-zero).  Each agent
    gets its own database derived from ``model_name``, e.g.
    ``catalog.schema.my_agent`` → database ``my-agent-checkpoints``.

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

    # ── 3. Resolve the owner role for the deploying user ────────────
    user_email = w.current_user.me().user_name
    owner_role = f"{branch_path}/roles/{user_email}"

    # ── 4. Create the checkpoints database or reuse existing ──────
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
