"""AI Chat handler — converts natural language to GraphDef JSON."""

from __future__ import annotations

import json
import logging

from databricks_langchain import ChatDatabricks
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from .nodes import get_all_metadata
from .schema import GraphDef

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "databricks-gpt-5-4-mini"


class AIChatRequest(BaseModel):
    messages: list[dict[str, str]]
    current_graph: GraphDef | None = None


class AIChatResponse(BaseModel):
    message: str
    graph: GraphDef | None = None
    error: str | None = None


def _build_system_prompt(current_graph: GraphDef | None) -> str:
    """Build the system prompt with schema + node metadata baked in."""
    schema = json.dumps(GraphDef.model_json_schema(), indent=2)
    metadata = json.dumps(get_all_metadata(), indent=2)

    current_graph_section = ""
    if current_graph:
        current_graph_section = f"""

## Current Graph (user's existing canvas)
The user already has this graph on their canvas. When they ask to modify it,
build on this rather than starting from scratch:
```json
{current_graph.model_dump_json(indent=2)}
```
"""

    return f"""You are an agent-building assistant for the Agent Builder app.
Your job is to help users create LangGraph agent graphs from natural language descriptions.

## Response Format
Always respond with valid JSON in this exact format:
{{"message": "your explanation here", "graph": {{...}}}}

The "graph" field is optional — only include it when you are creating or modifying a graph.
When just chatting or asking clarifying questions, omit "graph":
{{"message": "your question or response here"}}

## GraphDef Schema
Follow this schema exactly when constructing graphs:
```json
{schema}
```

## Available Node Types
These are the only valid node types and their configurations:
```json
{metadata}
```

## Rules

### Node basics
1. Every node needs a unique `id` (use descriptive slugs like "llm-1", "retriever-1").
2. Every node needs a `type` that matches one of the available node types above.
3. Give nodes a descriptive `name` field (used as the label in the UI).
4. Each node should have a `writes_to` field indicating which state field it updates.
5. For `config` fields, use placeholder values like "your-endpoint-name" if the user
   hasn't specified actual resource names. Tell them what to fill in.

### Edge wiring — THIS IS CRITICAL
6. There MUST be at least one edge from `__start__` to a node.
7. Every path through the graph MUST eventually reach `__end__`. No dead ends.
8. Every non-router node MUST have EXACTLY ONE outgoing edge (or go to `__end__`).
   Do NOT create multiple outgoing edges from the same non-router node.
9. Only router nodes may have multiple outgoing edges. Each outgoing edge from a
   router MUST have a `source_handle` that matches the `match_value` of the
   corresponding route in `routes_json`. If a route has no `match_value`, use
   its `label` instead. This is how the frontend connects edges to handles.
10. For loops: route back to an earlier node, but ensure there is always a path
    that exits the loop to `__end__`.

### State
11. Set `state_fields` appropriately. Most agents need at least an "input" field (type "str").
    Chat agents typically also need a "messages" field (type "list[str]").
12. The `output_fields` array controls which state fields appear in the final output.
    Leave empty to include all fields.

### Layout
13. Assign `position` with x/y coordinates. Space nodes ~250px apart.
    Start at roughly x=300, y=100 for the first node, going down.
{current_graph_section}
## Complete Examples
Copy these structures exactly. Pay close attention to how edges are wired.

### Example 1: RAG Agent (retriever + LLM)
```json
{{
  "nodes": [
    {{"id": "vs-1", "type": "vector_search", "name": "Retriever", "writes_to": "context", "config": {{"query_from": "input", "index_name": "your-catalog.your-schema.your-index", "endpoint_name": "your-vs-endpoint", "columns": "text", "num_results": 3}}, "position": {{"x": 300, "y": 100}}}},
    {{"id": "llm-1", "type": "llm", "name": "Assistant", "writes_to": "messages", "config": {{"endpoint": "your-endpoint-name", "system_prompt": "You are a helpful assistant. Use the retrieved context to answer accurately.", "temperature": 0.7}}, "position": {{"x": 300, "y": 350}}}}
  ],
  "edges": [
    {{"id": "e-start-vs", "source": "__start__", "target": "vs-1", "source_handle": null}},
    {{"id": "e-vs-llm", "source": "vs-1", "target": "llm-1", "source_handle": null}},
    {{"id": "e-llm-end", "source": "llm-1", "target": "__end__", "source_handle": null}}
  ],
  "state_fields": [
    {{"name": "input", "type": "str", "description": "The user question", "sub_fields": []}},
    {{"name": "context", "type": "str", "description": "Retrieved documents", "sub_fields": []}},
    {{"name": "messages", "type": "list[str]", "description": "Conversation history", "sub_fields": []}}
  ],
  "output_fields": []
}}
```
Key points: vs-1 has ONE outgoing edge to llm-1. llm-1 has ONE outgoing edge to __end__. Linear chain.

### Example 2: Human-in-the-Loop with Feedback
```json
{{
  "nodes": [
    {{"id": "genie-1", "type": "genie", "name": "Genie Query", "writes_to": "context", "config": {{"question_from": "input", "room_id": "your-genie-room-id"}}, "position": {{"x": 300, "y": 100}}}},
    {{"id": "human-1", "type": "human_input", "name": "Review Answer", "writes_to": "feedback", "config": {{"prompt": "Does this answer look correct? Reply yes or no."}}, "position": {{"x": 300, "y": 350}}}},
    {{"id": "router-1", "type": "router", "name": "Feedback Router", "writes_to": "route", "config": {{"evaluates": "feedback", "routes_json": "[{{\\\"label\\\":\\\"approved\\\",\\\"match_value\\\":\\\"yes\\\"}},{{\\\"label\\\":\\\"revise\\\",\\\"match_value\\\":\\\"no\\\"}}]"}}, "position": {{"x": 300, "y": 600}}}}
  ],
  "edges": [
    {{"id": "e-start-genie", "source": "__start__", "target": "genie-1", "source_handle": null}},
    {{"id": "e-genie-human", "source": "genie-1", "target": "human-1", "source_handle": null}},
    {{"id": "e-human-router", "source": "human-1", "target": "router-1", "source_handle": null}},
    {{"id": "e-router-end", "source": "router-1", "target": "__end__", "source_handle": "yes"}},
    {{"id": "e-router-loop", "source": "router-1", "target": "genie-1", "source_handle": "no"}}
  ],
  "state_fields": [
    {{"name": "input", "type": "str", "description": "The user question", "sub_fields": []}},
    {{"name": "context", "type": "str", "description": "Genie response", "sub_fields": []}},
    {{"name": "feedback", "type": "str", "description": "User feedback", "sub_fields": []}},
    {{"name": "route", "type": "str", "description": "Routing decision", "sub_fields": []}}
  ],
  "output_fields": []
}}
```
Key points: ONLY router-1 has multiple outgoing edges. source_handle values ("yes", "no") match the match_value in routes_json, NOT the label. genie-1 and human-1 each have exactly ONE outgoing edge. The loop exits via the "yes" route to __end__.

### Example 3: Simple Chat Agent
```json
{{
  "nodes": [
    {{"id": "llm-1", "type": "llm", "name": "Chat Assistant", "writes_to": "messages", "config": {{"endpoint": "your-endpoint-name", "system_prompt": "You are a helpful assistant.", "temperature": 0.7}}, "position": {{"x": 300, "y": 100}}}}
  ],
  "edges": [
    {{"id": "e-start-llm", "source": "__start__", "target": "llm-1", "source_handle": null}},
    {{"id": "e-llm-end", "source": "llm-1", "target": "__end__", "source_handle": null}}
  ],
  "state_fields": [
    {{"name": "input", "type": "str", "description": "The user message", "sub_fields": []}},
    {{"name": "messages", "type": "list[str]", "description": "Conversation history", "sub_fields": []}}
  ],
  "output_fields": []
}}
```
"""


