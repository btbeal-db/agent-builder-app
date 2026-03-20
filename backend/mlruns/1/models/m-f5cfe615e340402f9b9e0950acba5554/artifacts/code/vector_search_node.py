from __future__ import annotations

from typing import Any

from .base import BaseNode, NodeConfigField
from . import register


@register
class VectorSearchNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "vector_search"

    @property
    def display_name(self) -> str:
        return "Vector Search"

    @property
    def description(self) -> str:
        return "Query a Databricks Vector Search index for relevant documents."

    @property
    def category(self) -> str:
        return "retrieval"

    @property
    def icon(self) -> str:
        return "search"

    @property
    def color(self) -> str:
        return "#06b6d4"

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="query_from",
                label="Query from",
                field_type="state_variable",
                default="user_input",
            ),
            NodeConfigField(
                name="index_name",
                label="Vector Search Index",
                placeholder="catalog.schema.my_vs_index",
            ),
            NodeConfigField(
                name="endpoint_name",
                label="VS Endpoint Name",
                placeholder="my-vs-endpoint",
            ),
            NodeConfigField(
                name="num_results",
                label="Number of Results",
                field_type="number",
                required=False,
                default=3,
            ),
            NodeConfigField(
                name="columns",
                label="Columns to Return",
                required=False,
                placeholder="content, source",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        query = state.get(config.get("query_from", "user_input"), "")
        num_results = int(config.get("num_results", 3))
        index_name = config.get("index_name", "?")

        stub_docs = [
            f"[Doc {i+1} from {index_name}]: Relevant content for '{query[:50]}...'"
            for i in range(num_results)
        ]

        return {
            writes_to: "\n\n".join(stub_docs),
            "messages": [
                {"role": "system", "content": f"Retrieved {len(stub_docs)} documents from '{index_name}'.", "node": "vector_search"}
            ],
        }
