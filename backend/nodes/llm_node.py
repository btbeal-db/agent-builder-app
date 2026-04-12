from __future__ import annotations

import json
from typing import Any

from pydantic import Field, create_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from databricks_langchain import ChatDatabricks

from .base import BaseNode, NodeConfigField
from . import register

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list[str]": list[str],
    "list[int]": list[int],
    "list[float]": list[float],
}


def build_pydantic_model(sub_fields: list[dict[str, str]], model_name: str = "StructuredOutput") -> type | None:
    """Build a Pydantic model from sub-field definitions.

    Works for both structured types (multiple sub-fields) and primitive
    types wrapped as a single-field model (e.g. bool, int, float).
    """
    if not sub_fields:
        return None

    field_definitions: dict[str, Any] = {}
    for f in sub_fields:
        name = f.get("name", "").strip()
        type_str = f.get("type", "str").strip()
        desc = f.get("description", "")
        if not name:
            continue
        py_type = _TYPE_MAP.get(type_str, str)
        field_definitions[name] = (py_type, Field(description=desc))

    if not field_definitions:
        return None

    return create_model(model_name, **field_definitions)


def _resolve_templates(template: str, state: dict[str, Any]) -> str:
    """Replace {field_name} placeholders in the template with state values."""
    result = template
    for key, val in state.items():
        if key in ("messages", "_writes_to", "_target_field"):
            continue
        result = result.replace(f"{{{key}}}", str(val))
    return result


def _build_state_context(state: dict[str, Any]) -> str:
    """Format non-empty state fields as context for the LLM prompt."""
    parts = []
    for key, val in state.items():
        if key in ("messages", "_writes_to", "_target_field") or not val:
            continue
        parts.append(f"{key}: {val}")
    return "\n".join(parts)


def _format_conversation_history(state: dict[str, Any], last_n: int = 0) -> str:
    """Format prior user/assistant turns as a readable text block.

    Args:
        state: The full agent state (messages may be BaseMessage or dict).
        last_n: Number of recent messages to include. 0 means all.

    Returns:
        A formatted string like:
            User: hello
            Assistant: Hi there!
    """
    _TYPE_TO_ROLE = {"human": "User", "ai": "Assistant"}
    turns: list[str] = []
    for msg in state.get("messages", []):
        if isinstance(msg, BaseMessage):
            role = _TYPE_TO_ROLE.get(msg.type)
            if role:
                turns.append(f"{role}: {msg.content}")
        elif isinstance(msg, dict):
            role = msg.get("role", "")
            if role in ("user", "assistant"):
                label = "User" if role == "user" else "Assistant"
                turns.append(f"{label}: {msg.get('content', '')}")

    if last_n and last_n > 0:
        turns = turns[-last_n:]

    return "\n".join(turns)


def _build_schema_instruction(sub_fields: list[dict[str, str]], field_name: str) -> str:
    """Build a prompt section describing the expected structured output."""
    lines = [f"You must respond with a structured `{field_name}` object containing:"]
    for f in sub_fields:
        name = f.get("name", "")
        type_str = f.get("type", "str")
        desc = f.get("description", "")
        if not name:
            continue
        lines.append(f"- {name} ({type_str}): {desc}" if desc else f"- {name} ({type_str})")
    return "\n".join(lines)


