from __future__ import annotations

import json
import logging
from typing import Any

from databricks_langchain import UCFunctionToolkit

from .base import BaseNode, NodeConfigField, resolve_state
from . import register

logger = logging.getLogger(__name__)


@register
class UCFunctionNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "uc_function"

    @property
    def display_name(self) -> str:
        return "UC Function"

    @property
    def description(self) -> str:
        return "Execute a Unity Catalog function with parameters from state."

    @property
    def category(self) -> str:
        return "action"

    @property
    def icon(self) -> str:
        return "function-square"

    @property
    def color(self) -> str:
        return "#8b5cf6"

    @property
    def tool_compatible(self) -> bool:
        return True

    @property
    def default_field_template(self) -> dict[str, str] | None:
        return {"name": "function_result", "type": "str", "description": "Function output"}

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="function_name",
                label="UC Function",
                placeholder="catalog.schema.function_name",
                help_text="Fully qualified name of a Unity Catalog function.",
            ),
            NodeConfigField(
                name="parameters_from",
                label="Parameters from",
                field_type="state_variable",
                required=False,
                help_text="State variable containing a JSON object of function parameters. If not set, the function is called with no arguments.",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        function_name = config.get("function_name", "")

        if not function_name:
            return {
                writes_to: "Error: no UC function configured.",
                "messages": [{"role": "system", "content": "UC Function: missing function_name.", "node": "uc_function"}],
            }

        # Resolve parameters from state
        params: dict[str, Any] = {}
        params_from = config.get("parameters_from")
        if params_from:
            raw_params = resolve_state(state, params_from)
            if raw_params:
                if isinstance(raw_params, dict):
                    params = raw_params
                elif isinstance(raw_params, str):
                    try:
                        params = json.loads(raw_params)
                    except json.JSONDecodeError:
                        logger.warning("Invalid parameters JSON from '%s': %s", params_from, raw_params)

        try:
            toolkit = UCFunctionToolkit(function_names=[function_name])
            tools = toolkit.tools
            if not tools:
                return {
                    writes_to: f"Error: function '{function_name}' not found or not accessible.",
                    "messages": [{"role": "system", "content": f"UC Function: '{function_name}' not found.", "node": "uc_function"}],
                }

            tool = tools[0]
            result = tool.invoke(params)

        except Exception as exc:
            logger.exception("UC Function execution failed")
            return {
                writes_to: f"UC Function error: {exc}",
                "messages": [{"role": "system", "content": f"UC Function error: {exc}", "node": "uc_function"}],
            }

        result_text = result if isinstance(result, str) else json.dumps(result, indent=2)

        return {
            writes_to: result_text,
            "messages": [
                {"role": "system", "content": f"[UC Function: {function_name}]\n{result_text}", "node": "uc_function"}
            ],
        }
