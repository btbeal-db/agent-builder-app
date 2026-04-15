from __future__ import annotations

import logging
from typing import Any

from .base import BaseNode, NodeConfigField, resolve_state
from . import register

logger = logging.getLogger(__name__)


@register
class MCPServerNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "mcp_server"

    @property
    def display_name(self) -> str:
        return "MCP Server"

    @property
    def description(self) -> str:
        return "Connect to a Databricks MCP server and use its tools."

    @property
    def category(self) -> str:
        return "action"

    @property
    def icon(self) -> str:
        return "plug"

    @property
    def color(self) -> str:
        return "#f59e0b"

    @property
    def tool_compatible(self) -> bool:
        return True

    @property
    def default_field_template(self) -> dict[str, str] | None:
        return {"name": "mcp_result", "type": "str", "description": "MCP tool output"}

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="server_url",
                label="Server URL",
                placeholder="https://my-workspace.cloud.databricks.com/api/2.0/mcp/functions/catalog/schema",
                help_text="Databricks MCP server endpoint URL. Auth is handled automatically via your workspace credentials.",
            ),
            NodeConfigField(
                name="tool_filter",
                label="Tool Filter",
                required=False,
                placeholder="tool_a, tool_b",
                help_text="Comma-separated list of MCP tool names to expose. Leave empty for all tools.",
            ),
            NodeConfigField(
                name="tool_name",
                label="Tool Name",
                required=False,
                placeholder="my_mcp_tool",
                help_text="Standalone mode: specific MCP tool to call. Leave empty when using as an LLM tool.",
            ),
            NodeConfigField(
                name="query_from",
                label="Input from",
                field_type="state_variable",
                required=False,
                default="input",
                help_text="Standalone mode: state variable containing the tool input (passed as 'query' argument).",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Standalone execution: connect to MCP server and call a specific tool."""
        writes_to = config.get("_writes_to", "")
        server_url = config.get("server_url", "")
        tool_name = config.get("tool_name", "")

        if not server_url:
            return {
                writes_to: "Error: no MCP server URL configured.",
                "messages": [{"role": "system", "content": "MCP Server: missing server_url.", "node": "mcp_server"}],
            }
        if not tool_name:
            return {
                writes_to: "Error: no tool_name configured for standalone MCP node.",
                "messages": [{"role": "system", "content": "MCP Server: missing tool_name for standalone execution.", "node": "mcp_server"}],
            }

        query = resolve_state(state, config.get("query_from", "input"))

        try:
            from ..tools import _get_mcp_token, _mcp_call_tool, _run_mcp_in_thread

            token = _get_mcp_token()
            result = _run_mcp_in_thread(
                _mcp_call_tool, server_url, token, tool_name, {"query": str(query)},
            )
            parts = [c.text for c in result.content if hasattr(c, "text")]
            result_text = "\n".join(parts) if parts else "(no output)"

        except Exception as exc:
            logger.exception("MCP tool call failed (server=%s, tool=%s)", server_url, tool_name)
            return {
                writes_to: f"MCP Server error: {exc}",
                "messages": [{"role": "system", "content": f"MCP Server error: {exc}", "node": "mcp_server"}],
            }

        return {
            writes_to: result_text,
            "messages": [
                {"role": "system", "content": f"[MCP: {tool_name}]\n{result_text}", "node": "mcp_server"}
            ],
        }
