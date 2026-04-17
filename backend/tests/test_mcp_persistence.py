"""Unit tests for MCP tool metadata persistence.

Verifies that:
- _make_mcp_tools uses persisted metadata when available (skip discovery)
- _make_mcp_tools falls back to live discovery when persisted metadata is absent
- _persist_mcp_tool_metadata injects discovered_tools into the graph_def
- discover_mcp_tool_metadata returns serializable dicts
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.schema import GraphDef, NodeDef, EdgeDef, StateFieldDef
from backend.tools import _make_mcp_tools, discover_mcp_tool_metadata, make_tools_from_json


# ── Fake MCP tool metadata ──────────────────────────────────────────────────

FAKE_MCP_TOOLS = [
    SimpleNamespace(
        name="search_docs",
        description="Search documents by query",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    ),
    SimpleNamespace(
        name="get_schema",
        description="Get table schema",
        inputSchema={
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    ),
]

FAKE_PERSISTED = [
    {
        "name": "search_docs",
        "description": "Search documents by query",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_schema",
        "description": "Get table schema",
        "inputSchema": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    },
]


# ── _make_mcp_tools with persisted metadata ─────────────────────────────────


class TestMakeMCPToolsPersistedPath:
    """When discovered_tools is present, skip live discovery entirely."""

    def test_uses_persisted_metadata(self):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": FAKE_PERSISTED,
        }
        tools = _make_mcp_tools(config)
        assert len(tools) == 2
        assert tools[0].name == "search_docs"
        assert tools[1].name == "get_schema"

    @patch("backend.tools._run_mcp_in_thread")
    @patch("backend.tools._get_mcp_token")
    def test_persisted_skips_discovery(self, mock_token, mock_thread):
        """Live discovery helpers should NOT be called when persisted tools exist."""
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": FAKE_PERSISTED,
        }
        _make_mcp_tools(config)
        mock_token.assert_not_called()
        mock_thread.assert_not_called()

    def test_tool_filter_applied_to_persisted(self):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": FAKE_PERSISTED,
            "tool_filter": "search_docs",
        }
        tools = _make_mcp_tools(config)
        assert len(tools) == 1
        assert tools[0].name == "search_docs"

    def test_custom_description_applied_to_persisted(self):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": [FAKE_PERSISTED[0]],  # single tool
            "tool_description": "Custom description for the LLM",
        }
        tools = _make_mcp_tools(config)
        assert len(tools) == 1
        assert tools[0].description == "Custom description for the LLM"

    def test_empty_persisted_list_returns_empty(self):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": [],
        }
        tools = _make_mcp_tools(config)
        assert tools == []

    def test_tool_callable_with_persisted(self):
        """Persisted tools should still produce callable functions."""
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            "discovered_tools": [FAKE_PERSISTED[0]],
        }
        tools = _make_mcp_tools(config)
        assert len(tools) == 1
        # The tool function should be callable (though it would fail without a real server)
        assert callable(tools[0].func)


# ── _make_mcp_tools live discovery fallback ─────────────────────────────────


class TestMakeMCPToolsLiveDiscovery:
    """When discovered_tools is absent, fall back to live discovery."""

    @patch("backend.tools._run_mcp_in_thread", return_value=FAKE_MCP_TOOLS)
    @patch("backend.tools._get_mcp_token", return_value="fake-token")
    def test_live_discovery_when_no_persisted(self, mock_token, mock_thread):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
        }
        tools = _make_mcp_tools(config)
        assert len(tools) == 2
        mock_token.assert_called()
        mock_thread.assert_called()

    @patch("backend.tools._run_mcp_in_thread", side_effect=Exception("connection refused"))
    @patch("backend.tools._get_mcp_token", return_value="fake-token")
    def test_live_discovery_failure_returns_empty(self, mock_token, mock_thread):
        config = {
            "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
        }
        tools = _make_mcp_tools(config)
        assert tools == []

    def test_missing_server_url_returns_empty(self):
        tools = _make_mcp_tools({"server_url": ""})
        assert tools == []
        tools = _make_mcp_tools({})
        assert tools == []


# ── discover_mcp_tool_metadata ──────────────────────────────────────────────


class TestDiscoverMCPToolMetadata:
    @patch("backend.tools._run_mcp_in_thread", return_value=FAKE_MCP_TOOLS)
    def test_returns_serializable_dicts(self, mock_thread):
        result = discover_mcp_tool_metadata(
            "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            token="fake-token",
        )
        assert len(result) == 2
        assert result[0]["name"] == "search_docs"
        assert result[0]["description"] == "Search documents by query"
        assert "inputSchema" in result[0]
        # Must be JSON-serializable
        json.dumps(result)

    def test_empty_url_returns_empty(self):
        result = discover_mcp_tool_metadata("", token="fake-token")
        assert result == []


# ── make_tools_from_json with persisted MCP ─────────────────────────────────


class TestMakeToolsFromJSONWithPersistedMCP:
    """End-to-end: tools_json containing an MCP config with discovered_tools."""

    def test_persisted_mcp_through_json_pipeline(self):
        tools_json = json.dumps([{
            "type": "mcp_server",
            "config": {
                "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
                "discovered_tools": FAKE_PERSISTED,
            },
        }])
        tools = make_tools_from_json(tools_json)
        assert len(tools) == 2
        assert tools[0].name == "search_docs"
        assert tools[1].name == "get_schema"

    def test_mixed_tool_types_with_persisted_mcp(self):
        """MCP tools alongside other tool types (e.g. genie)."""
        tools_json = json.dumps([
            {
                "type": "mcp_server",
                "config": {
                    "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
                    "discovered_tools": [FAKE_PERSISTED[0]],
                },
            },
            {
                "type": "genie",
                "config": {"room_id": "test-room"},
            },
        ])
        tools = make_tools_from_json(tools_json)
        # 1 MCP tool + 1 Genie tool
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "search_docs" in names
        assert "genie_test-room" in names


# ── _persist_mcp_tool_metadata ──────────────────────────────────────────────


class TestPersistMCPToolMetadata:
    """Test the deploy-time injection of discovered_tools into graph_def."""

    @patch("backend.tools._run_mcp_in_thread", return_value=FAKE_MCP_TOOLS)
    @patch("backend.tools._get_mcp_token", return_value="fake-token")
    def test_injects_into_standalone_mcp_node(self, mock_token, mock_thread):
        from backend.main import _persist_mcp_tool_metadata

        graph = GraphDef(
            nodes=[
                NodeDef(
                    id="mcp_1", type="mcp_server", writes_to="result",
                    config={
                        "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
                        "tool_name": "search_docs",
                    },
                ),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="mcp_1"),
                EdgeDef(id="e2", source="mcp_1", target="__end__"),
            ],
        )
        _persist_mcp_tool_metadata(graph, pat="fake-pat")

        assert "discovered_tools" in graph.nodes[0].config
        discovered = graph.nodes[0].config["discovered_tools"]
        assert len(discovered) == 2
        assert discovered[0]["name"] == "search_docs"

    @patch("backend.tools._run_mcp_in_thread", return_value=FAKE_MCP_TOOLS)
    @patch("backend.tools._get_mcp_token", return_value="fake-token")
    def test_injects_into_llm_tools_json(self, mock_token, mock_thread):
        from backend.main import _persist_mcp_tool_metadata

        tools_json = json.dumps([{
            "type": "mcp_server",
            "config": {
                "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
            },
        }])
        graph = GraphDef(
            nodes=[
                NodeDef(
                    id="llm_1", type="llm", writes_to="output",
                    config={
                        "endpoint": "databricks-meta-llama-3-3-70b-instruct",
                        "tools_json": tools_json,
                    },
                ),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="llm_1"),
                EdgeDef(id="e2", source="llm_1", target="__end__"),
            ],
        )
        _persist_mcp_tool_metadata(graph, pat="fake-pat")

        # tools_json should now contain discovered_tools
        updated = json.loads(graph.nodes[0].config["tools_json"])
        assert len(updated) == 1
        mcp_config = updated[0]["config"]
        assert "discovered_tools" in mcp_config
        assert len(mcp_config["discovered_tools"]) == 2

    @patch("backend.tools._run_mcp_in_thread", side_effect=Exception("timeout"))
    @patch("backend.tools._get_mcp_token", return_value="fake-token")
    def test_discovery_failure_does_not_crash(self, mock_token, mock_thread):
        """Deploy should continue even if MCP discovery fails."""
        from backend.main import _persist_mcp_tool_metadata

        graph = GraphDef(
            nodes=[
                NodeDef(
                    id="mcp_1", type="mcp_server", writes_to="result",
                    config={
                        "server_url": "https://workspace.databricks.com/api/2.0/mcp/functions/cat/schema",
                        "tool_name": "search_docs",
                    },
                ),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="mcp_1"),
                EdgeDef(id="e2", source="mcp_1", target="__end__"),
            ],
        )
        # Should not raise
        _persist_mcp_tool_metadata(graph, pat="fake-pat")
        # discovered_tools should NOT be present since discovery failed
        assert "discovered_tools" not in graph.nodes[0].config

    def test_non_mcp_nodes_untouched(self):
        from backend.main import _persist_mcp_tool_metadata

        graph = GraphDef(
            nodes=[
                NodeDef(
                    id="llm_1", type="llm", writes_to="output",
                    config={"endpoint": "databricks-meta-llama-3-3-70b-instruct"},
                ),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="llm_1"),
                EdgeDef(id="e2", source="llm_1", target="__end__"),
            ],
        )
        _persist_mcp_tool_metadata(graph, pat="fake-pat")
        assert "discovered_tools" not in graph.nodes[0].config
