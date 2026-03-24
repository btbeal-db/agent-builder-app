"""Integration tests for full graph preview — end-to-end execution."""

from __future__ import annotations

import pytest
from httpx import Client, ASGITransport

from backend.main import app
from backend.schema import GraphDef, StateFieldDef, NodeDef, EdgeDef

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return Client(transport=transport, base_url="http://test")


class TestPreviewIntegration:
    def test_simple_llm_graph(self, client, llm_endpoint):
        graph = GraphDef(
            nodes=[
                NodeDef(id="llm_1", type="llm", writes_to="output", config={
                    "endpoint": llm_endpoint,
                    "system_prompt": "Reply with exactly one word.",
                    "temperature": 0.1,
                }),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="llm_1"),
                EdgeDef(id="e2", source="llm_1", target="__end__"),
            ],
            state_fields=[
                StateFieldDef(name="input"),
                StateFieldDef(name="output"),
            ],
        )
        resp = client.post("/api/graph/preview", json={
            "graph": graph.model_dump(),
            "input_message": "Hello",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["output"]) > 0

    def test_rag_graph(self, client, llm_endpoint, vs_index_name, vs_endpoint_name):
        graph = GraphDef(
            nodes=[
                NodeDef(id="vs_1", type="vector_search", writes_to="context", config={
                    "query_from": "input",
                    "index_name": vs_index_name,
                    "endpoint_name": vs_endpoint_name,
                    "columns": "note_id,department,text",
                    "num_results": 2,
                    "enable_reranker": "false",
                }),
                NodeDef(id="llm_1", type="llm", writes_to="output", config={
                    "endpoint": llm_endpoint,
                    "system_prompt": "Answer based on context: {context}",
                    "temperature": 0.1,
                }),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="vs_1"),
                EdgeDef(id="e2", source="vs_1", target="llm_1"),
                EdgeDef(id="e3", source="llm_1", target="__end__"),
            ],
            state_fields=[
                StateFieldDef(name="input"),
                StateFieldDef(name="context"),
                StateFieldDef(name="output"),
            ],
        )
        resp = client.post("/api/graph/preview", json={
            "graph": graph.model_dump(),
            "input_message": "Tell me about cardiology patients",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["output"]) > 0

    def test_multi_turn_preserves_thread(self, client, llm_endpoint):
        graph = GraphDef(
            nodes=[
                NodeDef(id="llm_1", type="llm", writes_to="output", config={
                    "endpoint": llm_endpoint,
                    "system_prompt": "You are a helpful assistant. Be brief.",
                    "temperature": 0.1,
                    "conversational": "true",
                }),
            ],
            edges=[
                EdgeDef(id="e1", source="__start__", target="llm_1"),
                EdgeDef(id="e2", source="llm_1", target="__end__"),
            ],
            state_fields=[
                StateFieldDef(name="input"),
                StateFieldDef(name="output"),
            ],
        )
        # First message
        resp1 = client.post("/api/graph/preview", json={
            "graph": graph.model_dump(),
            "input_message": "My name is Alice.",
        })
        data1 = resp1.json()
        assert data1["success"] is True
        thread_id = data1["thread_id"]
        assert thread_id is not None

        # Second message with same thread
        resp2 = client.post("/api/graph/preview", json={
            "graph": graph.model_dump(),
            "input_message": "What is my name?",
            "thread_id": thread_id,
        })
        data2 = resp2.json()
        assert data2["success"] is True
        # The LLM should remember Alice from the first turn
        assert "alice" in data2["output"].lower()
