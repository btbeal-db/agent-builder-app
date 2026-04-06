"""FastAPI backend for the Agent Builder app."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from langchain_core.messages import BaseMessage

from .auth import set_user_token, get_workspace_client
from .deploy_helpers import extract_resources, collect_code_paths
from .ai_chat import AIChatRequest, AIChatResponse, handle_ai_chat
from .graph_builder import build_graph, filter_output, run_graph
from .nodes import get_all_metadata
from .schema import (
    AppConfig,
    DeployEvent,
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



# _extract_resources and _collect_code_paths moved to deploy_helpers.py


app = FastAPI(title="Agent Builder", version="0.1.0")

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


# ── App deployment config ────────────────────────────────────────────────────

_EXPERIMENT_BASE = os.environ.get("EXPERIMENT_BASE", "/Shared/agent-builder")

# DEPLOY_JOB_ID: try env var first (local dev), then discover from app resources.
_DEPLOY_JOB_ID = os.environ.get("DEPLOY_JOB_ID", "")
if not _DEPLOY_JOB_ID:
    try:
        _app_name = os.environ.get("DATABRICKS_APP_NAME", "")
        if _app_name:
            _app_info = WorkspaceClient().apps.get(_app_name)
            for r in _app_info.resources or []:
                if r.name == "deploy-job" and r.job:
                    _DEPLOY_JOB_ID = r.job.id
                    logger.info("Discovered DEPLOY_JOB_ID=%s from app resources", _DEPLOY_JOB_ID)
                    break
    except Exception as e:
        logger.warning("Could not discover deploy job ID from app resources: %s", e)


def _get_app_config() -> AppConfig:
    return AppConfig(
        experiment_base=_EXPERIMENT_BASE,
        deploy_job_id=_DEPLOY_JOB_ID,
    )


@app.get("/api/config")
def get_config() -> AppConfig:
    """Return the app's deployment configuration for the frontend."""
    return _get_app_config()


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
    """Validate the graph and submit a deploy Job.

    Returns the Job run_id immediately.  The frontend polls
    ``/api/graph/deploy/status`` for progress.
    """
    cfg = _get_app_config()

    if not req.catalog or not req.schema_name:
        raise HTTPException(400, "Catalog and schema are required.")
    if not cfg.deploy_job_id:
        raise HTTPException(400, "App not configured: set DEPLOY_JOB_ID env var.")

    # Validate
    try:
        build_graph(req.graph)
    except Exception as e:
        raise HTTPException(400, f"Graph validation failed: {e}")

    # Submit the deploy Job
    try:
        w = WorkspaceClient()  # SP credentials for Job submission

        # Identify the deploying user via the OBO token
        deployed_by = ""
        try:
            user_client = get_workspace_client()  # OBO client
            me = user_client.current_user.me()
            deployed_by = me.user_name or ""
        except Exception:
            pass

        # Resolve the git ref the app is running from so the Job
        # installs the same version of the package.
        git_ref = "main"
        repo_url = ""
        try:
            app_info = w.apps.get(os.environ.get("DATABRICKS_APP_NAME", ""))
            git_source = app_info.active_deployment.git_source
            if git_source:
                git_ref = git_source.branch or git_source.tag or git_ref
                if git_source.git_repository:
                    repo_url = git_source.git_repository.url or ""
        except Exception:
            pass

        params_json = json.dumps({
            "graph_json": req.graph.model_dump_json(),
            "model_name": req.model_name,
            "catalog": req.catalog,
            "schema_name": req.schema_name,
            "experiment_base": cfg.experiment_base,
            "lakebase_conn_string": req.lakebase_conn_string,
            "git_ref": git_ref,
            "repo_url": repo_url,
            "deployed_by": deployed_by,
        })
        run_response = w.jobs.run_now(
            job_id=int(cfg.deploy_job_id),
            notebook_params={"params_json": params_json},
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to submit deploy job: {e}")

    fq_model_name = f"{req.catalog}.{req.schema_name}.{req.model_name}"
    endpoint_name = req.model_name.replace("_", "-")

    return {
        "run_id": run_response.run_id,
        "model_name": fq_model_name,
        "endpoint_name": endpoint_name,
    }


@app.get("/api/graph/deploy/status")
def deploy_status(run_id: int):
    """Poll the status of a deploy Job run."""
    try:
        w = WorkspaceClient()
        run_state = w.jobs.get_run(run_id=run_id)
    except Exception as e:
        raise HTTPException(500, f"Failed to check job status: {e}")

    lifecycle = run_state.state.life_cycle_state.value if run_state.state.life_cycle_state else ""
    terminal_states = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}

    if lifecycle not in terminal_states:
        return {"status": "running", "lifecycle": lifecycle}

    result_state = run_state.state.result_state
    if result_state and result_state.value == "SUCCESS":
        # Read notebook output
        try:
            output = w.jobs.get_run_output(run_id=run_id)
            result = json.loads(output.notebook_output.result)
        except Exception:
            result = {}
        return {"status": "success", **result}
    else:
        return {
            "status": "failed",
            "error": run_state.state.state_message or "Job failed",
        }


# ── Serve frontend build ──────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
