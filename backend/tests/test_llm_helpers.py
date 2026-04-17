"""Unit tests for LLM node helper functions — pure logic, no LLM calls."""

from backend.nodes.llm_node import (
    _resolve_templates,
    _build_state_context,
    _build_schema_instruction,
    build_pydantic_model,
)


class TestResolveTemplates:
    def test_single_replacement(self):
        result = _resolve_templates("Hello {name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_multiple_replacements(self):
        result = _resolve_templates("{a} and {b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_no_placeholders(self):
        result = _resolve_templates("No vars here.", {"x": "y"})
        assert result == "No vars here."

    def test_skips_messages_field(self):
        result = _resolve_templates("{messages}", {"messages": "should not appear"})
        assert result == "{messages}"

    def test_missing_var_unchanged(self):
        result = _resolve_templates("Hello {missing}!", {"other": "val"})
        assert result == "Hello {missing}!"


class TestBuildStateContext:
    def test_formats_fields(self):
        state = {"input": "hello", "context": "some docs", "output": ""}
        result = _build_state_context(state)
        assert "input: hello" in result
        assert "context: some docs" in result
        assert "output" not in result  # empty fields excluded

    def test_skips_messages(self):
        state = {"input": "hello", "messages": [{"role": "user"}]}
        result = _build_state_context(state)
        assert "messages" not in result

    def test_empty_state(self):
        result = _build_state_context({})
        assert result == ""


class TestBuildSchemaInstruction:
    def test_basic_schema(self):
        sub_fields = [
            {"name": "score", "type": "int", "description": "Rating 1-10"},
            {"name": "reasoning", "type": "str", "description": "Why"},
        ]
        result = _build_schema_instruction(sub_fields, "verdict")
        assert "verdict" in result
        assert "score (int): Rating 1-10" in result
        assert "reasoning (str): Why" in result

    def test_empty_sub_fields(self):
        result = _build_schema_instruction([], "output")
        assert "output" in result


class TestBuildPydanticModel:
    def test_creates_model(self):
        sub_fields = [
            {"name": "score", "type": "int", "description": "Rating"},
            {"name": "text", "type": "str", "description": "Comment"},
        ]
        model = build_pydantic_model(sub_fields, "Verdict")
        assert model is not None
        assert "score" in model.model_fields
        assert "text" in model.model_fields

    def test_empty_sub_fields_returns_none(self):
        assert build_pydantic_model([], "Empty") is None

    def test_blank_name_skipped(self):
        sub_fields = [
            {"name": "", "type": "str", "description": ""},
            {"name": "valid", "type": "str", "description": "ok"},
        ]
        model = build_pydantic_model(sub_fields, "Test")
        assert model is not None
        assert "valid" in model.model_fields
        assert len(model.model_fields) == 1

    def test_model_validates_data(self):
        sub_fields = [
            {"name": "is_funny", "type": "bool", "description": ""},
            {"name": "reasoning", "type": "str", "description": ""},
        ]
        model = build_pydantic_model(sub_fields, "Verdict")
        instance = model(is_funny=True, reasoning="clever")
        assert instance.is_funny is True
        assert instance.reasoning == "clever"
