from __future__ import annotations

from typing import Any

from .base import BaseNode, NodeConfigField
from . import register


@register
class GenieNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "genie"

    @property
    def display_name(self) -> str:
        return "Genie Room"

    @property
    def description(self) -> str:
        return "Query a Databricks Genie Room for structured data answers."

    @property
    def category(self) -> str:
        return "retrieval"

    @property
    def icon(self) -> str:
        return "database"

    @property
    def color(self) -> str:
        return "#f59e0b"

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="question_from",
                label="Question from",
                field_type="state_variable",
                default="user_input",
            ),
            NodeConfigField(
                name="room_id",
                label="Genie Room ID",
                placeholder="01efg...",
            ),
            NodeConfigField(
                name="description",
                label="Room Description",
                field_type="textarea",
                required=False,
                placeholder="What data does this room answer questions about?",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        query = state.get(config.get("question_from", "user_input"), "")
        room_id = config.get("room_id", "?")

        stub_result = (
            f"[Genie Room {room_id}] "
            f"SQL result for: {query[:80]}\n"
            f"| col_a | col_b |\n|-------|-------|\n| val1  | val2  |"
        )

        return {
            writes_to: stub_result,
            "messages": [
                {"role": "system", "content": f"Genie room '{room_id}' returned structured data.", "node": "genie"}
            ],
        }
