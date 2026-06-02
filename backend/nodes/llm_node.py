from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import Field, ValidationError, create_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from databricks_langchain import ChatDatabricks

from .base import BaseNode, NodeConfigField
from . import register

logger = logging.getLogger(__name__)


def _text_from_blocks(blocks: list) -> str:
    return "".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _looks_like_harmony(obj: Any) -> bool:
    """True if ``obj`` is a list whose first dict has a harmony ``type``."""
    if not isinstance(obj, list) or not obj:
        return False
    first = obj[0]
    return isinstance(first, dict) and first.get("type") in {"reasoning", "text"}


def extract_visible_text(content: Any) -> str:
    """Strip gpt-oss harmony reasoning blocks; return only user-facing text.

    databricks_langchain's _convert_dict_to_message stringifies non-string
    content via ``json.dumps``, which destroys the typed-block shape that
    AIMessage.text would normally use. This helper reverses that: parses
    leading JSON list literals and keeps only ``type=="text"`` blocks.

    Content shapes seen in practice:
    - Plain string (Claude, Llama): pass through.
    - List of typed blocks: keep ``type=="text"`` blocks.
    - JSON-encoded list-of-blocks (single bundled chunk): same as above after parse.
    - One harmony JSON literal per stream chunk (LangGraph 1.x): a single
      ``[{...reasoning...}]`` literal arrives per chunk. After stripping we
      return ``""`` so the caller can skip the chunk entirely.
    - Concatenated literals + trailing text (older accumulation behavior):
      walk leading literals, skip reasoning blocks, keep trailing plain text.
    """
    if isinstance(content, list):
        return _text_from_blocks(content)
    if not isinstance(content, str):
        return str(content)
    stripped = content.lstrip()
    if not stripped.startswith("["):
        return content
    decoder = json.JSONDecoder()
    text_parts: list[str] = []
    saw_harmony = False
    remaining = stripped
    while remaining.startswith("["):
        try:
            obj, end = decoder.raw_decode(remaining)
        except (json.JSONDecodeError, ValueError):
            break
        if _looks_like_harmony(obj):
            saw_harmony = True
            text_parts.append(_text_from_blocks(obj))
            remaining = remaining[end:].lstrip()
        else:
            break
    if remaining:
        text_parts.append(remaining)
    return "".join(text_parts) if saw_harmony else content


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
    """Build a Pydantic model from sub-field definitions."""
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


_TEMPLATE_SKIP_KEYS = {"messages", "_writes_to", "_target_field"}


def _resolve_templates(template: str, state: dict[str, Any]) -> str:
    """Replace ``{field}`` and ``{field.sub}`` placeholders with state values.

    Sub-paths (e.g. ``{verdict.reasoning}``) resolve against fields whose
    value is a JSON-encoded structured output. Unknown placeholders are left
    in place so the user notices the typo.
    """
    for key, val in state.items():
        if key in _TEMPLATE_SKIP_KEYS:
            continue
        template = template.replace(f"{{{key}}}", str(val))
        if isinstance(val, str) and val.strip().startswith("{"):
            try:
                parsed = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                for sub_key, sub_val in parsed.items():
                    template = template.replace(f"{{{key}.{sub_key}}}", str(sub_val))
    return template


def _get_message_history(state: dict[str, Any], last_n: int = 0) -> list[BaseMessage]:
    """Extract user/assistant message objects from state for the LLM context.

    Filters to only HumanMessage and AIMessage — skips system, tool, and
    any non-standard messages that other nodes may have added.
    """
    messages: list[BaseMessage] = []
    for msg in state.get("messages", []):
        if isinstance(msg, (HumanMessage, AIMessage)):
            messages.append(msg)
        elif isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    if last_n and last_n > 0:
        messages = messages[-last_n:]

    return messages


