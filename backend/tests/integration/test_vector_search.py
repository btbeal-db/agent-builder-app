"""Integration tests for Vector Search node — real VS queries."""

from __future__ import annotations

import pytest

from backend.nodes.vector_search_node import VectorSearchNode

pytestmark = pytest.mark.integration


class TestVectorSearchIntegration:
    def setup_method(self):
        self.node = VectorSearchNode()

    def test_basic_query(self, vs_index_name, vs_endpoint_name):
        state = {"input": "cardiology patient notes", "messages": []}
        config = {
            "_writes_to": "context",
            "_target_field": None,
            "query_from": "input",
            "index_name": vs_index_name,
            "endpoint_name": vs_endpoint_name,
            "columns": "note_id,patient_id,department,text",
            "num_results": 3,
            "enable_reranker": "false",
        }
        result = self.node.execute(state, config)
        assert "context" in result
        # Should have actual content, not an error
        assert "error" not in result["context"].lower() or "Error" not in result["context"]

    def test_with_filters(self, vs_index_name, vs_endpoint_name):
        state = {
            "input": "patient notes",
            "filters": '{"department": "Cardiology"}',
            "messages": [],
        }
        config = {
            "_writes_to": "context",
            "_target_field": None,
            "query_from": "input",
            "index_name": vs_index_name,
            "endpoint_name": vs_endpoint_name,
            "columns": "note_id,department,text",
            "num_results": 5,
            "filters_from": "filters",
            "enable_reranker": "false",
        }
        result = self.node.execute(state, config)
        assert "context" in result

    def test_missing_index_returns_error(self):
        state = {"input": "test query", "messages": []}
        config = {
            "_writes_to": "context",
            "_target_field": None,
            "query_from": "input",
            "index_name": "nonexistent.catalog.index",
            "endpoint_name": "nonexistent-endpoint",
            "columns": "text",
            "num_results": 1,
            "enable_reranker": "false",
        }
        result = self.node.execute(state, config)
        assert "context" in result
        # Should contain an error message, not raise an exception
        assert "error" in result["context"].lower()
