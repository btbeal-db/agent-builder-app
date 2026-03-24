"""Integration tests for LLM node — real FMAPI calls."""

from __future__ import annotations

import json

import pytest

from backend.nodes.llm_node import LLMNode

pytestmark = pytest.mark.integration


class TestLLMNodeIntegration:
    def setup_method(self):
        self.node = LLMNode()

    def test_simple_prompt(self, llm_endpoint):
        state = {"input": "Say hello in one word.", "messages": []}
        config = {
            "_writes_to": "output",
            "_target_field": None,
            "endpoint": llm_endpoint,
            "system_prompt": "You are a helpful assistant. Be very brief.",
            "temperature": 0.1,
        }
        result = self.node.execute(state, config)
        assert "output" in result
        assert len(result["output"]) > 0
        assert "messages" in result

    def test_template_resolution(self, llm_endpoint):
        state = {"input": "cats", "topic": "animals", "messages": []}
        config = {
            "_writes_to": "output",
            "_target_field": None,
            "endpoint": llm_endpoint,
            "system_prompt": "You are an expert on {topic}. Answer in one sentence.",
            "temperature": 0.1,
        }
        result = self.node.execute(state, config)
        assert "output" in result
        assert len(result["output"]) > 0

    def test_structured_output(self, llm_endpoint):
        from backend.schema import StateFieldDef

        target_field = StateFieldDef(
            name="verdict",
            type="structured",
            description="Judgment",
            sub_fields=[
                {"name": "is_funny", "type": "bool", "description": "Is it funny?"},
                {"name": "reasoning", "type": "str", "description": "Why or why not"},
            ],
        )
        state = {"input": "Why did the chicken cross the road? To get to the other side.", "messages": []}
        config = {
            "_writes_to": "verdict",
            "_target_field": target_field,
            "endpoint": llm_endpoint,
            "system_prompt": "Judge whether the joke is funny.",
            "temperature": 0.1,
        }
        result = self.node.execute(state, config)
        assert "verdict" in result
        # Should be valid JSON
        parsed = json.loads(result["verdict"])
        assert "is_funny" in parsed
        assert "reasoning" in parsed
