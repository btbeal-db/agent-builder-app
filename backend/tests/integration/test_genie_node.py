"""Integration tests for Genie node — real Genie space queries."""

from __future__ import annotations

import pytest

from backend.nodes.genie_node import GenieNode

pytestmark = pytest.mark.integration


class TestGenieNodeIntegration:
    def setup_method(self):
        self.node = GenieNode()

    def test_valid_query(self, genie_room_id):
        state = {"input": "What is the average cost per department?", "messages": []}
        config = {
            "_writes_to": "genie_response",
            "_target_field": None,
            "question_from": "input",
            "room_id": genie_room_id,
        }
        result = self.node.execute(state, config)
        assert "genie_response" in result
        # Should have actual content
        content = result["genie_response"]
        assert len(content) > 0
        assert "error" not in content.lower() or "no content" not in content.lower()

    def test_invalid_room_id(self):
        state = {"input": "test question", "messages": []}
        config = {
            "_writes_to": "genie_response",
            "_target_field": None,
            "question_from": "input",
            "room_id": "00000000000000000000000000000000",
        }
        result = self.node.execute(state, config)
        assert "genie_response" in result
        # Should contain an error, not raise
        assert "error" in result["genie_response"].lower()

    def test_empty_room_id(self):
        state = {"input": "test", "messages": []}
        config = {
            "_writes_to": "genie_response",
            "_target_field": None,
            "question_from": "input",
            "room_id": "",
        }
        result = self.node.execute(state, config)
        assert "no Genie Room ID" in result["genie_response"]
