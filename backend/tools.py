"""Factory that converts ToolConfig entries into LangChain BaseTool instances.

Each tool wraps the same core logic used by the corresponding graph node,
but exposed as a callable tool for the ReAct agent.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import MessageStatus
from databricks.sdk.service.vectorsearch import RerankerConfig, RerankerConfigRerankerParameters
from databricks_langchain import UCFunctionToolkit
from databricks_langchain.uc_ai import DatabricksFunctionClient
from databricks_mcp import DatabricksOAuthClientProvider
from langchain_core.tools import BaseTool, StructuredTool, tool
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .auth import get_data_client, get_user_token

logger = logging.getLogger(__name__)


def _make_vector_search_tool(config: dict[str, Any]) -> BaseTool:
    """Create a tool that queries a Databricks Vector Search index."""
    index_name = config.get("index_name", "")
    endpoint_name = config.get("endpoint_name", "")
    columns_raw = config.get("columns", "")
    columns = [c.strip() for c in columns_raw.split(",") if c.strip()]
    num_results = int(config.get("num_results", 3))

    score_threshold = config.get("score_threshold")
    if score_threshold is not None and score_threshold != "":
        score_threshold = float(score_threshold)
    else:
        score_threshold = None

    enable_reranker = str(config.get("enable_reranker", "true")).lower() == "true"

    @tool
    def vector_search(query: str, filters: str = "") -> str:
        """Search for relevant documents in a vector index."""
        reranker = None
        if enable_reranker:
            rerank_cols_raw = config.get("columns_to_rerank", "")
            rerank_cols = (
                [c.strip() for c in rerank_cols_raw.split(",") if c.strip()]
                if rerank_cols_raw
                else None
            )
            if rerank_cols:
                reranker = RerankerConfig(
                    model="databricks_reranker",
                    parameters=RerankerConfigRerankerParameters(columns_to_rerank=rerank_cols),
                )

        filters_json = None
        if filters and filters.strip():
            try:
                json.loads(filters)  # validate
                filters_json = filters
            except json.JSONDecodeError:
                pass

        w = get_data_client()
        response = w.vector_search_indexes.query_index(
            index_name=index_name,
            columns=columns,
            query_text=query,
            num_results=num_results,
            score_threshold=score_threshold,
            filters_json=filters_json,
            reranker=reranker,
        )

        result_dict = response.as_dict()
        docs: list[str] = []
        data_chunk = result_dict.get("result", {}).get("data_array", [])
        col_names = [c["name"] for c in result_dict.get("manifest", {}).get("columns", [])]

        for row in data_chunk:
            row_parts = [f"{col_names[i]}: {row[i]}" for i in range(len(row)) if i < len(col_names)]
            docs.append("\n".join(row_parts))

        return "\n\n---\n\n".join(docs) if docs else "(no results)"

    # Give the tool a descriptive name and docstring so the LLM knows when to use it
    vector_search.name = f"search_{index_name.replace('.', '_')}"
    custom_desc = config.get("tool_description", "")
    cols_str = ", ".join(columns)
    vector_search.description = custom_desc or (
        f"Search the vector index '{index_name}' for relevant documents. "
        f"Returns the top {num_results} results with columns: {cols_str}."
    )
    if not custom_desc:
        vector_search.description += (
            f' Optionally filter results by passing a JSON string to the "filters" parameter using column names: {cols_str}. '
            f'Example: \'{{"column_name": "value"}}\' for exact match, \'{{"column_name >=": value}}\' for comparisons.'
        )
    return vector_search


def _make_genie_tool(config: dict[str, Any]) -> BaseTool:
    """Create a tool that queries a Databricks Genie Room."""
    room_id = config.get("room_id", "")

    @tool
    def genie_query(question: str) -> str:
        """Ask a natural-language question to get structured data answers."""
        w = get_data_client()
        try:
            message = w.genie.start_conversation_and_wait(room_id, question)
        except Exception as exc:
            # start_conversation_and_wait raises OperationFailed when the
            # Genie query fails (e.g. SQL error, permissions). Return the
            # error as text so the LLM can inform the user gracefully.
            return f"Genie error: {exc}"

        if message.status == MessageStatus.FAILED:
            error_text = message.error.message if message.error else "Unknown error"
            return f"Genie error: {error_text}"

        parts: list[str] = []
        for attachment in message.attachments or []:
            if attachment.text and attachment.text.content:
                parts.append(attachment.text.content)

            if attachment.query and attachment.attachment_id:
                try:
                    result = w.genie.get_message_attachment_query_result(
                        room_id,
                        message.conversation_id,
                        message.message_id,
                        attachment.attachment_id,
                    )
                    # Format query result
                    query_parts: list[str] = []
                    if attachment.query.description:
                        query_parts.append(attachment.query.description)
                    if attachment.query.query:
                        query_parts.append(f"```sql\n{attachment.query.query}\n```")

                    stmt = result.statement_response if result else None
                    if stmt and stmt.result and stmt.result.data_array:
                        cols = []
                        if stmt.manifest and stmt.manifest.schema and stmt.manifest.schema.columns:
                            cols = [c.name or f"col_{i}" for i, c in enumerate(stmt.manifest.schema.columns)]
                        rows = stmt.result.data_array[:50]
                        if cols:
                            header = "| " + " | ".join(cols) + " |"
                            sep = "| " + " | ".join("---" for _ in cols) + " |"
                            table_rows = [
                                "| " + " | ".join(str(v) if v is not None else "" for v in row) + " |"
                                for row in rows
                            ]
                            query_parts.append("\n".join([header, sep, *table_rows]))
                    parts.append("\n\n".join(query_parts))
                except Exception as exc:
                    parts.append(f"(failed to fetch query result: {exc})")

        return "\n\n".join(parts) if parts else "(Genie returned no content)"

    genie_query.name = f"genie_{room_id}"
    custom_desc = config.get("tool_description", "")
    genie_query.description = custom_desc or (
        f"Query Genie Room '{room_id}' with a natural-language question "
        f"to get structured data answers from your tables."
    )
    return genie_query


def _make_uc_function_tools(config: dict[str, Any]) -> list[BaseTool]:
    """Create tool(s) from a Unity Catalog function."""
    function_name = config.get("function_name", "")
    custom_desc = config.get("tool_description", "")
    # Pass the data client so UC functions use OBO auth when configured
    w = get_data_client()
    client = DatabricksFunctionClient(client=w)
    toolkit = UCFunctionToolkit(function_names=[function_name], client=client)
    tools = toolkit.tools
    if custom_desc and tools:
        tools[0].description = custom_desc
    return tools


# ── MCP helpers ──────────────────────────────────────────────────────────────


def _get_mcp_client(server_url: str) -> WorkspaceClient:
    """Return a WorkspaceClient for MCP server communication.

    Same credential priority as VS, Genie, and UC Function nodes:
    PAT > OBO > SP (via ``get_data_client()``).

    For Databricks Apps URLs the OBO token from the Apps proxy is
    preferred because it is an OAuth token that Apps accept reliably.
    """
    if urlparse(server_url).netloc.endswith(".databricksapps.com"):
        obo = get_user_token()
        if obo:
            host = os.environ.get("DATABRICKS_HOST", "")
            return WorkspaceClient(host=host, token=obo, auth_type="pat")

    return get_data_client()


def _run_mcp_in_thread(fn, *args, **kwargs):
    """Run a function in a dedicated thread.

    The MCP SDK uses ``asyncio.run()`` internally, which crashes if an
    event loop is already running (e.g. inside a FastAPI handler).
    Running in a separate thread guarantees no existing event loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


