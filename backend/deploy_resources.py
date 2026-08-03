"""Resource + scope resolution shared across deploy targets.

These helpers translate a ``GraphDef`` into the resource declarations and
API scopes required by each deploy target:

- **Model Serving** (``main.py:deploy_graph``): ``_extract_resources`` (for
  passthrough auth) and ``_build_auth_policy`` (for OBO auth) produce MLflow
  ``resources``/``auth_policy`` kwargs for ``mlflow.pyfunc.log_model``.
- **Agents on Apps** (``app_deploy.py``): ``graph_to_app_resources`` and
  ``graph_to_user_api_scopes`` produce ``databricks-sdk`` ``AppResource`` objects
  and ``user_api_scopes`` for ``w.apps.create``.

``_persist_mcp_tool_metadata`` (MCP tool pre-discovery) is shared by both.

Lives in its own module so both ``main.py`` and ``app_deploy.py`` can import it
without a circular dependency.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging

from mlflow.models.auth_policy import AuthPolicy, SystemAuthPolicy, UserAuthPolicy
from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
    DatabricksSQLWarehouse,
    DatabricksTable,
    DatabricksVectorSearchIndex,
)

from .auth import create_pat_client, get_sp_workspace_client
from .schema import GraphDef
from .tools import discover_mcp_tool_metadata, managed_mcp_url_for_tool

logger = logging.getLogger(__name__)


def _fallback_client(client=None):
    """Return the caller's client, else SP, else a default WorkspaceClient."""
    if client:
        return client
    try:
        return get_sp_workspace_client()
    except RuntimeError:
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient()


def _extract_resources(
    graph: GraphDef,
    client=None,
) -> list:
    """Extract Databricks resource declarations from all nodes in the graph.

    Maps node config fields to the appropriate MLflow resource types so that
    Model Serving provisions credentials for each external resource via
    automatic authentication passthrough.

    Handles both top-level node config fields (e.g. VS node's ``index_name``)
    and tool configs embedded in an LLM node's ``tools_json`` string.

    For Genie spaces, also discovers and declares downstream dependencies
    (tables and SQL warehouse) by querying the Genie API, as required by
    the automatic auth passthrough docs.

    Args:
        graph: The graph definition to extract resources from.
        client: Optional WorkspaceClient for resolving Genie/MCP downstream
            dependencies.  Pass a PAT-authenticated client during deploy so
            the resolution uses the user's credentials rather than the app SP
            (which may lack permission to read Genie room metadata).
    """
    resources = []
    seen: set[tuple[str, str]] = set()

    # Config field name → resource class mapping.
    # Note: "endpoint_name" is the Vector Search endpoint (infrastructure),
    # NOT a Model Serving endpoint — it does not need a resource declaration.
    # Only the VS index itself needs to be declared.
    resource_map = {
        "endpoint": DatabricksServingEndpoint,        # LLM serving endpoints
        "index_name": DatabricksVectorSearchIndex,    # VS indexes
        "room_id": DatabricksGenieSpace,              # Genie rooms
        "table_name": DatabricksTable,                # UC tables
        "function_name": DatabricksFunction,          # UC functions
    }

    init_param_map = {
        DatabricksServingEndpoint: "endpoint_name",
        DatabricksVectorSearchIndex: "index_name",
        DatabricksGenieSpace: "genie_space_id",
        DatabricksTable: "table_name",
        DatabricksFunction: "function_name",
    }

    # Collect Genie room IDs so we can resolve their dependencies after
    genie_room_ids: list[str] = []

    def _add_from_config(config: dict) -> None:
        for config_key, resource_cls in resource_map.items():
            value = config.get(config_key)
            if value and (config_key, value) not in seen:
                seen.add((config_key, value))
                resources.append(
                    resource_cls(**{init_param_map[resource_cls]: value})
                )
                if config_key == "room_id":
                    genie_room_ids.append(value)

    for node in graph.nodes:
        # Top-level node config (VS node, Genie node, UC Function node, etc.)
        _add_from_config(node.config)

        # Tools attached to LLM nodes via tools_json
        tools_json_raw = node.config.get("tools_json", "")
        if tools_json_raw and str(tools_json_raw).strip():
            try:
                tool_configs = json.loads(str(tools_json_raw))
                if isinstance(tool_configs, list):
                    for tc in tool_configs:
                        _add_from_config(tc.get("config", {}))
            except (json.JSONDecodeError, TypeError):
                pass

    # Resolve Genie downstream dependencies (tables + SQL warehouse).
    # The auth passthrough docs require these to be explicitly declared.
    for room_id in genie_room_ids:
        try:
            # Prefer the caller-provided client (user PAT during deploy) so
            # we can read Genie room metadata.  Fall back to SP → default.
            w = _fallback_client(client)
            space = w.genie.get_space(room_id, include_serialized_space=True)

            # SQL warehouse
            if space.warehouse_id and ("warehouse", space.warehouse_id) not in seen:
                seen.add(("warehouse", space.warehouse_id))
                resources.append(DatabricksSQLWarehouse(warehouse_id=space.warehouse_id))

            # Tables from the serialized space definition
            if space.serialized_space:
                space_def = json.loads(space.serialized_space)
                tables = space_def.get("data_sources", {}).get("tables", [])
                for table in tables:
                    table_id = table.get("identifier", "")
                    if table_id and ("table_name", table_id) not in seen:
                        seen.add(("table_name", table_id))
                        resources.append(DatabricksTable(table_name=table_id))
        except Exception as exc:
            logger.warning("Could not resolve Genie room %s dependencies: %s", room_id, exc)

    # Resolve MCP server resources.
    # DatabricksMCPClient.get_databricks_resources() parses the MCP URL to
    # determine the resource type (UC functions, VS indexes, Genie spaces,
    # UC connections) and returns the corresponding MLflow resource objects.
    # This runs in a thread because get_databricks_resources() calls
    # list_tools() which uses asyncio.run() — incompatible with the
    # FastAPI event loop on the calling thread.
    mcp_urls = _collect_mcp_urls(graph)
    if mcp_urls:
        from databricks_mcp import DatabricksMCPClient

        w_mcp = _fallback_client(client)

        def _resolve_mcp(url: str) -> list:
            try:
                mcp_client = DatabricksMCPClient(server_url=url, workspace_client=w_mcp)
                return mcp_client.get_databricks_resources()
            except Exception as exc:
                logger.warning("Could not resolve MCP resources for %s: %s", url, exc)
                return []

        with concurrent.futures.ThreadPoolExecutor() as pool:
            futures = {pool.submit(_resolve_mcp, url): url for url in mcp_urls}
            for future in concurrent.futures.as_completed(futures):
                for resource in future.result():
                    key = (type(resource).__name__, str(resource))
                    if key not in seen:
                        seen.add(key)
                        resources.append(resource)

    return resources