def handle_ai_chat(req: AIChatRequest) -> AIChatResponse:
    """Process an AI chat request and return a response with optional graph."""
    try:
        system_prompt = _build_system_prompt(req.current_graph)

        llm = ChatDatabricks(endpoint=_DEFAULT_MODEL, temperature=0.3)

        lc_messages: list = [SystemMessage(content=system_prompt)]
        for msg in req.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))

        response = llm.invoke(lc_messages)
        if not isinstance(response.content, str):
            raise TypeError(
                f"Expected string content from Chat Completions API, got {type(response.content).__name__}. "
                f"This model may only support the Responses API, which is not supported here."
            )
        raw_text = response.content

        return _parse_response(raw_text)

    except Exception as e:
        logger.exception("AI chat failed")
        return AIChatResponse(
            message="Sorry, I encountered an error. Please try again.",
            error=str(e),
        )


def _parse_response(raw_text: str) -> AIChatResponse:
    """Parse LLM output into AIChatResponse, validating any graph JSON."""
    text = raw_text.strip()

    # Handle markdown code fences
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else 3
        text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return AIChatResponse(message=raw_text)
        else:
            return AIChatResponse(message=raw_text)

    message = data.get("message", raw_text)
    graph_data = data.get("graph")

    if graph_data:
        try:
            graph = GraphDef.model_validate(graph_data)
        except Exception as e:
            logger.warning("Graph validation failed: %s", e)
            return AIChatResponse(
                message=message + f"\n\n*(Graph validation failed: {e})*",
            )

        # Structural validation
        errors = _validate_graph_structure(graph)
        if errors:
            error_list = "\n".join(f"- {e}" for e in errors)
            return AIChatResponse(
                message=message + f"\n\n**Graph has structural issues:**\n{error_list}",
                graph=graph,
            )

        return AIChatResponse(message=message, graph=graph)

    return AIChatResponse(message=message)


