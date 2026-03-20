"""FastAPI backend for the Agent Builder app."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import (
    AutoCaptureConfigInput,
    EndpointCoreConfigInput,
    ServedEntityInput,
)
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .graph_builder import build_graph, generate_code, run_graph
from .mlflow_model import AgentGraphModel
from .nodes import get_all_metadata
from .schema import (
    DeployRequest,
    DeployResponse,
    ExportResponse,
    GraphDef,
    PreviewRequest,
    PreviewResponse,
)

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).parent


def _collect_code_paths() -> list[str]:
    """Collect all .py files under backend/ for MLflow code_paths (skip __pycache__, static, etc.)."""
    return sorted(
        str(p) for p in _BACKEND_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    )


app = FastAPI(title="Agent Builder", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API routes ────────────────────────────────────────────────────────────────


@app.get("/api/nodes")
def list_nodes():
    """Return metadata for every registered node type."""
    return get_all_metadata()


@app.post("/api/graph/validate")
def validate_graph(graph: GraphDef):
    """Basic structural validation of a graph definition."""
    errors: list[str] = []

    if not graph.nodes:
        errors.append("Graph has no nodes.")

    node_ids = {n.id for n in graph.nodes}
    valid_ids = node_ids | {"__start__", "__end__"}

    for edge in graph.edges:
        if edge.source not in valid_ids:
            errors.append(f"Edge references unknown source node: {edge.source}")
        if edge.target not in valid_ids:
            errors.append(f"Edge references unknown target node: {edge.target}")

    start_edges = [e for e in graph.edges if e.source == "__start__"]
    end_edges = [e for e in graph.edges if e.target == "__end__"]

    if not start_edges:
        errors.append("Connect the START node to at least one node.")
    if not end_edges:
        errors.append("Connect at least one node to the END node.")

    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/api/graph/preview", response_model=PreviewResponse)
def preview_graph(req: PreviewRequest):
    """Build the graph and run it with a test message."""
    try:
        result = run_graph(req.graph, req.input_message)
        # Return all user-defined state variables (exclude messages)
        state_snapshot = {
            k: v for k, v in result.items() if k != "messages"
        }
        return PreviewResponse(
            success=True,
            output=str(result.get("output", result.get("user_input", ""))),
            execution_trace=result.get("messages", []),
            state=state_snapshot,
        )
    except Exception as e:
        return PreviewResponse(success=False, error=str(e))


@app.post("/api/graph/export", response_model=ExportResponse)
def export_graph(graph: GraphDef):
    """Generate a standalone Python file for this graph."""
    try:
        code = generate_code(graph)
        return ExportResponse(success=True, code=code)
    except Exception as e:
        return ExportResponse(success=False, error=str(e))


@app.post("/api/graph/deploy", response_model=DeployResponse)
def deploy_graph(req: DeployRequest):
    """Log the graph as an MLflow model and create a Model Serving endpoint."""
    try:
        # 1. Validate the graph compiles
        build_graph(req.graph)

        # 2. Set up MLflow experiment and tracing
        mlflow.set_experiment(req.experiment_path)
        mlflow.langchain.autolog()

        # 3. Serialize GraphDef to a temp JSON artifact
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(req.graph.model_dump_json())
            graph_def_path = f.name

        # 4. OBO auth policy scopes
        auth_policy = {
            "permissions": [
                {"serving_endpoint_permission": {"allowed_entities": [], "filter": "ALL"}},
            ],
            "system_permissions": [
                {"permission": "serving.serving-endpoints"},
                {"permission": "vectorsearch.vector-search-endpoints"},
                {"permission": "vectorsearch.vector-search-indexes"},
            ],
        }

        # 5. Log model to MLflow
        pip_requirements = [
            "langgraph>=0.2.0",
            "langchain-core>=0.3.0",
            "langchain-community>=0.3.0",
            "databricks-sdk>=0.20.0",
            "databricks-langchain>=0.17.0",
            "langgraph-checkpoint-postgres>=2.0.0",
            "psycopg[binary]>=3.1.0",
            "mlflow>=3.10.1",
        ]

        with mlflow.start_run() as run:
            model_info = mlflow.pyfunc.log_model(
                artifact_path="agent",
                python_model=AgentGraphModel(),
                artifacts={"graph_def": graph_def_path},
                code_paths=_collect_code_paths(),
                pip_requirements=pip_requirements,
                resources=auth_policy,
            )

            # 6. Register model in Unity Catalog
            mlflow.set_registry_uri("databricks-uc")
            mv = mlflow.register_model(
                model_uri=model_info.model_uri,
                name=req.model_name,
            )
            model_version = mv.version

        # 7. Create or update serving endpoint
        w = WorkspaceClient()
        endpoint_name = req.model_name.replace(".", "_")

        env_vars = {}
        if req.lakebase_conn_string:
            env_vars["LAKEBASE_CONN_STRING"] = req.lakebase_conn_string

        served_entity = ServedEntityInput(
            entity_name=req.model_name,
            entity_version=str(model_version),
            environment_vars=env_vars if env_vars else None,
            scale_to_zero_enabled=True,
        )

        try:
            w.serving_endpoints.create(
                name=endpoint_name,
                config=EndpointCoreConfigInput(
                    served_entities=[served_entity],
                    auto_capture_config=AutoCaptureConfigInput(enabled=True),
                ),
            )
        except Exception:
            # Endpoint may already exist — update it
            w.serving_endpoints.update_config(
                name=endpoint_name,
                served_entities=[served_entity],
                auto_capture_config=AutoCaptureConfigInput(enabled=True),
            )

        host = w.config.host.rstrip("/")
        endpoint_url = f"{host}/serving-endpoints/{endpoint_name}/invocations"

        return DeployResponse(
            success=True,
            endpoint_url=endpoint_url,
            model_version=str(model_version),
        )

    except Exception as e:
        logger.exception("Deploy failed")
        return DeployResponse(success=False, error=str(e))


# ── Serve frontend build ──────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
