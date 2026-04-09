"""FastAPI backend for the Agent Builder app."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

import mlflow
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
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
    DatabricksTable,
    DatabricksVectorSearchIndex,
)

from langchain_core.messages import BaseMessage

from .auth import set_user_token, get_workspace_client
from .ai_chat import AIChatRequest, AIChatResponse, handle_ai_chat
from .graph_builder import build_graph, filter_output, run_graph
from .nodes import get_all_metadata
from .setup import router as setup_router, ensure_setup_table
from .schema import (
    DeployEvent,
    DeployMode,
    DeployRequest,
    DeployStepStatus,
    GraphDef,
    PreviewRequest,
    PreviewResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).parent

_MSG_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "system": "system"}


def _serialize_messages(messages: list) -> list[dict]:
    """Convert BaseMessage objects (from add_messages reducer) to plain dicts."""
    result = []
    for msg in messages:
        if isinstance(msg, dict):
            result.append(msg)
        elif isinstance(msg, BaseMessage):
            role = _MSG_TYPE_TO_ROLE.get(msg.type, msg.type)
            entry: dict = {"role": role, "content": msg.content}
            # Preserve the node tag if present in additional_kwargs
            node = msg.additional_kwargs.get("node")
            if node:
                entry["node"] = node
            result.append(entry)
    return result


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
        "function_name": DatabricksFunction,          # UC functions
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
        ignore=shutil.ignore_patterns(
            "mlruns", "__pycache__", "static", "*.pyc", "*.db", "mlflow_model.py",
        ),
    )
    return [str(tmp)]


app = FastAPI(title="Agent Builder", version="0.1.0")


@app.on_event("startup")
def _startup():
    ensure_setup_table()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class OBOMiddleware(BaseHTTPMiddleware):
    """Extract the user's OBO token from the x-forwarded-access-token header."""

    async def dispatch(self, request: Request, call_next):
        token = request.headers.get("x-forwarded-access-token")
        set_user_token(token)
        return await call_next(request)


app.add_middleware(OBOMiddleware)

app.include_router(setup_router, prefix="/api/setup", tags=["setup"])


# ── Preview session store (in-memory, per-process) ────────────────────────────

_preview_sessions: dict[str, InMemorySaver] = {}

# ── MLflow preview tracing setup ──────────────────────────────────────────────
# When deployed: use Lakebase Postgres for durable trace storage.
# When local:    use in-memory SQLite — traces live only for the process lifetime.
_lakebase_trace_conn = os.environ.get("LAKEBASE_TRACE_CONN_STRING", "")
if _lakebase_trace_conn:
    _PREVIEW_TRACKING_URI = _lakebase_trace_conn
    logger.info("MLflow playground traces → Lakebase")
else:
    # Use a temp file DB instead of :memory: because in-memory SQLite is
    # per-connection and MLflow opens multiple connections.
    _preview_trace_db = Path(tempfile.mkdtemp()) / "preview_traces.db"
    _PREVIEW_TRACKING_URI = f"sqlite:///{_preview_trace_db}"
    logger.info("MLflow playground traces → temp DB (%s)", _preview_trace_db)

# Initialize the preview tracking DB and experiment once at startup
_prev_uri = mlflow.get_tracking_uri()
mlflow.set_tracking_uri(_PREVIEW_TRACKING_URI)
mlflow.set_experiment("playground")
mlflow.set_tracking_uri(_prev_uri)

# ── API routes ────────────────────────────────────────────────────────────────


@app.get("/api/nodes")
def list_nodes():
    """Return metadata for every registered node type."""
    return get_all_metadata()


@app.post("/api/ai-chat", response_model=AIChatResponse)
def ai_chat(req: AIChatRequest) -> AIChatResponse:
    """Generate or modify a graph definition from natural language."""
    return handle_ai_chat(req)


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