def _mcp_session(server_url: str, client: WorkspaceClient):
    """Open an authenticated MCP session via Streamable HTTP.

    Uses ``DatabricksOAuthClientProvider`` for proper OAuth auth,
    which is required by the Databricks MCP proxy (especially for
    external MCP connections).
    """
    return streamablehttp_client(
        url=server_url,
        auth=DatabricksOAuthClientProvider(client),
    )


def _mcp_list_tools(server_url: str, client: WorkspaceClient):
    """Discover tools from an MCP server."""

    async def _discover():
        async with _mcp_session(server_url, client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    return asyncio.run(_discover())


def _mcp_call_tool(server_url: str, client: WorkspaceClient, tool_name: str, arguments: dict):
    """Call a tool on an MCP server."""

    async def _call():
        async with _mcp_session(server_url, client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    return asyncio.run(_call())


def discover_mcp_tool_metadata(
    server_url: str,
    client: WorkspaceClient | None = None,
) -> list[dict[str, Any]]:
    """Discover MCP tools and return their metadata as serializable dicts.

    Each dict contains ``name``, ``description``, and ``inputSchema`` —
    everything needed to recreate LangChain tools without re-contacting
    the MCP server.  This is called at deploy time to persist tool
    metadata in the graph artifact.

    If *client* is not provided, one is obtained via ``_get_mcp_client``.
    """
    if not server_url:
        return []

    if client is None:
        client = _get_mcp_client(server_url)

    mcp_tools = _run_mcp_in_thread(_mcp_list_tools, server_url, client)
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "inputSchema": t.inputSchema,
        }
        for t in mcp_tools
    ]