_INNER_MESSAGE_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _humanize_llm_error(exc: Exception, endpoint: str) -> str:
    """Pull a human-readable message out of an FMAPI / OpenAI-style error.

    These errors arrive doubly nested — a Python dict repr wrapping a JSON
    string whose ``error.message`` is the actual user-facing text. The default
    ``str(exc)`` dumps all of it. Surface only the inner message, and append
    an actionable tip when we recognize the temperature-rejection case.
    """
    raw = str(exc)
    matches = _INNER_MESSAGE_RE.findall(raw)
    if not matches:
        return raw
    inner = matches[-1].replace("\\'", "'").replace('\\"', '"').replace('\\n', ' ').strip()
    lower = inner.lower()
    if "temperature" in lower and ("does not support" in lower or "unsupported" in lower):
        return (
            f"You configured a Temperature value, but this model does not support that parameter.\n\n"
            f"Tip: clear the Temperature field on the LLM node — `{endpoint}` uses its built-in default."
        )
    return inner


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
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="endpoint",
                label="Serving Endpoint",
                field_type="searchable_select",
                fetch_endpoint="/api/discover/serving-endpoints",
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
                default=None,
                help_text="Leave blank to use the endpoint's default. Reasoning models (e.g. Claude Opus 4) reject this parameter.",
                advanced=True,
            ),
            NodeConfigField(
                name="conversational",
                label="Conversational",
                field_type="select",
                required=False,
                default="false",
                options=["false", "true"],
                help_text="Multi-turn awareness — pass prior user/assistant messages to the LLM each turn.",
            ),
            NodeConfigField(
                name="last_n_messages",
                label="Last N Messages",
                field_type="number",
                required=False,
                default=0,
                help_text="Cap on prior messages to include (0 = all). Only used when Conversational is enabled.",
                advanced=True,
            ),
            NodeConfigField(
                name="output_schema",
                label="Structured Output",
                field_type="schema_editor",
                required=False,
                default=[],
                help_text="Define structured output fields. Leave empty for plain text output.",
                advanced=True,
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        endpoint = config.get("endpoint", "databricks-meta-llama-3-3-70b-instruct")
        raw_prompt = config.get("system_prompt", "You are a helpful assistant.")
        conversational = str(config.get("conversational", "false")).lower() == "true"
        last_n = int(config.get("last_n_messages", 0) or 0)

        # Resolve {field} / {field.sub} placeholders against current state.
        system_prompt = _resolve_templates(raw_prompt, state)

        # LLM calls use the SP credentials (default env vars). FMAPI's data-plane
        # does not accept OBO tokens. Data-access nodes (VS, Genie, UC) use OBO.
        # ``temperature`` is opt-in: only forward it when the user explicitly
        # set a parsable value. Reasoning endpoints (e.g. Claude Opus 4) reject
        # the parameter — leaving it unset lets ChatDatabricks omit it from the
        # request payload entirely.
        llm_kwargs: dict[str, Any] = {"endpoint": endpoint}
        raw_temp = config.get("temperature")
        if raw_temp not in (None, "", "null"):
            try:
                llm_kwargs["temperature"] = float(raw_temp)
            except (TypeError, ValueError):
                pass
        llm = ChatDatabricks(**llm_kwargs)

        # Bind tools if configured
        tools_json_raw = config.get("tools_json", "")
        tools = []
        if tools_json_raw and str(tools_json_raw).strip():
            try:
                from ..tools import make_tools_from_json
                tools = make_tools_from_json(str(tools_json_raw))
                if not tools:
                    logger.error(
                        "tools_json was configured but no tools were created — "
                        "the LLM will proceed without tools. tools_json=%s",
                        str(tools_json_raw)[:200],
                    )
                else:
                    logger.info("Bound %d tools: %s", len(tools), [t.name for t in tools])
                    llm = llm.bind_tools(tools)
            except Exception as exc:
                return {writes_to: f"Error binding tools: {exc}"}

        # Structured output is configured directly on the node. Empty schema = plain text.
        raw_schema = config.get("output_schema", [])
        if isinstance(raw_schema, str):
            try:
                raw_schema = json.loads(raw_schema)
            except (json.JSONDecodeError, ValueError):
                raw_schema = []
        sub_fields = [f for f in raw_schema if isinstance(f, dict) and f.get("name")] if isinstance(raw_schema, list) else []

        # In conversational mode, pass prior user/assistant turns as real
        # message objects. Otherwise treat each invocation as a single turn.
        if conversational:
            messages_for_llm = [SystemMessage(content=system_prompt)] + _get_message_history(state, last_n=last_n)
        else:
            messages_for_llm = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=state.get("input", "")),
            ]

        # Structured output path
        if sub_fields and writes_to:
            model_cls = build_pydantic_model(sub_fields, model_name=writes_to.title().replace("_", ""))
            if model_cls is None:
                return {writes_to: "Error: structured field has no valid sub-fields"}

            schema_instruction = _build_schema_instruction(sub_fields, writes_to)
            messages_for_llm[0] = SystemMessage(
                content=f"{system_prompt}\n\n{schema_instruction}"
            )

            # Use FMAPI's server-side strict json_schema response_format. The
            # default with_structured_output(method="function_calling") path
            # forces a tool call that some endpoints (e.g. databricks-gpt-oss-*)
            # don't reliably emit. databricks-langchain's built-in
            # method="json_schema" omits the required ``name`` field, so we
            # build the payload manually here.
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": model_cls.__name__,
                    "strict": True,
                    "schema": model_cls.model_json_schema(),
                },
            }
            # FMAPI rejects structured output + streaming for some endpoints
            # (gpt-oss-* explicitly errors with "Structured output is not
            # currently supported with streaming"). LangGraph drives nodes
            # in a streaming context during preview, which forces .invoke()
            # to stream unless we pin disable_streaming on the model.
            non_streaming = ChatDatabricks(**llm_kwargs, disable_streaming=True)
            try:
                ai_msg = non_streaming.bind(response_format=response_format).invoke(messages_for_llm)
            except Exception as exc:
                raise RuntimeError(_humanize_llm_error(exc, endpoint)) from exc
            visible = extract_visible_text(getattr(ai_msg, "content", "") or "")
            try:
                result_dict = model_cls(**json.loads(visible)).model_dump()
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                err = (
                    f"Endpoint '{endpoint}' returned no parseable structured output: {exc}. "
                    "The model did not return JSON matching the requested schema; "
                    "try a different endpoint."
                )
                return {writes_to: err, "messages": [AIMessage(content=err)]}

            response_text = json.dumps(result_dict, indent=2)

            return {
                writes_to: response_text,
                "messages": [AIMessage(content=response_text)],
            }

        # Plain text / tool-calling path — loop stays local, only
        # the final AIMessage is returned to state.
        max_iterations = int(config.get("max_tool_iterations", 10) or 10)

        for _ in range(max_iterations):
            try:
                response = llm.invoke(messages_for_llm)
            except Exception as exc:
                raise RuntimeError(_humanize_llm_error(exc, endpoint)) from exc

            if not tools or not hasattr(response, "tool_calls") or not response.tool_calls:
                visible = extract_visible_text(response.content)
                return {
                    writes_to: visible,
                    "messages": [AIMessage(content=visible)],
                }

            # Tool-calling loop — intermediates stay local
            messages_for_llm.append(response)
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

                messages_for_llm.append(ToolMessage(content=result_str, tool_call_id=tool_id))

        return {
            writes_to: "(max tool iterations reached)",
            "messages": [AIMessage(content="(max tool iterations reached)")],
        }
