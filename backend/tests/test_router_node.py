"""Unit tests for the Router node — pure logic, no external calls."""

from backend.nodes.router_node import RouterNode, _parse_routes, _resolve_value


class TestParseRoutes:
    def test_list_input(self):
        routes = _parse_routes({"routes_json": [{"label": "A", "match_value": "a"}]})
        assert len(routes) == 1
        assert routes[0]["label"] == "A"

    def test_json_string_input(self):
        routes = _parse_routes({"routes_json": '[{"label": "A", "match_value": "a"}]'})
        assert len(routes) == 1

    def test_invalid_json(self):
        routes = _parse_routes({"routes_json": "not json"})
        assert routes == []

    def test_missing_key(self):
        routes = _parse_routes({})
        assert routes == []


class TestResolveValue:
    def test_simple_field(self):
        assert _resolve_value({"x": "hello"}, "x", "") == "hello"

    def test_missing_field(self):
        assert _resolve_value({}, "x", "") == ""

    def test_json_sub_field(self):
        state = {"verdict": '{"score": 8, "reasoning": "good"}'}
        assert _resolve_value(state, "verdict", "score") == 8

    def test_dict_sub_field(self):
        state = {"verdict": {"score": 8}}
        assert _resolve_value(state, "verdict", "score") == 8

    def test_invalid_json_sub_field(self):
        state = {"verdict": "not json"}
        assert _resolve_value(state, "verdict", "score") == ""


class TestRouterNodeExecute:
    def setup_method(self):
        self.node = RouterNode()

    def test_boolean_true_match(self):
        state = {"is_valid": "true"}
        config = {
            "evaluates": "is_valid",
            "routes_json": [
                {"label": "Valid", "match_value": "true"},
                {"label": "Invalid", "match_value": "false"},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "true"

    def test_boolean_false_match(self):
        state = {"is_valid": "false"}
        config = {
            "evaluates": "is_valid",
            "routes_json": [
                {"label": "Valid", "match_value": "true"},
                {"label": "Invalid", "match_value": "false"},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "false"

    def test_keyword_match(self):
        state = {"feedback": "Yes, looks great!"}
        config = {
            "evaluates": "feedback",
            "routes_json": [
                {"label": "Positive", "match_value": "yes, yep, sure"},
                {"label": "Negative", "match_value": "no, nope"},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "yes, yep, sure"

    def test_keyword_match_case_insensitive(self):
        state = {"feedback": "YES"}
        config = {
            "evaluates": "feedback",
            "routes_json": [
                {"label": "Positive", "match_value": "yes"},
                {"label": "Negative", "match_value": "no"},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "yes"

    def test_fallback_route(self):
        state = {"feedback": "maybe"}
        config = {
            "evaluates": "feedback",
            "routes_json": [
                {"label": "Positive", "match_value": "yes"},
                {"label": "Negative", "match_value": "no"},
                {"label": "default", "match_value": ""},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "default"

    def test_no_routes_returns_default(self):
        state = {"x": "anything"}
        config = {"evaluates": "x", "routes_json": []}
        result = self.node.execute(state, config)
        assert result["_route"] == "default"

    def test_structured_sub_field_routing(self):
        state = {"verdict": '{"is_funny": true, "reasoning": "clever"}'}
        config = {
            "evaluates": "verdict",
            "_route_sub_field": "is_funny",
            "routes_json": [
                {"label": "Funny", "match_value": "true"},
                {"label": "Not Funny", "match_value": "false"},
            ],
        }
        result = self.node.execute(state, config)
        assert result["_route"] == "true"

    def test_route_value_returned(self):
        state = {"x": "yes"}
        config = {
            "evaluates": "x",
            "routes_json": [{"label": "Y", "match_value": "yes"}],
        }
        result = self.node.execute(state, config)
        assert "_route" in result
        assert result["_route"] == "yes"
