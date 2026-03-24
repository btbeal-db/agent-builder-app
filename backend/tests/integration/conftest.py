"""Integration test fixtures — require a live Databricks workspace.

Configure via environment variables or use defaults matching demo/setup_demo.py output.

    TEST_PROFILE=BUILDER              # Databricks CLI profile
    TEST_CATALOG=agentbuilder_...     # UC catalog
    TEST_SCHEMA=agent_builder         # UC schema
    TEST_LLM_ENDPOINT=databricks-claude-sonnet-4-6
    TEST_VS_INDEX=catalog.schema.patient_notes_index
    TEST_VS_ENDPOINT=agent-builder-vs
    TEST_GENIE_ROOM_ID=01f127...
"""

from __future__ import annotations

import os

import pytest
from databricks.sdk import WorkspaceClient


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# ── Skip logic ────────────────────────────────────────────────────────────────

_PROFILE = _env("TEST_PROFILE", "DEFAULT")

def _can_connect() -> bool:
    try:
        w = WorkspaceClient(profile=_PROFILE)
        w.current_user.me()
        return True
    except Exception:
        return False

_CONNECTED = _can_connect()

pytestmark = pytest.mark.skipif(
    not _CONNECTED,
    reason=f"Cannot connect to Databricks with profile '{_PROFILE}'",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def workspace_client() -> WorkspaceClient:
    return WorkspaceClient(profile=_PROFILE)


@pytest.fixture(scope="session")
def warehouse_id(workspace_client: WorkspaceClient) -> str:
    warehouses = list(workspace_client.warehouses.list())
    if not warehouses:
        pytest.skip("No SQL warehouses available")
    return warehouses[0].id


@pytest.fixture(scope="session")
def catalog() -> str:
    return _env("TEST_CATALOG", "agentbuilder_serverless_stable_catalog")


@pytest.fixture(scope="session")
def schema() -> str:
    return _env("TEST_SCHEMA", "agent_builder")


@pytest.fixture(scope="session")
def llm_endpoint() -> str:
    return _env("TEST_LLM_ENDPOINT", "databricks-claude-sonnet-4-6")


@pytest.fixture(scope="session")
def vs_index_name(catalog, schema) -> str:
    return _env("TEST_VS_INDEX", f"{catalog}.{schema}.patient_notes_index")


@pytest.fixture(scope="session")
def vs_endpoint_name() -> str:
    return _env("TEST_VS_ENDPOINT", "agent-builder-vs")


@pytest.fixture(scope="session")
def genie_room_id() -> str:
    room = _env("TEST_GENIE_ROOM_ID", "")
    if not room:
        pytest.skip("TEST_GENIE_ROOM_ID not set")
    return room
