from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


def resolve_state(state: dict[str, Any], path: str, default: Any = "") -> Any:
    """Resolve a dot-path reference from state.

    For flat keys (e.g. "input") this is just ``state.get(path)``.
    For dot paths (e.g. "output.query_filters") it reads the top-level
    field, parses it as JSON if needed, and extracts the nested key.
    """
    if "." not in path:
        return state.get(path, default)

    top, sub = path.split(".", 1)
    raw = state.get(top, default)
    if not raw:
        return default

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default

    if isinstance(raw, dict):
        return raw.get(sub, default)

    return default


class NodeConfigField(BaseModel):
    """Describes a single configuration field for a node type.

    When ``field_type`` is ``"state_variable"``, the frontend renders a
    dropdown populated with the user-defined state variables.
    """

    name: str
    label: str
    field_type: str = "text"  # text | select | number | textarea | state_variable | schema_editor | route_editor
    required: bool = True
    default: Any = None
    options: list[str] | None = None
    placeholder: str = ""
    help_text: str = ""


class BaseNode(ABC):
    """Extend this class to create a new agent-builder node.

    1. Subclass BaseNode
    2. Implement the abstract properties and ``execute``
    3. Decorate with ``@register`` from ``nodes/__init__.py``

    That's it — the node will appear in the palette automatically.
    """

    # -- metadata ----------------------------------------------------------

    @property
    @abstractmethod
    def node_type(self) -> str:
        """Unique machine-readable identifier (e.g. 'llm', 'vector_search')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the UI palette."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description shown on hover / in docs."""

    @property
    @abstractmethod
    def config_fields(self) -> list[NodeConfigField]:
        """All config fields, including state variable references."""

    @property
    def category(self) -> str:
        return "general"

    @property
    def icon(self) -> str:
        return "puzzle"

    @property
    def color(self) -> str:
        """Hex color for the node header in the UI."""
        return "#6366f1"

    @property
    def tool_compatible(self) -> bool:
        """Whether this node type can be attached as a tool to an LLM node."""
        return False

    @property
    def default_field_template(self) -> dict[str, str] | None:
        """Default state field auto-created when this node is dropped on the canvas.

        Return ``None`` for node types that don't write to state (e.g. routers).
        """
        return None

    # -- execution ---------------------------------------------------------

    @abstractmethod
    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Run this node.

        Args:
            state: The full agent state.
            config: The user-supplied configuration values.

        Returns:
            A dict of state *updates* (will be merged into state).
        """

    # -- serialisation helpers --------------------------------------------

    def to_metadata(self) -> dict[str, Any]:
        return {
            "type": self.node_type,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "color": self.color,
            "config_fields": [f.model_dump() for f in self.config_fields],
            "tool_compatible": self.tool_compatible,
            "default_field_template": self.default_field_template,
        }
