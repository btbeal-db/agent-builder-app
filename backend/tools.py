"""Factory that converts ToolConfig entries into LangChain BaseTool instances.

Each tool wraps the same core logic used by the corresponding graph node,
but exposed as a callable tool for the ReAct agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from databricks.sdk.service.dashboards import MessageStatus

from .auth import get_workspace_client, get_sp_workspace_client
from databricks.sdk.service.vectorsearch import RerankerConfig, RerankerConfigRerankerParameters
from databricks_langchain import UCFunctionToolkit
from langchain_core.tools import BaseTool, tool

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

        w = get_sp_workspace_client()
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
        w = get_workspace_client()
        message = w.genie.start_conversation_and_wait(room_id, question)

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
    toolkit = UCFunctionToolkit(function_names=[function_name])
    tools = toolkit.tools
    if custom_desc and tools:
        tools[0].description = custom_desc
    return tools


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
