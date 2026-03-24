"""Shared fixtures for the Agent Builder test suite."""

from __future__ import annotations

import pytest

from backend.schema import GraphDef, StateFieldDef, NodeDef, EdgeDef


# ── Graph fixtures ────────────────────────────────────────────────────────────


def _make_graph(
    nodes: list[dict],
    edges: list[dict],
    state_fields: list[dict] | None = None,
) -> GraphDef:
    """Build a GraphDef from plain dicts for convenience."""
    sf = state_fields or [
        {"name": "input", "type": "str", "description": "The initial input", "sub_fields": []},
    ]
    return GraphDef(
        nodes=[NodeDef(**n) for n in nodes],
        edges=[EdgeDef(**e) for e in edges],
        state_fields=[StateFieldDef(**f) for f in sf],
    )


@pytest.fixture
def make_graph():
    """Factory fixture for building GraphDef from dicts."""
    return _make_graph


@pytest.fixture
def simple_graph_def() -> GraphDef:
    """Minimal START → LLM → END graph."""
    return _make_graph(
        nodes=[
            {
                "id": "llm_1",
                "type": "llm",
                "writes_to": "output",
                "config": {
                    "endpoint": "databricks-claude-sonnet-4-6",
                    "system_prompt": "You are a helpful assistant.",
                    "temperature": 0.7,
                },
            },
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "llm_1"},
            {"id": "e2", "source": "llm_1", "target": "__end__"},
        ],
        state_fields=[
            {"name": "input", "type": "str", "description": "User input", "sub_fields": []},
            {"name": "output", "type": "str", "description": "LLM response", "sub_fields": []},
        ],
    )


@pytest.fixture
def rag_graph_def() -> GraphDef:
    """START → VectorSearch → LLM → END graph."""
    return _make_graph(
        nodes=[
            {
                "id": "vs_1",
                "type": "vector_search",
                "writes_to": "context",
                "config": {
                    "query_from": "input",
                    "index_name": "catalog.schema.test_index",
                    "endpoint_name": "test-vs-endpoint",
                    "columns": "text,id",
                    "num_results": 3,
                },
            },
            {
                "id": "llm_1",
                "type": "llm",
                "writes_to": "output",
                "config": {
                    "endpoint": "databricks-claude-sonnet-4-6",
                    "system_prompt": "Answer based on: {context}",
                    "temperature": 0.3,
                },
            },
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "vs_1"},
            {"id": "e2", "source": "vs_1", "target": "llm_1"},
            {"id": "e3", "source": "llm_1", "target": "__end__"},
        ],
        state_fields=[
            {"name": "input", "type": "str", "description": "User input", "sub_fields": []},
            {"name": "context", "type": "str", "description": "Retrieved docs", "sub_fields": []},
            {"name": "output", "type": "str", "description": "LLM response", "sub_fields": []},
        ],
    )


@pytest.fixture
def router_graph_def() -> GraphDef:
    """START → LLM → Router → (branch_a / branch_b) → END."""
    return _make_graph(
        nodes=[
            {
                "id": "llm_1",
                "type": "llm",
                "writes_to": "category",
                "config": {
                    "endpoint": "databricks-claude-sonnet-4-6",
                    "system_prompt": "Classify as 'positive' or 'negative'.",
                },
            },
            {
                "id": "router_1",
                "type": "router",
                "writes_to": "",
                "config": {
                    "evaluates": "category",
                    "routes_json": [
                        {"label": "Positive", "match_value": "positive"},
                        {"label": "Negative", "match_value": "negative"},
                        {"label": "default", "match_value": ""},
                    ],
                },
            },
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "llm_1"},
            {"id": "e2", "source": "llm_1", "target": "router_1"},
            {"id": "e3", "source": "router_1", "target": "__end__", "source_handle": "positive"},
            {"id": "e4", "source": "router_1", "target": "__end__", "source_handle": "negative"},
            {"id": "e5", "source": "router_1", "target": "__end__", "source_handle": "default"},
        ],
        state_fields=[
            {"name": "input", "type": "str", "description": "User input", "sub_fields": []},
            {"name": "category", "type": "str", "description": "Classification", "sub_fields": []},
        ],
    )
