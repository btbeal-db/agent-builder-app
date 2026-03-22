"""FastAPI backend for the Agent Builder app."""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists
from databricks.sdk.service.serving import (
    AiGatewayConfig,
    AiGatewayInferenceTableConfig,
    EndpointCoreConfigInput,
    ServedEntityInput,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mlflow.models.resources import (
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
    DatabricksTable,
    DatabricksVectorSearchIndex,
)

from .graph_builder import build_graph, generate_code, run_graph
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


def _extract_resources(graph: GraphDef) -> list:
    """Extract Databricks resource declarations from all nodes in the graph.

    Maps node config fields to the appropriate MLflow resource types so that
    Model Serving provisions credentials (OBO) for each external resource.
    """
    resources = []
    seen = set()

    # Config field name → resource class mapping
    resource_map = {
        "endpoint": DatabricksServingEndpoint,        # LLM serving endpoints
        "endpoint_name": DatabricksServingEndpoint,   # VS endpoint names
        "index_name": DatabricksVectorSearchIndex,    # VS indexes
        "room_id": DatabricksGenieSpace,              # Genie rooms
        "table_name": DatabricksTable,                # UC tables
    }

    for node in graph.nodes:
        for config_key, resource_cls in resource_map.items():
            value = node.config.get(config_key)
            if value and (config_key, value) not in seen:
                seen.add((config_key, value))
                # Each resource class takes a single positional arg for its identifier
                init_param = {
                    DatabricksServingEndpoint: "endpoint_name",
                    DatabricksVectorSearchIndex: "index_name",
                    DatabricksGenieSpace: "genie_space_id",
                    DatabricksTable: "table_name",
                }[resource_cls]
                resources.append(resource_cls(**{init_param: value}))

    return resources


def _collect_code_paths() -> list[str]:
    """Copy backend/ to a clean temp directory (no __pycache__, static, etc.) for MLflow code_paths.

    MLflow code_paths needs a directory to preserve the package structure
    so that `from backend.graph_builder import ...` works in the serving container.
    """
    import shutil

    tmp = Path(tempfile.mkdtemp()) / "backend"
    shutil.copytree(
        _BACKEND_DIR,
        tmp,
        ignore=shutil.ignore_patterns("mlruns", "__pycache__", "static", "*.pyc", "*.db"),
    )
    return [str(tmp)]


app = FastAPI(title="Agent Builder", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Preview session store (in-memory, per-process) ────────────────────────────

_preview_sessions: dict[str, InMemorySaver] = {}

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
    """Build the graph and run it with a test message.

    Supports multi-turn conversations (via *thread_id*) and human-in-the-loop
    interrupts (via *resume_value*).
    """
    thread_id = req.thread_id or str(uuid.uuid4())
    if thread_id not in _preview_sessions:
        _preview_sessions[thread_id] = InMemorySaver()

    try:
        result = run_graph(
            req.graph,
            req.input_message,
            checkpointer=_preview_sessions[thread_id],
            thread_id=thread_id,
            resume_value=req.resume_value,
        )

        # Check for interrupt in the returned state (LangGraph embeds it
        # as __interrupt__ rather than raising GraphInterrupt when a
        # checkpointer is present).
        interrupts = result.get("__interrupt__")
        if interrupts:
            prompt = interrupts[0].get("value", "Input needed") if isinstance(interrupts[0], dict) else str(interrupts[0].value)
            state_snapshot = {
                k: v for k, v in result.items()
                if k not in ("messages", "__interrupt__")
            }
            return PreviewResponse(
                success=True,
                interrupt=str(prompt),
                thread_id=thread_id,
                execution_trace=result.get("messages", []),
                state=state_snapshot,
            )

        state_snapshot = {
            k: v for k, v in result.items()
            if k not in ("messages", "__interrupt__")
        }
        return PreviewResponse(
            success=True,
            output=str(result.get("output", result.get("input", ""))),
            execution_trace=result.get("messages", []),
            state=state_snapshot,
            thread_id=thread_id,
        )
    except GraphInterrupt as gi:
        prompt = gi.interrupts[0].value if gi.interrupts else "Input needed"
        return PreviewResponse(
            success=True,
            interrupt=str(prompt),
            thread_id=thread_id,
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

        # 2. Set up MLflow tracking and registry to point at Databricks
        mlflow.set_tracking_uri("databricks")
        mlflow.set_registry_uri("databricks-uc")
        mlflow.set_experiment(req.experiment_path)
        mlflow.langchain.autolog()

        # 3. Serialize GraphDef to a temp JSON artifact
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(req.graph.model_dump_json())
            graph_def_path = f.name

        # 4. Declare Databricks resources the model needs (enables OBO auth)
        resources = _extract_resources(req.graph)

        # 5. Log model to MLflow — use pinned requirements compiled from pyproject.toml
        requirements_path = _BACKEND_DIR.parent / "requirements-serving.txt"
        if not requirements_path.exists():
            raise FileNotFoundError(
                "requirements-serving.txt not found. Run: "
                "uv pip compile pyproject.toml -o requirements-serving.txt --python-version 3.11"
            )

        with mlflow.start_run() as run:
            model_info = mlflow.pyfunc.log_model(
                artifact_path="agent",
                python_model=str(_BACKEND_DIR / "mlflow_model.py"),
                artifacts={"graph_def": graph_def_path},
                code_paths=_collect_code_paths(),
                pip_requirements=str(requirements_path),
                resources=resources if resources else None,
            )

            # 6. Ensure catalog and schema exist, then register in UC
            parts = req.model_name.split(".")
            if len(parts) == 3:
                catalog, schema_name, _ = parts
                w = WorkspaceClient()
                try:
                    w.schemas.get(f"{catalog}.{schema_name}")
                except Exception:
                    # Schema doesn't exist — try to create it
                    try:
                        w.schemas.create(name=schema_name, catalog_name=catalog)
                        logger.info("Created schema %s.%s", catalog, schema_name)
                    except Exception as schema_err:
                        logger.warning("Could not create schema: %s", schema_err)

            mv = mlflow.register_model(
                model_uri=model_info.model_uri,
                name=req.model_name,
            )
            model_version = mv.version

        # 7. Create or update serving endpoint
        w = WorkspaceClient()
        # Use just the model short name (last segment) as the endpoint name
        endpoint_name = req.model_name.split(".")[-1].replace("_", "-")

        env_vars = {}
        if req.lakebase_conn_string:
            env_vars["LAKEBASE_CONN_STRING"] = req.lakebase_conn_string

        served_entity = ServedEntityInput(
            entity_name=req.model_name,
            entity_version=str(model_version),
            environment_vars=env_vars if env_vars else None,
            scale_to_zero_enabled=True,
            workload_size="Small",
        )

        # AI Gateway inference table config (replaces legacy AutoCaptureConfig)
        parts = req.model_name.split(".")
        ai_gateway = AiGatewayConfig(
            inference_table_config=AiGatewayInferenceTableConfig(
                catalog_name=parts[0] if len(parts) >= 3 else None,
                schema_name=parts[1] if len(parts) >= 3 else None,
                table_name_prefix=endpoint_name,
                enabled=True,
            ),
        )

        try:
            w.serving_endpoints.create(
                name=endpoint_name,
                config=EndpointCoreConfigInput(
                    name=endpoint_name,
                    served_entities=[served_entity],
                ),
                ai_gateway=ai_gateway,
            )
        except ResourceAlreadyExists:
            w.serving_endpoints.update_config(
                name=endpoint_name,
                served_entities=[served_entity],
            )
            w.serving_endpoints.put_ai_gateway(
                name=endpoint_name,
                inference_table_config=AiGatewayInferenceTableConfig(
                    catalog_name=parts[0] if len(parts) >= 3 else None,
                    schema_name=parts[1] if len(parts) >= 3 else None,
                    table_name_prefix=endpoint_name,
                    enabled=True,
                ),
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