def _collect_mcp_urls(graph: GraphDef) -> list[str]:
    """Collect all MCP server URLs from the graph (nodes + tools_json).

    Includes explicit ``mcp_server`` URLs and managed MCP URLs derived
    from VS / Genie / UC Function node configs.
    """
    urls: list[str] = []

    def _add_from_config(config: dict, tool_type: str) -> None:
        if tool_type == "mcp_server":
            url = config.get("server_url")
            if url:
                urls.append(url)
        else:
            url = managed_mcp_url_for_tool(tool_type, config)
            if url:
                urls.append(url)

    for node in graph.nodes:
        _add_from_config(node.config, node.type)

        tools_json_raw = node.config.get("tools_json", "")
        if tools_json_raw and str(tools_json_raw).strip():
            try:
                tool_configs = json.loads(str(tools_json_raw))
                if isinstance(tool_configs, list):
                    for tc in tool_configs:
                        _add_from_config(tc.get("config", {}), tc.get("type", ""))
            except (json.JSONDecodeError, TypeError):
                pass
    return urls


def _persist_mcp_tool_metadata(graph: GraphDef, pat: str = "") -> None:
    """Discover MCP tools and inject ``discovered_tools`` into the graph config.

    Called at deploy time so the served model / app has tool metadata baked in
    and never needs to re-contact the MCP server for discovery.  Mutates
    the graph in place (caller should pass a deep copy).

    Handles all MCP-routed tool types: ``mcp_server`` (explicit MCP nodes)
    and ``vector_search``, ``genie``, ``uc_function`` (managed MCP routing).

    Uses a PAT-authenticated WorkspaceClient for discovery (same credential
    that works during preview).  Falls back to SP if no PAT is provided.
    """
    # Build a WorkspaceClient for MCP discovery
    pat_client = create_pat_client(pat) if pat else None

    def _discover(url: str) -> list:
        client = pat_client
        if not client:
            try:
                client = get_sp_workspace_client()
            except RuntimeError:
                from databricks.sdk import WorkspaceClient
                client = WorkspaceClient()
        return discover_mcp_tool_metadata(url, client)

    def _persist_for_config(
        tc_config: dict,
        tool_type: str,
        label: str,
    ) -> bool:
        """Discover and inject ``discovered_tools`` for one tool config.

        Returns True if the config was modified.
        """
        # Explicit MCP server URL
        url = tc_config.get("server_url", "") if tool_type == "mcp_server" else None

        # VS / Genie / UC Function → build managed MCP URL
        if not url:
            url = managed_mcp_url_for_tool(tool_type, tc_config)

        if not url:
            return False

        try:
            metadata = _discover(url)
            tc_config["discovered_tools"] = metadata
            # Persist the fully-qualified MCP URL so the served model
            # doesn't need to rebuild it from DATABRICKS_HOST (which
            # may not include the https:// protocol in serving envs).
            tc_config["mcp_server_url"] = url
            logger.info("Persisted %d MCP tools for %s (%s)", len(metadata), label, url)
            return True
        except Exception as exc:
            logger.warning("Failed to pre-discover MCP tools for %s (%s): %s",
                           label, url, exc)
            return False

    # Eligible types for MCP tool persistence
    _MCP_TYPES = {"mcp_server", "vector_search", "genie", "uc_function"}

    for node in graph.nodes:
        # Standalone nodes (not attached as tools)
        if node.type in _MCP_TYPES:
            _persist_for_config(node.config, node.type, f"node {node.id}")

        # Tools attached to LLM nodes via tools_json
        tools_json_raw = node.config.get("tools_json", "")
        if not (tools_json_raw and str(tools_json_raw).strip()):
            continue

        try:
            tool_configs = json.loads(str(tools_json_raw))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(tool_configs, list):
            continue

        modified = False
        for tc in tool_configs:
            tc_type = tc.get("type", "")
            if tc_type not in _MCP_TYPES:
                continue
            if _persist_for_config(tc.get("config", {}), tc_type, f"LLM tool on {node.id}"):
                modified = True

        if modified:
            node.config["tools_json"] = json.dumps(tool_configs)