def _extract_trace() -> list[dict]:
    """Grab the last MLflow trace and serialize its spans for the frontend."""
    try:
        trace_id = mlflow.get_last_active_trace_id()
        if not trace_id:
            return []
        trace = mlflow.get_trace(trace_id)
        if not trace:
            return []
        spans = []
        for span in trace.data.spans:
            entry: dict = {
                "name": span.name,
                "status": str(span.status),
                "start_time_ms": span.start_time_ns // 1_000_000 if span.start_time_ns else 0,
                "end_time_ms": span.end_time_ns // 1_000_000 if span.end_time_ns else 0,
            }
            # Include inputs/outputs but truncate large values
            if span.inputs:
                try:
                    entry["inputs"] = _truncate(span.inputs)
                except Exception:
                    entry["inputs"] = str(span.inputs)[:500]
            if span.outputs:
                try:
                    entry["outputs"] = _truncate(span.outputs)
                except Exception:
                    entry["outputs"] = str(span.outputs)[:500]
            spans.append(entry)
        return spans
    except Exception as e:
        logger.warning("Failed to extract MLflow trace: %s", e)
        return []


def _truncate(obj, max_str_len: int = 500):
    """Truncate string values in a dict/list structure for safe serialization."""
    if isinstance(obj, str):
        return obj[:max_str_len] + "..." if len(obj) > max_str_len else obj
    if isinstance(obj, dict):
        return {k: _truncate(v, max_str_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(item, max_str_len) for item in obj[:20]]
    return obj


@app.post("/api/graph/preview", response_model=PreviewResponse)
def preview_graph(req: PreviewRequest):
    """Build the graph and run it with a test message.

    Supports multi-turn conversations (via *thread_id*) and human-in-the-loop
    interrupts (via *resume_value*).
    """
    thread_id = req.thread_id or str(uuid.uuid4())
    if thread_id not in _preview_sessions:
        _preview_sessions[thread_id] = InMemorySaver()

    # Enable MLflow tracing — swap to the preview tracking DB for this request.
    prev_tracking_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(_PREVIEW_TRACKING_URI)
    mlflow.set_experiment("playground")
    mlflow.langchain.autolog(log_traces=True)

    try:
        result = run_graph(
            req.graph,
            req.input_message,
            checkpointer=_preview_sessions[thread_id],
            thread_id=thread_id,
            resume_value=req.resume_value,
        )

        # Only return messages from the current turn.
        # Walk backwards from the end to find the last user message — that's
        # the boundary of this turn.
        all_messages = result.get("messages", [])
        turn_start = 0
        for i in range(len(all_messages) - 1, -1, -1):
            msg = all_messages[i]
            is_user = (isinstance(msg, dict) and msg.get("role") == "user") or (
                hasattr(msg, "type") and msg.type == "human"
            )
            if is_user:
                turn_start = i
                break
        messages = _serialize_messages(all_messages[turn_start:])
        mlflow_trace = _extract_trace()

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
                execution_trace=messages,
                state=state_snapshot,
                mlflow_trace=mlflow_trace,
            )

        output_text, state_snapshot = filter_output(result, req.graph)
        return PreviewResponse(
            success=True,
            output=output_text,
            execution_trace=messages,
            state=state_snapshot,
            thread_id=thread_id,
            mlflow_trace=mlflow_trace,
        )
    except GraphInterrupt as gi:
        prompt = gi.interrupts[0].value if gi.interrupts else "Input needed"
        mlflow_trace = _extract_trace()
        return PreviewResponse(
            success=True,
            interrupt=str(prompt),
            thread_id=thread_id,
            mlflow_trace=mlflow_trace,
        )
    except Exception as e:
        logger.exception("Preview failed")
        return PreviewResponse(success=False, error=str(e))
    finally:
        # Restore previous tracking URI so deploy still points at Databricks
        mlflow.set_tracking_uri(prev_tracking_uri)