def _validate_graph_structure(graph: GraphDef) -> list[str]:
    """Validate graph structure and return a list of error strings."""
    errors: list[str] = []
    node_ids = {n.id for n in graph.nodes}
    node_types = {n.id: n.type for n in graph.nodes}
    valid_ids = node_ids | {"__start__", "__end__"}

    # Check edge references
    for edge in graph.edges:
        if edge.source not in valid_ids:
            errors.append(f"Edge references unknown source: {edge.source}")
        if edge.target not in valid_ids:
            errors.append(f"Edge references unknown target: {edge.target}")

    # Must have __start__ and __end__ connections
    start_edges = [e for e in graph.edges if e.source == "__start__"]
    end_edges = [e for e in graph.edges if e.target == "__end__"]
    if not start_edges:
        errors.append("No edge from __start__. The graph needs an entry point.")
    if not end_edges:
        errors.append("No edge to __end__. Every path must eventually reach __end__.")

    # Non-router nodes should have exactly one outgoing edge
    from collections import Counter
    outgoing_counts = Counter(e.source for e in graph.edges if e.source not in ("__start__", "__end__"))
    for node_id, count in outgoing_counts.items():
        if count > 1 and node_types.get(node_id) != "router":
            errors.append(
                f"Node '{node_id}' has {count} outgoing edges but is not a router. "
                f"Only router nodes can have multiple outgoing edges."
            )

    # Every node should have at least one outgoing edge (reachability to __end__)
    sources = {e.source for e in graph.edges}
    for node_id in node_ids:
        if node_id not in sources:
            errors.append(f"Node '{node_id}' has no outgoing edges — it's a dead end.")

    # Every node should be reachable (has at least one incoming edge)
    targets = {e.target for e in graph.edges}
    for node_id in node_ids:
        if node_id not in targets:
            errors.append(f"Node '{node_id}' has no incoming edges — it's unreachable.")

    # Router source_handle must match match_value (or label) from routes_json
    node_configs = {n.id: n.config for n in graph.nodes}
    for node_id in node_ids:
        if node_types.get(node_id) != "router":
            continue
        config = node_configs.get(node_id, {})
        routes_raw = config.get("routes_json", "[]")
        try:
            routes = json.loads(routes_raw) if isinstance(routes_raw, str) else routes_raw
        except (json.JSONDecodeError, TypeError):
            routes = []
        valid_handles = set()
        for route in routes:
            valid_handles.add(route.get("match_value") or route.get("label", ""))

        outgoing = [e for e in graph.edges if e.source == node_id]
        for edge in outgoing:
            if edge.source_handle and edge.source_handle not in valid_handles:
                errors.append(
                    f"Router '{node_id}' edge to '{edge.target}' has source_handle "
                    f"'{edge.source_handle}' but routes_json only has handles: "
                    f"{valid_handles}. source_handle must match a route's match_value."
                )

    return errors
