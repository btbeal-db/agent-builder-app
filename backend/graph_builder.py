"""Translates a visual graph definition into a runnable LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import get_node
from .schema import GraphDef, StateFieldDef


def _build_state_type(state_fields: list[StateFieldDef]) -> type:
    """Dynamically create a TypedDict from the state model."""
    fields: dict[str, type] = {}
    for sf in state_fields:
        fields[sf.name] = str  # all state values are strings at runtime
    fields["messages"] = list
    return TypedDict("AgentState", fields)  # type: ignore[misc]


def _make_node_fn(node_impl, config: dict[str, Any], writes_to: str, target_field: StateFieldDef | None):
    """Create a closure so each graph node captures its own config.

    Returns only the state *updates* — LangGraph merges them into state.
    """

    def fn(state: dict[str, Any]) -> dict[str, Any]:
        enriched_config = {
            **config,
            "_writes_to": writes_to,
            "_target_field": target_field,
        }
        updates = node_impl.execute(state, enriched_config)

        # Append messages rather than replace
        if "messages" in updates:
            updates["messages"] = state.get("messages", []) + updates["messages"]

        return updates

    fn.__name__ = f"node_{writes_to or node_impl.node_type}"
    return fn


def _make_router_fn(node_impl, config: dict[str, Any]):
    """Create a routing function that returns the chosen route key."""

    def fn(state: dict[str, Any]) -> str:
        result = node_impl.execute(state, config)
        return result.get("_route", "default")

    fn.__name__ = f"router_{node_impl.node_type}"
    return fn


def build_graph(graph_def: GraphDef):
    """Build a compiled LangGraph StateGraph from a GraphDef."""

    state_type = _build_state_type(graph_def.state_fields)
    builder = StateGraph(state_type)

    node_map = {n.id: n for n in graph_def.nodes}

    start_edges = [e for e in graph_def.edges if e.source == "__start__"]
    end_edges = [e for e in graph_def.edges if e.target == "__end__"]
    regular_edges = [
        e for e in graph_def.edges
        if e.source != "__start__" and e.target != "__end__"
    ]

    entry_nodes = [e.target for e in start_edges]
    exit_nodes = [e.source for e in end_edges]

    if not entry_nodes:
        raise ValueError("Connect the START node to at least one node.")
    if not exit_nodes:
        raise ValueError("Connect at least one node to the END node.")

    outgoing: dict[str, list] = {n.id: [] for n in graph_def.nodes}
    for edge in regular_edges:
        outgoing[edge.source].append(edge)

    router_nodes = set()
    for node_def in graph_def.nodes:
        node_impl = get_node(node_def.type)
        is_router = getattr(node_impl, "is_router", False)
        if is_router:
            router_nodes.add(node_def.id)

        target_field = graph_def.get_state_field(node_def.writes_to)
        builder.add_node(
            node_def.id,
            _make_node_fn(node_impl, node_def.config, node_def.writes_to, target_field),
        )

    for nid in entry_nodes:
        builder.add_edge(START, nid)

    # Build a lookup of end_edges by source for router→END handling
    end_edges_by_source: dict[str, list] = {}
    for e in end_edges:
        end_edges_by_source.setdefault(e.source, []).append(e)

    for node_id, edges in outgoing.items():
        if node_id in router_nodes:
            node_def = node_map[node_id]
            node_impl = get_node(node_def.type)
            route_map: dict[str, str] = {}
            for edge in edges:
                handle = edge.source_handle or "default"
                route_map[handle] = edge.target
            # Include any router→END edges in the route map
            for edge in end_edges_by_source.pop(node_id, []):
                handle = edge.source_handle or "default"
                route_map[handle] = END
            builder.add_conditional_edges(
                node_id,
                _make_router_fn(node_impl, node_def.config),
                route_map,
            )
        else:
            for edge in edges:
                builder.add_edge(node_id, edge.target)

    # Remaining (non-router) → END edges
    for nid, edges in end_edges_by_source.items():
        builder.add_edge(nid, END)

    return builder.compile()


def run_graph(graph_def: GraphDef, input_message: str) -> dict[str, Any]:
    """Build and invoke the graph with a user message."""
    compiled = build_graph(graph_def)

    initial_state: dict[str, Any] = {
        f.name: "" for f in graph_def.state_fields
    }
    initial_state["user_input"] = input_message
    initial_state["messages"] = [
        {"role": "user", "content": input_message, "node": "_start"},
    ]

    result = compiled.invoke(initial_state)
    return result


def generate_code(graph_def: GraphDef) -> str:
    """Generate a standalone Python file that reconstructs this graph."""

    lines = [
        '"""Auto-generated LangGraph agent."""',
        "",
        "from typing import TypedDict",
        "",
        "from langgraph.graph import END, START, StateGraph",
        "",
        "",
        "class AgentState(TypedDict):",
    ]
    for sf in graph_def.state_fields:
        comment = f"  # {sf.description}" if sf.description else ""
        lines.append(f"    {sf.name}: str{comment}")
    lines.append("    messages: list")
    lines.append("")
    lines.append("")

    for node_def in graph_def.nodes:
        node_impl = get_node(node_def.type)
        fn_name = f"node_{node_def.id}"
        lines.append(f"def {fn_name}(state: dict) -> dict:")
        lines.append(f'    """Node: {node_impl.display_name} | updates: {node_def.writes_to}"""')
        lines.append(f"    config = {node_def.config!r}")
        lines.append(f"    # TODO: implement {node_impl.node_type} logic")
        lines.append(f'    state["{node_def.writes_to}"] = ""  # placeholder')
        lines.append(f"    return state")
        lines.append("")
        lines.append("")

    lines.append("def build_graph():")
    lines.append("    builder = StateGraph(AgentState)")
    lines.append("")

    for node_def in graph_def.nodes:
        lines.append(f'    builder.add_node("{node_def.id}", node_{node_def.id})')
    lines.append("")

    start_edges = [e for e in graph_def.edges if e.source == "__start__"]
    end_edges = [e for e in graph_def.edges if e.target == "__end__"]
    regular_edges = [
        e for e in graph_def.edges
        if e.source != "__start__" and e.target != "__end__"
    ]

    outgoing: dict[str, list] = {n.id: [] for n in graph_def.nodes}
    for edge in regular_edges:
        outgoing[edge.source].append(edge)

    for e in start_edges:
        lines.append(f'    builder.add_edge(START, "{e.target}")')
    for node_id, edges in outgoing.items():
        for edge in edges:
            lines.append(f'    builder.add_edge("{edge.source}", "{edge.target}")')
    for e in end_edges:
        lines.append(f'    builder.add_edge("{e.source}", END)')

    lines.append("")
    lines.append("    return builder.compile()")
    lines.append("")

    return "\n".join(lines)
