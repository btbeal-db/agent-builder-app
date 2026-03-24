"""Unit tests for Pydantic schema models."""

from backend.schema import GraphDef, StateFieldDef, NodeDef, EdgeDef, PreviewResponse


class TestStateFieldDef:
    def test_defaults(self):
        f = StateFieldDef(name="x")
        assert f.type == "str"
        assert f.description == ""
        assert f.sub_fields == []

    def test_structured_sub_fields(self):
        f = StateFieldDef(
            name="verdict",
            type="structured",
            description="Judge output",
            sub_fields=[{"name": "score", "type": "int", "description": "1-10"}],
        )
        assert f.sub_fields[0]["name"] == "score"


class TestGraphDef:
    def test_state_variable_names(self):
        g = GraphDef(
            nodes=[],
            edges=[],
            state_fields=[
                StateFieldDef(name="input"),
                StateFieldDef(name="context"),
                StateFieldDef(name="output"),
            ],
        )
        assert g.state_variable_names == ["input", "context", "output"]

    def test_get_state_field_found(self):
        g = GraphDef(
            nodes=[],
            edges=[],
            state_fields=[StateFieldDef(name="input"), StateFieldDef(name="output")],
        )
        f = g.get_state_field("output")
        assert f is not None
        assert f.name == "output"

    def test_get_state_field_not_found(self):
        g = GraphDef(nodes=[], edges=[], state_fields=[StateFieldDef(name="input")])
        assert g.get_state_field("missing") is None

    def test_serialization_round_trip(self):
        g = GraphDef(
            nodes=[NodeDef(id="n1", type="llm", writes_to="output", config={"endpoint": "test"})],
            edges=[EdgeDef(id="e1", source="__start__", target="n1")],
            state_fields=[StateFieldDef(name="input"), StateFieldDef(name="output")],
        )
        json_str = g.model_dump_json()
        g2 = GraphDef.model_validate_json(json_str)
        assert g2.nodes[0].id == "n1"
        assert g2.edges[0].source == "__start__"
        assert len(g2.state_fields) == 2

    def test_default_state_fields(self):
        g = GraphDef(nodes=[], edges=[])
        assert len(g.state_fields) == 1
        assert g.state_fields[0].name == "input"


class TestEdgeDef:
    def test_source_handle_optional(self):
        e = EdgeDef(id="e1", source="a", target="b")
        assert e.source_handle is None

    def test_source_handle_set(self):
        e = EdgeDef(id="e1", source="a", target="b", source_handle="positive")
        assert e.source_handle == "positive"


class TestPreviewResponse:
    def test_defaults(self):
        r = PreviewResponse(success=True)
        assert r.output == ""
        assert r.error is None
        assert r.execution_trace == []
        assert r.state == {}
        assert r.mlflow_trace == []
        assert r.interrupt is None
