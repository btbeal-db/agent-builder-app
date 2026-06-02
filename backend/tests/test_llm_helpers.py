"""Unit tests for LLM node helper functions — pure logic, no LLM calls."""

import json

from backend.nodes.llm_node import (
    _resolve_templates,
    _build_schema_instruction,
    build_pydantic_model,
    extract_visible_text,
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

    def test_dotted_path_into_structured_field(self):
        verdict = json.dumps({"is_funny": False, "reasoning": "groan"})
        result = _resolve_templates("Critique: {verdict.reasoning}", {"verdict": verdict})
        assert result == "Critique: groan"

    def test_dotted_path_unknown_subfield(self):
        verdict = json.dumps({"is_funny": True})
        result = _resolve_templates("{verdict.missing}", {"verdict": verdict})
        assert result == "{verdict.missing}"


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


class TestExtractVisibleText:
    def test_plain_string_passthrough(self):
        assert extract_visible_text("Hello world") == "Hello world"

    def test_empty_string(self):
        assert extract_visible_text("") == ""

    def test_list_of_blocks(self):
        blocks = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
            {"type": "text", "text": "Answer"},
        ]
        assert extract_visible_text(blocks) == "Answer"

    def test_json_encoded_bundled_chunk(self):
        # gpt-oss non-streaming: full reasoning + text in one JSON list
        s = json.dumps([
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "x"}]},
            {"type": "text", "text": "Answer"},
        ])
        assert extract_visible_text(s) == "Answer"

    def test_concatenated_streaming_chunks(self):
        # gpt-oss streaming accumulation: reasoning literals concatenated,
        # then plain text chunks appended.
        accumulated = (
            '[{"type": "reasoning", "summary": [{"type": "summary_text", "text": "a"}]}]'
            '[{"type": "reasoning", "summary": [{"type": "summary_text", "text": "b"}]}]'
            'Subject: Repair RequestBody continues...'
        )
        assert extract_visible_text(accumulated) == "Subject: Repair RequestBody continues..."

    def test_isolated_reasoning_chunk_returns_empty(self):
        # LangGraph 1.x yields one JSON literal per stream chunk for gpt-oss.
        # A reasoning-only chunk must return "" so the caller can skip it,
        # otherwise raw harmony JSON leaks into the response.
        single = '[{"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]}]'
        assert extract_visible_text(single) == ""

    def test_text_after_text_blocks_preserved(self):
        # Some chunks bundle reasoning + a partial text block, then stream
        # additional plain text.
        accumulated = (
            '[{"type": "text", "text": "Hello "}]'
            'world!'
        )
        assert extract_visible_text(accumulated) == "Hello world!"

    def test_unrelated_bracket_string(self):
        # Plain text that happens to start with [ but isn't JSON.
        assert extract_visible_text("[draft] Hello") == "[draft] Hello"

    def test_non_harmony_json_list(self):
        # Valid JSON list of non-block dicts → no text extracted, return original.
        s = '[1, 2, 3]'
        assert extract_visible_text(s) == s
