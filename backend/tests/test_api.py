"""Unit tests for FastAPI endpoints (validation, node listing)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app


@pytest.fixture
def client():
    """Synchronous test client using httpx."""
    from httpx import Client
    transport = ASGITransport(app=app)
    return Client(transport=transport, base_url="http://test")


class TestListNodes:
    def test_returns_list(self, client):
        resp = client.get("/api/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_node_has_required_fields(self, client):
        resp = client.get("/api/nodes")
        node = resp.json()[0]
        for field in ("type", "display_name", "description", "icon", "color", "config_fields"):
            assert field in node, f"Missing field: {field}"


class TestValidateGraph:
    def test_valid_graph(self, client, simple_graph_def):
        resp = client.post("/api/graph/validate", json=simple_graph_def.model_dump())
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_empty_graph(self, client):
        resp = client.post("/api/graph/validate", json={
            "nodes": [],
            "edges": [],
            "state_fields": [{"name": "input", "type": "str", "description": "", "sub_fields": []}],
        })
        data = resp.json()
        assert data["valid"] is False
        assert any("no nodes" in e.lower() for e in data["errors"])

    def test_missing_start_edge(self, client):
        resp = client.post("/api/graph/validate", json={
            "nodes": [{"id": "n1", "type": "llm", "writes_to": "output", "config": {}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "state_fields": [{"name": "input", "type": "str", "description": "", "sub_fields": []}],
        })
        data = resp.json()
        assert data["valid"] is False
        assert any("start" in e.lower() for e in data["errors"])

    def test_missing_end_edge(self, client):
        resp = client.post("/api/graph/validate", json={
            "nodes": [{"id": "n1", "type": "llm", "writes_to": "output", "config": {}}],
            "edges": [{"id": "e1", "source": "__start__", "target": "n1"}],
            "state_fields": [{"name": "input", "type": "str", "description": "", "sub_fields": []}],
        })
        data = resp.json()
        assert data["valid"] is False
        assert any("end" in e.lower() for e in data["errors"])

    def test_unknown_edge_target(self, client):
        resp = client.post("/api/graph/validate", json={
            "nodes": [{"id": "n1", "type": "llm", "writes_to": "output", "config": {}}],
            "edges": [
                {"id": "e1", "source": "__start__", "target": "n1"},
                {"id": "e2", "source": "n1", "target": "nonexistent"},
            ],
            "state_fields": [{"name": "input", "type": "str", "description": "", "sub_fields": []}],
        })
        data = resp.json()
        assert data["valid"] is False
        assert any("unknown" in e.lower() for e in data["errors"])