@app.get("/api/graph/load-from-run")
def load_graph_from_run(run_id: str):
    """Load a GraphDef from an MLflow run's artifacts.

    Returns run metadata and the graph definition so the frontend can
    show the user what was found before they accept.
    """
    try:
        mlflow.set_tracking_uri("databricks")

        # Fetch run metadata
        run = mlflow.get_run(run_id)
        run_name = run.info.run_name or run_id
        experiment_id = run.info.experiment_id

        import glob as _glob

        search_paths = [
            "agent/artifacts/graph_def",
            "agent/artifacts",
            "agent",
            "",
        ]
        searched = []

        for artifact_path in search_paths:
            searched.append(artifact_path or "(root)")
            try:
                local_path = mlflow.artifacts.download_artifacts(
                    run_id=run_id,
                    artifact_path=artifact_path or None,
                    tracking_uri="databricks",
                )
            except Exception:
                continue

            if Path(local_path).is_dir():
                json_files = _glob.glob(f"{local_path}/*.json")
            elif local_path.endswith(".json"):
                json_files = [local_path]
            else:
                continue

            for jf in json_files:
                try:
                    with open(jf) as f:
                        graph_data = json.load(f)
                    if "nodes" in graph_data and "edges" in graph_data:
                        graph = GraphDef.model_validate(graph_data)
                        return {
                            "success": True,
                            "graph": graph.model_dump(),
                            "run_name": run_name,
                            "experiment_id": experiment_id,
                            "found_at": artifact_path or "(root)",
                            "searched": searched,
                        }
                except (json.JSONDecodeError, Exception):
                    continue

        return {
            "success": False,
            "error": f"No graph definition found in run {run_id}.",
            "run_name": run_name,
            "searched": searched,
        }
    except Exception as e:
        logger.exception("Failed to load graph from run %s", run_id)
        return {"success": False, "error": str(e)}