def _build_auth_policy(
    graph: GraphDef,
    client=None,
) -> AuthPolicy:
    """Build an AuthPolicy for OBO (on-behalf-of) Model Serving deployment.

    Classification follows the Databricks agent auth docs:

    **SystemAuthPolicy.resources** — resources the endpoint's SP needs:
      - LLM serving endpoints (FMAPI rejects user tokens)
      - Genie spaces + their downstream SQL warehouses and tables

    **UserAuthPolicy.api_scopes** — direct SDK scopes for the user's
    token.  Serving endpoints use the direct SDK (not MCP), so they
    need the SDK-level scopes.

    ``_extract_resources()`` resolves all resources (including Genie
    downstream dependencies); this function then classifies each one as
    system vs. user-scoped.

    Args:
        graph: The graph definition.
        client: Optional WorkspaceClient (PAT-authenticated) for resolving
            Genie/MCP downstream dependencies.
    """
    # Resolve every resource the graph touches (Genie downstream deps, MCP,
    # etc.) — same list used for passthrough mode.
    all_resources = _extract_resources(graph, client=client)

    # Classify: system SP resources vs. user-scoped resources.
    # Per the docs, LLM endpoints / Genie spaces / SQL warehouses / tables
    # go into system auth; VS indexes and UC functions are user-scoped.
    _SYSTEM_TYPES = (
        DatabricksServingEndpoint,
        DatabricksGenieSpace,
        DatabricksSQLWarehouse,
        DatabricksTable,
    )
    system_resources = [r for r in all_resources if isinstance(r, _SYSTEM_TYPES)]

    return AuthPolicy(
        system_auth_policy=SystemAuthPolicy(resources=system_resources),
        user_auth_policy=UserAuthPolicy(api_scopes=sorted(graph_to_user_api_scopes(graph))),
    )


# ── Databricks Apps ("agents on apps") mappings ──────────────────────────
#
# The scope + resource model changed vs. the Model Serving era:
#   - Scopes are full dotted API-scope strings (serving.serving-endpoints,
#     vectorsearch.vector-search-indexes, dashboards.genie, …), not mcp.*.
#   - Vector Search was renamed "AI Search" and maps to a UC securable
#     (TABLE/SELECT) — the databricks-sdk has no dedicated VS AppResource type.

# Node/tool type → user_api_scopes (dotted API-scope strings). Used for BOTH
# the app's user_api_scopes and the serving path's UserAuthPolicy.api_scopes.
_TYPE_TO_USER_SCOPES: dict[str, tuple[str, ...]] = {
    "llm": ("serving.serving-endpoints",),
    "vector_search": (
        "vectorsearch.vector-search-endpoints",
        "vectorsearch.vector-search-indexes",
    ),
    "genie": ("dashboards.genie",),
    "uc_function": ("unity-catalog", "sql"),
    # MCP server nodes may reach any resource type via an external UC
    # connection, so grant the connection scope plus the common families.
    "mcp_server": (
        "catalog.connections",
        "unity-catalog",
        "sql",
        "dashboards.genie",
        "vectorsearch.vector-search-indexes",
    ),
}

# Always granted, regardless of graph contents.
_DEFAULT_USER_SCOPES = ("iam.current-user:read", "workspace.workspace")


