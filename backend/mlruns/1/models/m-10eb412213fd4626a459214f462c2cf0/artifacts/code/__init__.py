"""Node registry — auto-discovers all node modules in this package."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from .base import BaseNode

_registry: dict[str, BaseNode] = {}


def register(cls: type[BaseNode]) -> type[BaseNode]:
    """Class decorator: instantiates the node and adds it to the registry."""
    instance = cls()
    _registry[instance.node_type] = instance
    return cls


def get_node(node_type: str) -> BaseNode:
    if node_type not in _registry:
        raise KeyError(f"Unknown node type: {node_type!r}")
    return _registry[node_type]


def get_all_nodes() -> dict[str, BaseNode]:
    return dict(_registry)


def get_all_metadata() -> list[dict[str, Any]]:
    return [n.to_metadata() for n in _registry.values()]


# Auto-import every sibling module so @register decorators fire.
for _importer, _mod_name, _is_pkg in pkgutil.iter_modules(__path__):
    if _mod_name != "base":
        importlib.import_module(f".{_mod_name}", __name__)