@app.post("/api/graph/deploy")
def deploy_graph(req: DeployRequest):
    """Log the graph as an MLflow model and optionally register + deploy.

    Streams SSE events so the frontend can show step-by-step progress.
    """

    def _emit(step: str, status: DeployStepStatus, message: str,
              data: dict[str, str] | None = None) -> str:
        event = DeployEvent(step=step, status=status, message=message, data=data)
        return f"data: {event.model_dump_json()}\n\n"

    def _generate():
        result_data: dict[str, str] = {}
        needs_register = req.deploy_mode in (DeployMode.LOG_AND_REGISTER, DeployMode.FULL)
        needs_endpoint = req.deploy_mode == DeployMode.FULL

        # ── Step 1: Validate ──────────────────────────────────────────
        yield _emit("validate", DeployStepStatus.RUNNING, "Compiling graph...")
        try:
            build_graph(req.graph)
        except Exception as e:
            yield _emit("validate", DeployStepStatus.ERROR, f"Graph validation failed: {e}")
            return
        yield _emit("validate", DeployStepStatus.DONE, "Graph compiled successfully")

        # ── Step 2: Log model to MLflow ───────────────────────────────
        yield _emit("log_model", DeployStepStatus.RUNNING,
                     f"Logging model to experiment {req.experiment_path}...")
        model_info = None
        try:
            # Ensure the parent directory is visible to the SP before
            # creating the experiment (Genesis Workbench pattern).
            # The experiment_path should be like /Users/user/folder/experiment,
            # so the parent is the folder the user granted SP access to.
            from .auth import get_sp_workspace_client as _get_sp
            exp_parent = req.experiment_path.rsplit("/", 1)[0]
            try:
                _get_sp().workspace.mkdirs(exp_parent)
            except Exception:
                pass  # best-effort; the folder may already exist

            mlflow.set_tracking_uri("databricks")
            mlflow.set_registry_uri("databricks-uc")
            experiment = mlflow.set_experiment(req.experiment_path)
            result_data["experiment_id"] = experiment.experiment_id

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                f.write(req.graph.model_dump_json())
                graph_def_path = f.name

            resources = _extract_resources(req.graph)

            requirements_path = _BACKEND_DIR.parent / "requirements-serving.txt"
            if not requirements_path.exists():
                raise FileNotFoundError(
                    "requirements-serving.txt not found. Run: "
                    "uv pip compile pyproject.toml -o requirements-serving.txt "
                    "--python-version 3.11"
                )

            # Use explicit start/end so we can yield between steps
            run = mlflow.start_run()
            try:
                model_info = mlflow.pyfunc.log_model(
                    artifact_path="agent",
                    python_model=str(_BACKEND_DIR / "mlflow_model.py"),
                    artifacts={"graph_def": graph_def_path},
                    code_paths=_collect_code_paths(),
                    pip_requirements=str(requirements_path),
                    resources=resources if resources else None,
                )
            except Exception:
                mlflow.end_run()
                raise

            result_data["run_id"] = run.info.run_id
        except Exception as e:
            yield _emit("log_model", DeployStepStatus.ERROR,
                        f"Model logging failed: {e}")
            return
        yield _emit("log_model", DeployStepStatus.DONE,
                     f"Model logged (run: {run.info.run_id})")

        # ── Step 3: Register model in Unity Catalog ───────────────────
        if not needs_register:
            mlflow.end_run()
            yield _emit("register_model", DeployStepStatus.SKIPPED,
                        "Skipped (Log Only mode)")
            yield _emit("create_endpoint", DeployStepStatus.SKIPPED,
                        "Skipped (Log Only mode)")
            yield _emit("complete", DeployStepStatus.DONE,
                        "Model logged successfully", result_data)
            return

        yield _emit("register_model", DeployStepStatus.RUNNING,
                     f"Registering {req.model_name} in Unity Catalog...")
        try:
            parts = req.model_name.split(".")
            if len(parts) != 3:
                raise ValueError(
                    f"Model name must be catalog.schema.model_name format, "
                    f"got '{req.model_name}'"
                )
            # Registration uses the SP credentials (via MLflow env vars),
            # so we skip OBO pre-validation — the OBO token doesn't have
            # catalog API scopes anyway.
            mv = mlflow.register_model(
                model_uri=model_info.model_uri,
                name=req.model_name,
            )
            result_data["model_version"] = str(mv.version)
        except Exception as e:
            mlflow.end_run()
            yield _emit("register_model", DeployStepStatus.ERROR,
                        f"Registration failed: {e}")
            return
        mlflow.end_run()
        yield _emit("register_model", DeployStepStatus.DONE,
                     f"Registered as {req.model_name} v{mv.version}")

        # ── Step 4: Create / update serving endpoint ──────────────────
        if not needs_endpoint:
            yield _emit("create_endpoint", DeployStepStatus.SKIPPED,
                        "Skipped (Log & Register mode)")
            yield _emit("complete", DeployStepStatus.DONE,
                        "Model registered successfully", result_data)
            return

        yield _emit("create_endpoint", DeployStepStatus.RUNNING,
                     "Creating serving endpoint...")
        try:
            # The entire deploy flow uses SP credentials — OBO tokens
            # lack scopes for MLflow, UC catalog, and serving endpoint APIs.
            from .auth import get_sp_workspace_client as _get_sp
            w = _get_sp()
            endpoint_name = req.model_name.split(".")[-1].replace("_", "-")

            env_vars = {
                "ENABLE_MLFLOW_TRACING": "true",
                "MLFLOW_EXPERIMENT_ID": result_data.get("experiment_id", ""),
            }
            if req.lakebase_conn_string:
                env_vars["LAKEBASE_CONN_STRING"] = req.lakebase_conn_string

            served_entity = ServedEntityInput(
                entity_name=req.model_name,
                entity_version=result_data["model_version"],
                environment_vars=env_vars if env_vars else None,
                scale_to_zero_enabled=True,
                workload_size="Small",
            )

            parts = req.model_name.split(".")
            ai_gateway = AiGatewayConfig(
                inference_table_config=AiGatewayInferenceTableConfig(
                    catalog_name=parts[0],
                    schema_name=parts[1],
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
                        catalog_name=parts[0],
                        schema_name=parts[1],
                        table_name_prefix=endpoint_name,
                        enabled=True,
                    ),
                )

            host = w.config.host.rstrip("/")
            result_data["endpoint_url"] = (
                f"{host}/serving-endpoints/{endpoint_name}/invocations"
            )
        except Exception as e:
            yield _emit("create_endpoint", DeployStepStatus.ERROR,
                        f"Endpoint creation failed: {e}")
            return
        yield _emit("create_endpoint", DeployStepStatus.DONE,
                     f"Endpoint ready: {endpoint_name}")

        # ── Done ──────────────────────────────────────────────────────
        yield _emit("complete", DeployStepStatus.DONE,
                     "Deployment complete!", result_data)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Serve frontend build ──────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