def _iter_type_and_config(graph: GraphDef):
    """Yield (tool_type, config) for every node and every tools_json tool."""
    for node in graph.nodes:
        yield node.type, node.config

        tools_json_raw = node.config.get("tools_json", "")
        if tools_json_raw and str(tools_json_raw).strip():
            try:
                tool_configs = json.loads(str(tools_json_raw))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(tool_configs, list):
                for tc in tool_configs:
                    yield tc.get("type", ""), tc.get("config", {})


def graph_to_user_api_scopes(graph: GraphDef) -> list[str]:
    """Derive the sorted set of user API scopes a graph requires.

    Returns dotted API-scope strings for use as an app's ``user_api_scopes``
    (agents-on-apps) or ``UserAuthPolicy.api_scopes`` (Model Serving OBO).
    """
    scopes: set[str] = set(_DEFAULT_USER_SCOPES)
    for tool_type, _config in _iter_type_and_config(graph):
        scopes.update(_TYPE_TO_USER_SCOPES.get(tool_type, ()))
    return sorted(scopes)


def graph_to_app_resources(graph: GraphDef, client=None) -> list:
    """Translate a graph's resources into databricks-sdk ``AppResource`` objects.

    Reuses ``_extract_resources`` (which resolves Genie downstream deps + MCP
    resources into MLflow resource objects), then maps each MLflow resource to
    the corresponding ``AppResource`` subtype.  Verified against
    databricks-sdk 0.102.0 — there is no dedicated Vector Search AppResource,
    so AI Search / VS indexes map to a UC securable (TABLE/SELECT).
    """
    from databricks.sdk.service.apps import (
        AppResource,
        AppResourceGenieSpace,
        AppResourceGenieSpaceGenieSpacePermission,
        AppResourceServingEndpoint,
        AppResourceServingEndpointServingEndpointPermission,
        AppResourceSqlWarehouse,
        AppResourceSqlWarehouseSqlWarehousePermission,
        AppResourceUcSecurable,
        AppResourceUcSecurableUcSecurablePermission,
        AppResourceUcSecurableUcSecurableType,
    )

    mlflow_resources = _extract_resources(graph, client=client)

    app_resources: list = []
    seen: set[str] = set()
    counter = 0

    def _emit(name_hint: str, **kwargs) -> None:
        nonlocal counter
        key = str(kwargs)
        if key in seen:
            return
        seen.add(key)
        counter += 1
        # AppResource names must be unique + resource-name-safe.
        safe = f"{name_hint}_{counter}"
        app_resources.append(AppResource(name=safe, **kwargs))

    # Every MLflow resource stores its identifier in ``.name`` (the constructor
    # kwargs like ``endpoint_name`` are not exposed as attributes).
    for r in mlflow_resources:
        if isinstance(r, DatabricksServingEndpoint):
            _emit("endpoint", serving_endpoint=AppResourceServingEndpoint(
                name=r.name,
                permission=AppResourceServingEndpointServingEndpointPermission.CAN_QUERY,
            ))
        elif isinstance(r, DatabricksVectorSearchIndex):
            _emit("index", uc_securable=AppResourceUcSecurable(
                securable_full_name=r.name,
                securable_type=AppResourceUcSecurableUcSecurableType.TABLE,
                permission=AppResourceUcSecurableUcSecurablePermission.SELECT,
            ))
        elif isinstance(r, DatabricksFunction):
            _emit("function", uc_securable=AppResourceUcSecurable(
                securable_full_name=r.name,
                securable_type=AppResourceUcSecurableUcSecurableType.FUNCTION,
                permission=AppResourceUcSecurableUcSecurablePermission.EXECUTE,
            ))
        elif isinstance(r, DatabricksTable):
            _emit("table", uc_securable=AppResourceUcSecurable(
                securable_full_name=r.name,
                securable_type=AppResourceUcSecurableUcSecurableType.TABLE,
                permission=AppResourceUcSecurableUcSecurablePermission.SELECT,
            ))
        elif isinstance(r, DatabricksGenieSpace):
            _emit("genie", genie_space=AppResourceGenieSpace(
                space_id=r.name,
                permission=AppResourceGenieSpaceGenieSpacePermission.CAN_RUN,
            ))
        elif isinstance(r, DatabricksSQLWarehouse):
            _emit("warehouse", sql_warehouse=AppResourceSqlWarehouse(
                id=r.name,
                permission=AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE,
            ))

    return app_resources


def lakebase_app_resource(instance_name: str, database_name: str):
    """Build an AppResource granting the app SP access to a Lakebase database."""
    from databricks.sdk.service.apps import (
        AppResource,
        AppResourceDatabase,
        AppResourceDatabaseDatabasePermission,
    )

    return AppResource(
        name="lakebase",
        database=AppResourceDatabase(
            instance_name=instance_name,
            database_name=database_name,
            permission=AppResourceDatabaseDatabasePermission.CAN_CONNECT_AND_CREATE,
        ),
    )
