"""Unit tests for backend/tools.py helpers — pure logic, no MCP/SDK calls."""

from backend.tools import _safe_tool_name


class TestSafeToolName:
    def test_short_name_passthrough(self):
        assert _safe_tool_name("search_index") == "search_index"

    def test_max_length_passthrough(self):
        name = "a" * 64
        assert _safe_tool_name(name) == name

    def test_long_name_truncates_with_hash(self):
        # Reproduces the medical-assistant VS index name (74 chars).
        long_name = "agentbuilder_serverless_stable_catalog__agent_builder__patient_notes_index"
        assert len(long_name) == 74
        result = _safe_tool_name(long_name)
        assert len(result) == 64
        # Trailing portion is kept (resource name is the distinctive bit).
        assert "patient_notes_index" in result
        # Hash suffix is stable for the same input.
        assert _safe_tool_name(long_name) == result

    def test_distinct_long_names_produce_distinct_hashes(self):
        a = "catalog_a__schema__" + "x" * 50
        b = "catalog_b__schema__" + "x" * 50
        assert _safe_tool_name(a) != _safe_tool_name(b)

    def test_invalid_chars_sanitized(self):
        # Dots / slashes get replaced with underscores so the result matches
        # the OpenAI tool-name regex [a-zA-Z0-9_-]{1,64}.
        assert _safe_tool_name("catalog.schema.fn") == "catalog_schema_fn"
