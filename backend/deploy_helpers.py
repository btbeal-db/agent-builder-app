"""Shared deployment utilities used by both the FastAPI app and the deploy Job."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
    DatabricksTable,
    DatabricksVectorSearchIndex,
)

from .schema import GraphDef

_BACKEND_DIR = Path(__file__).parent


def extract_resources(graph: GraphDef) -> list:
    """Extract Databricks resource declarations from all nodes in the graph.

    Maps node config fields to the appropriate MLflow resource types so that
    Model Serving provisions credentials for each external resource.
    """
    resources = []
    seen: set[tuple[str, str]] = set()

    resource_map = {
        "endpoint": DatabricksServingEndpoint,
        "endpoint_name": DatabricksServingEndpoint,
        "index_name": DatabricksVectorSearchIndex,
        "room_id": DatabricksGenieSpace,
        "table_name": DatabricksTable,
        "function_name": DatabricksFunction,
    }

    for node in graph.nodes:
        for config_key, resource_cls in resource_map.items():
            value = node.config.get(config_key)
            if value and (config_key, value) not in seen:
                seen.add((config_key, value))
                init_param = {
                    DatabricksServingEndpoint: "endpoint_name",
                    DatabricksVectorSearchIndex: "index_name",
                    DatabricksGenieSpace: "genie_space_id",
                    DatabricksTable: "table_name",
                    DatabricksFunction: "function_name",
                }[resource_cls]
                resources.append(resource_cls(**{init_param: value}))

    return resources


def collect_code_paths() -> list[str]:
    """Copy backend/ to a clean temp directory for MLflow code_paths.

    MLflow code_paths needs a directory to preserve the package structure
    so that ``from backend.graph_builder import ...`` works in the serving container.
    """
    tmp = Path(tempfile.mkdtemp()) / "backend"
    shutil.copytree(
        _BACKEND_DIR,
        tmp,
        ignore=shutil.ignore_patterns(
            "mlruns", "__pycache__", "static", "*.pyc", "*.db", "mlflow_model.py",
        ),
    )
    return [str(tmp)]
