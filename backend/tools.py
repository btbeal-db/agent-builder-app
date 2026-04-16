"""Factory that converts ToolConfig entries into LangChain BaseTool instances.

Each tool wraps the same core logic used by the corresponding graph node,
but exposed as a callable tool for the ReAct agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from databricks.sdk.service.dashboards import MessageStatus

from .auth import get_data_client
from databricks.sdk.service.vectorsearch import RerankerConfig, RerankerConfigRerankerParameters
from databricks_langchain import UCFunctionToolkit
from langchain_core.tools import BaseTool, StructuredTool, tool

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
    from databricks_langchain.uc_ai import DatabricksFunctionClient

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


def _get_mcp_token(server_url: str) -> str:
    """Get a Bearer token for MCP calls.

    Same credential priority as VS, Genie, and UC Function nodes:
    PAT > OBO > SP (via ``get_data_client()``).

    For Databricks Apps URLs the OBO token from the Apps proxy is
    preferred because it is an OAuth token that Apps accept reliably.
    The PAT from the banner is used as a fallback.
    """
    from urllib.parse import urlparse
    from .auth import get_user_token

    # On the deployed app, the OBO token (OAuth) is the most reliable
    # credential for Apps URLs.  Try it first before the general path.
    if urlparse(server_url).netloc.endswith(".databricksapps.com"):
        obo = get_user_token()
        if obo:
            return obo

    # General path: PAT > OBO > SP
    w = get_data_client()
    headers = w.config.authenticate()
    return headers["Authorization"].split("Bearer ", 1)[1]


def _run_mcp_in_thread(fn, *args, **kwargs):
    """Run an async MCP operation in a dedicated thread.

    The MCP SDK uses ``asyncio.run()`` internally, which crashes if an
    event loop is already running (e.g. inside a FastAPI handler).
    Running in a separate thread guarantees no existing event loop.
    """
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


def _mcp_list_tools(server_url: str, token: str):
    """Discover tools from an MCP server using the raw MCP SDK.

    Bypasses ``DatabricksMCPClient`` which rejects PAT auth for Apps URLs.
    PAT works fine as a Bearer token at the HTTP level.
    """
    import asyncio
    import httpx
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async def _discover():
        async with streamablehttp_client(
            url=server_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    return asyncio.run(_discover())


def _mcp_call_tool(server_url: str, token: str, tool_name: str, arguments: dict):
    """Call a tool on an MCP server using the raw MCP SDK."""
    import asyncio
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async def _call():
        async with streamablehttp_client(
            url=server_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    return asyncio.run(_call())


def _make_mcp_tools(config: dict[str, Any]) -> list[BaseTool]:
    """Create LangChain tools from an MCP server.

    Uses the user's PAT (via ``get_data_client()``) as a Bearer token,
    same auth model as VS, Genie, and UC Function nodes.  Bypasses
    ``DatabricksMCPClient`` which rejects PAT for Apps URLs.
    """
    server_url = config.get("server_url", "")
    if not server_url:
        logger.warning("MCP tool config missing server_url")
        return []

    # Discover tools — runs in a thread to avoid event loop conflicts
    try:
        token = _get_mcp_token(server_url)
        mcp_tools = _run_mcp_in_thread(_mcp_list_tools, server_url, token)
    except Exception:
        logger.exception("Failed to discover MCP tools from %s", server_url)
        return []

    logger.info("Discovered %d MCP tools from %s: %s",
                len(mcp_tools), server_url, [t.name for t in mcp_tools])

    # Apply tool name filter
    tool_filter = config.get("tool_filter", "")
    if tool_filter and str(tool_filter).strip():
        allowed = {t.strip() for t in str(tool_filter).split(",") if t.strip()}
        mcp_tools = [t for t in mcp_tools if t.name in allowed]

    custom_desc = config.get("tool_description", "")
    sync_tools: list[BaseTool] = []

    for mcp_tool in mcp_tools:
        def _make_fn(tool_name: str = mcp_tool.name) -> Any:
            def call_tool(**kwargs: Any) -> str:
                tok = _get_mcp_token(server_url)  # fresh token per call
                result = _run_mcp_in_thread(
                    _mcp_call_tool, server_url, tok, tool_name, kwargs,
                )
                parts = [c.text for c in result.content if hasattr(c, "text")]
                return "\n".join(parts) if parts else "(no output)"
            return call_tool

        desc = mcp_tool.description or ""
        if custom_desc and len(mcp_tools) == 1:
            desc = custom_desc

        sync_tools.append(
            StructuredTool(
                name=mcp_tool.name,
                description=desc,
                args_schema=mcp_tool.inputSchema,
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
