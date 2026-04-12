from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from .base import BaseNode, NodeConfigField
from . import register


@register
class HumanInputNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "human_input"

    @property
    def display_name(self) -> str:
        return "Human Input"

    @property
    def description(self) -> str:
        return "Pause execution and ask the user a question."

    @property
    def category(self) -> str:
        return "control"

    @property
    def icon(self) -> str:
        return "user"

    @property
    def color(self) -> str:
        return "#f59e0b"

    @property
    def default_field_template(self) -> dict[str, str] | None:
        return {"name": "user_response", "type": "str", "description": "User's response"}

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="prompt",
                label="Question to ask",
                field_type="textarea",
                default="Please provide your input:",
                help_text="Shown to the user when the graph pauses. Use {field_name} to include state values.",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        raw_prompt = config.get("prompt", "Please provide your input:")

        # Resolve {field_name} templates from state (same pattern as LLMNode)
        prompt = raw_prompt
        for key, val in state.items():
            if key in ("messages", "_writes_to", "_target_field"):
                continue
            prompt = prompt.replace(f"{{{key}}}", str(val))

        # First call: raises GraphInterrupt, pausing the graph.
        # After resume: returns the user's response.
        answer = interrupt(prompt)

        return {
            writes_to: answer,
            "messages": [
                {"role": "assistant", "content": prompt, "node": "human_input"},
                {"role": "user", "content": answer, "node": "human_input"},
            ],
        }