@register
class LLMNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "llm"

    @property
    def display_name(self) -> str:
        return "LLM"

    @property
    def description(self) -> str:
        return "Call a Databricks Foundation Model or external LLM endpoint."

    @property
    def category(self) -> str:
        return "model"

    @property
    def icon(self) -> str:
        return "brain"

    @property
    def color(self) -> str:
        return "#8b5cf6"

    @property
    def default_field_template(self) -> dict[str, str] | None:
        return {"name": "llm_output", "type": "str", "description": "LLM response"}

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="endpoint",
                label="Serving Endpoint",
                placeholder="databricks-meta-llama-3-3-70b-instruct",
            ),
            NodeConfigField(
                name="system_prompt",
                label="System Prompt",
                field_type="textarea",
                required=False,
                default="You are a helpful assistant.",
            ),
            NodeConfigField(
                name="temperature",
                label="Temperature",
                field_type="number",
                required=False,
                default=0.7,
            ),
            NodeConfigField(
                name="include_state_variables",
                label="Include State Variables",
                field_type="select",
                required=False,
                default="true",
                options=["true", "false"],
                help_text="Pass all user-defined state variables to the LLM. When disabled, use {field_name} templates in the system prompt instead.",
            ),
            NodeConfigField(
                name="include_message_history",
                label="Include Message History",
                field_type="select",
                required=False,
                default="false",
                options=["false", "true"],
                help_text="Include prior conversation turns in the prompt for multi-turn awareness.",
            ),
            NodeConfigField(
                name="last_n_messages",
                label="Last N Messages",
                field_type="number",
                required=False,
                default=0,
                help_text="Number of recent messages to include (0 = all). Only used when Include Message History is enabled.",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        target_field = config.get("_target_field")
        endpoint = config.get("endpoint", "databricks-meta-llama-3-3-70b-instruct")
        temperature = float(config.get("temperature", 0.7))
        raw_prompt = config.get("system_prompt", "You are a helpful assistant.")
        include_state_vars = str(config.get("include_state_variables", "true")).lower() == "true"
        include_msg_history = str(config.get("include_message_history",
                                             config.get("conversational", "false"))).lower() == "true"
        last_n = int(config.get("last_n_messages", 0) or 0)

        # Resolve {field_name} templates in the system prompt from state
        system_prompt = _resolve_templates(raw_prompt, state)

        # Either pass all state fields automatically, or rely on templates only
        state_context = _build_state_context(state) if include_state_vars else ""

        # Optionally append conversation history to the context
        if include_msg_history:
            history_text = _format_conversation_history(state, last_n=last_n)
            if history_text:
                state_context = f"{state_context}\n\nConversation History:\n{history_text}" if state_context else f"Conversation History:\n{history_text}"

        # LLM calls use the SP credentials (default env vars). FMAPI's data-plane
        # does not accept OBO tokens. Data-access nodes (VS, Genie, UC) use OBO.
        llm = ChatDatabricks(endpoint=endpoint, temperature=temperature)

        # Bind tools if configured
        tools_json_raw = config.get("tools_json", "")
        tools = []
        if tools_json_raw and str(tools_json_raw).strip():
            try:
                from ..tools import make_tools_from_json
                tools = make_tools_from_json(str(tools_json_raw))
                llm = llm.bind_tools(tools)
            except Exception as exc:
                return {
                    writes_to: f"Error binding tools: {exc}",
                    "messages": [{"role": "system", "content": f"LLM: tool binding error: {exc}", "node": "llm"}],
                }

        # Auto-detect structured output from the state field definition.
        # Covers both multi-field structured types and primitive types
        # (bool, int, float) that need typed extraction via with_structured_output.
        target_type = getattr(target_field, "type", "str") if target_field else "str"

        if target_type == "structured":
            sub_fields = getattr(target_field, "sub_fields", [])
        elif target_type in _TYPE_MAP and target_type != "str":
            # Wrap primitive as a single-field structured output
            sub_fields = [{"name": writes_to, "type": target_type, "description": getattr(target_field, "description", "") or writes_to}]
        else:
            sub_fields = []

        if sub_fields and writes_to:
            model_cls = build_pydantic_model(sub_fields, model_name=writes_to.title().replace("_", ""))
            if model_cls is None:
                return {
                    writes_to: "Error: structured field has no valid sub-fields",
                    "messages": [{"role": "system", "content": "LLM: invalid structured field schema.", "node": "llm"}],
                }

            schema_instruction = _build_schema_instruction(sub_fields, writes_to)
            system_prompt = f"{system_prompt}\n\n{schema_instruction}"

            structured_llm = llm.with_structured_output(model_cls)
            result = structured_llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=state_context or state.get("input", "")),
            ])

            result_dict = result.model_dump()
            # Single-field primitives: extract the value directly
            if target_type != "structured":
                response_text = str(result_dict[writes_to])
            else:
                response_text = json.dumps(result_dict, indent=2)

            return {
                writes_to: response_text,
                "messages": [
                    {"role": "assistant", "content": response_text, "node": "llm"},
                ],
            }

        # Plain text / tool-calling path
        max_iterations = int(config.get("max_tool_iterations", 10) or 10)
        messages_for_llm = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state_context or state.get("input", "")),
        ]
        trace_messages: list[dict[str, Any]] = []

        for _iteration in range(max_iterations):
            response = llm.invoke(messages_for_llm)

            # If no tools or no tool calls, we have our final answer
            if not tools or not hasattr(response, "tool_calls") or not response.tool_calls:
                response_text = response.content
                trace_messages.append({"role": "assistant", "content": response_text, "node": "llm"})
                return {
                    writes_to: response_text,
                    "messages": trace_messages,
                }

            # LLM wants to call tools — execute them and loop
            messages_for_llm.append(response)
            trace_messages.append({
                "role": "assistant",
                "content": f"[Tool calls: {', '.join(tc['name'] for tc in response.tool_calls)}]",
                "node": "llm",
            })

            tool_map = {t.name: t for t in tools}
            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name", "")
                tool_args = tool_call.get("args", {})
                tool_id = tool_call.get("id", "")

                tool_fn = tool_map.get(tool_name)
                if not tool_fn:
                    result_str = f"Unknown tool: {tool_name}"
                else:
                    try:
                        result = tool_fn.invoke(tool_args)
                        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
                    except Exception as exc:
                        result_str = f"Error calling {tool_name}: {exc}"

                from langchain_core.messages import ToolMessage
                messages_for_llm.append(ToolMessage(content=result_str, tool_call_id=tool_id))
                trace_messages.append({
                    "role": "system",
                    "content": f"[{tool_name}] {result_str}",
                    "node": "tool",
                })

        # Max iterations reached — return whatever we have
        response_text = "(max tool iterations reached)"
        trace_messages.append({"role": "assistant", "content": response_text, "node": "llm"})
        return {
            writes_to: response_text,
            "messages": trace_messages,
        }