def _make_mcp_tools(config: dict[str, Any]) -> list[BaseTool]:
    """Create LangChain tools from an MCP server.

    **Persisted path** (deployed models): if the config contains
    ``discovered_tools`` (a list of ``{name, description, inputSchema}``
    dicts saved at deploy time), tools are built from that metadata
    without contacting the MCP server.  This avoids runtime discovery
    failures caused by network/auth differences in the serving env.

    **Live discovery path** (preview): falls back to connecting to the
    MCP server at execution time.  Uses ``DatabricksOAuthClientProvider``
    for proper OAuth auth as required by the Databricks MCP proxy.
    """
    server_url = config.get("server_url", "")
    if not server_url:
        logger.warning("MCP tool config missing server_url")
        return []

    # ── Resolve tool metadata ───────────────────────────────────────
    # Prefer persisted metadata (injected at deploy time) so the served
    # model never needs to re-discover tools from the MCP server.
    persisted = config.get("discovered_tools")
    if persisted and isinstance(persisted, list):
        logger.info("Using %d persisted MCP tools for %s", len(persisted), server_url)
        tool_defs = persisted
    else:
        # Live discovery — retry once (cold-start on Databricks Apps).
        mcp_tools = None
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                client = _get_mcp_client(server_url)
                if attempt == 0:
                    logger.info("MCP client obtained for %s (auth_type=%s)",
                                server_url, client.config.auth_type)
                mcp_tools = _run_mcp_in_thread(_mcp_list_tools, server_url, client)
                break
            except Exception as exc:
                last_err = exc
                if attempt == 0:
                    logger.warning("MCP discovery attempt 1 failed for %s, retrying: %s",
                                   server_url, exc)
        if mcp_tools is None:
            logger.error("Failed to discover MCP tools from %s after 2 attempts: %s",
                         server_url, last_err)
            return []

        logger.info("Discovered %d MCP tools from %s: %s",
                     len(mcp_tools), server_url, [t.name for t in mcp_tools])
        tool_defs = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in mcp_tools
        ]

    # ── Apply tool name filter ──────────────────────────────────────
    tool_filter = config.get("tool_filter", "")
    if tool_filter and str(tool_filter).strip():
        allowed = {t.strip() for t in str(tool_filter).split(",") if t.strip()}
        tool_defs = [t for t in tool_defs if t["name"] in allowed]

    # ── Build LangChain tools ───────────────────────────────────────
    custom_desc = config.get("tool_description", "")
    sync_tools: list[BaseTool] = []

    for td in tool_defs:
        tool_name = td["name"]

        def _make_fn(_name: str = tool_name) -> Any:
            def call_tool(**kwargs: Any) -> str:
                client = _get_mcp_client(server_url)  # fresh client per call
                result = _run_mcp_in_thread(
                    _mcp_call_tool, server_url, client, _name, kwargs,
                )
                parts = [c.text for c in result.content if hasattr(c, "text")]
                return "\n".join(parts) if parts else "(no output)"
            return call_tool

        desc = td.get("description", "")
        if custom_desc and len(tool_defs) == 1:
            desc = custom_desc

        sync_tools.append(
            StructuredTool(
                name=tool_name,
                description=desc,
                args_schema=td.get("inputSchema", {}),
                func=_make_fn(),
            )
        )

    return sync_tools


def make_tools(tool_configs: list[dict[str, Any]]) -> list[BaseTool]:
    """Convert a list of tool config dicts into LangChain tools.

    Each dict should have ``{"type": "...", "config": {...}}``.
    """
    tools: list[BaseTool] = []
    for tc in tool_configs:
        tool_type = tc.get("type", "")
        config = tc.get("config", {})
        if tool_type == "uc_function":
            tools.extend(_make_uc_function_tools(config))
        elif tool_type == "vector_search":
            tools.append(_make_vector_search_tool(config))
        elif tool_type == "genie":
            tools.append(_make_genie_tool(config))
        elif tool_type == "mcp_server":
            tools.extend(_make_mcp_tools(config))
        else:
            logger.warning("Unknown tool type: %s", tool_type)
    return tools


def make_tools_from_json(tools_json: str) -> list[BaseTool]:
    """Parse a JSON string of tool configs and return LangChain tools."""
    try:
        tool_configs = json.loads(tools_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid tools_json: %s", tools_json[:100])
        return []
    if not isinstance(tool_configs, list):
        logger.warning("tools_json must be a JSON array, got %s", type(tool_configs))
        return []
    return make_tools(tool_configs)
