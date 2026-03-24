"""Integration tests for the tool factory — real SDK calls."""

from __future__ import annotations

import json

import pytest

from backend.tools import make_tools_from_json

pytestmark = pytest.mark.integration


class TestToolFactory:
    def test_vector_search_tool(self, vs_index_name, vs_endpoint_name):
        tools_json = json.dumps([{
            "type": "vector_search",
            "config": {
                "index_name": vs_index_name,
                "endpoint_name": vs_endpoint_name,
                "columns": "note_id,department,text",
                "num_results": 2,
                "enable_reranker": "false",
            },
        }])
        tools = make_tools_from_json(tools_json)
        assert len(tools) == 1
        # Invoke the tool
        result = tools[0].invoke({"query": "cardiology"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_genie_tool(self, genie_room_id):
        tools_json = json.dumps([{
            "type": "genie",
            "config": {
                "room_id": genie_room_id,
            },
        }])
        tools = make_tools_from_json(tools_json)
        assert len(tools) == 1
        result = tools[0].invoke({"question": "How many patients are there?"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_invalid_json_returns_empty(self):
        tools = make_tools_from_json("not valid json")
        assert tools == []

    def test_empty_list(self):
        tools = make_tools_from_json("[]")
        assert tools == []

    def test_unknown_tool_type_skipped(self):
        tools_json = json.dumps([{"type": "nonexistent", "config": {}}])
        tools = make_tools_from_json(tools_json)
        assert tools == []
