from __future__ import annotations

import json
from typing import Any

from .base import BaseNode, NodeConfigField
from . import register


def _parse_routes(config: dict[str, Any]) -> list[dict[str, str]]:
    raw = config.get("routes_json", "[]")
    if isinstance(raw, list):
        return raw
    try:
        routes = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(routes, list):
        return []
    return routes


def _resolve_value(state: dict[str, Any], eval_var: str, sub_field: str) -> Any:
    """Get the value to route on, resolving into structured sub-fields."""
    raw = state.get(eval_var, "")

    if not sub_field:
        return raw

    # Structured field stored as JSON string — parse and extract sub-field
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get(sub_field, "")
        except (json.JSONDecodeError, TypeError):
            pass
    elif isinstance(raw, dict):
        return raw.get(sub_field, "")

    return ""


@register
class RouterNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "router"

    @property
    def display_name(self) -> str:
        return "Router"

    @property
    def description(self) -> str:
        return "Conditional branch — route based on a state field's value."

    @property
    def category(self) -> str:
        return "control"

    @property
    def icon(self) -> str:
        return "git-branch"

    @property
    def color(self) -> str:
        return "#ef4444"

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="evaluates",
                label="Evaluates",
                field_type="state_variable",
                default="user_input",
            ),
            NodeConfigField(
                name="routes_json",
                label="Routes",
                field_type="route_editor",
                required=True,
                default='[{"label": "default", "match_value": ""}]',
            ),
        ]

    @property
    def is_router(self) -> bool:
        return True

    def get_route_names(self, config: dict[str, Any]) -> list[str]:
        """Return handle IDs (match_value for matched routes, label for fallback)."""
        return [
            r.get("match_value") or r.get("label", "default")
            for r in _parse_routes(config)
            if r.get("label") or r.get("match_value")
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        eval_var = config.get("evaluates", "user_input")
        sub_field = config.get("_route_sub_field", "")
        value = _resolve_value(state, eval_var, sub_field)
        routes = _parse_routes(config)

        if not routes:
            return {
                "_route": "default",
                "messages": [{"role": "system", "content": "Router: no routes defined.", "node": "router"}],
            }

        value_str = str(value).strip().lower()
        field_label = f"{eval_var}{'.' + sub_field if sub_field else ''}"

        # Check all routes with a match_value; last route without one is fallback
        chosen_label = None
        chosen_key = None
        match_reason = "fallback"

        for route in routes:
            match_value = route.get("match_value", "").strip().lower()

            if not match_value:
                # No match_value = fallback; only use if nothing else matched
                if chosen_label is None:
                    chosen_label = route.get("label", "default")
                    chosen_key = chosen_label
                continue

            # Bool match
            if value_str in ("true", "false") and value_str == match_value:
                chosen_label = route["label"]
                chosen_key = route.get("match_value") or chosen_label
                match_reason = f"{field_label} = {value_str}"
                break

            # Keyword match
            keywords = [kw.strip() for kw in match_value.split(",") if kw.strip()]
            if any(kw in value_str for kw in keywords):
                chosen_label = route["label"]
                chosen_key = route.get("match_value") or chosen_label
                match_reason = f"keyword match in {field_label}"
                break

        # If nothing matched and no fallback was found, use last route
        if chosen_label is None:
            chosen_label = routes[-1].get("label", "default")
            chosen_key = routes[-1].get("match_value") or chosen_label

        return {
            "_route": chosen_key,
            "messages": [{
                "role": "system",
                "content": f"Router → {chosen_label} ({match_reason})",
                "node": "router",
            }],
        }
