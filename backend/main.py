"""FastAPI backend for AgentSweet."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

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
from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessageChunk, BaseMessage

from .auth import (
    set_user_token,
    set_user_pat,
    get_workspace_client,
    get_sp_workspace_client,
    create_pat_client,
)
from .ai_chat import AIChatRequest, AIChatResponse, handle_ai_chat
from .graph_builder import build_graph, filter_output, interrupt_value, pending_interrupts, prepare_invocation
from .deploy_resources import (
    _build_auth_policy,
    _extract_resources,
    _persist_mcp_tool_metadata,
    graph_to_app_resources,
    graph_to_user_api_scopes,
    lakebase_app_resource,
)
from .app_deploy import AppDeployConfig, generate_app_project
from .nodes import get_all_metadata
from .nodes.llm_node import extract_visible_text
from .lakebase import LakebaseConfig, provision_lakebase, resolve_lakebase
from .setup import router as setup_router
from .schema import (
    AppDeployRequest,
    AuthMode,
    DeployEvent,
    DeployMode,
    DeployRequest,
    DeployStepStatus,
    GraphDef,
    ModelInfo,
    ModelsResponse,
    PreviewRequest,
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


def _extract_resource_links(graph: dict, host: str) -> list:
    """Build resource labels with deep links from a raw graph dict.

    Used by the Models listing to show what Databricks resources a model uses.
    To add a new resource type, add an entry to RESOURCE_LINK_MAP below.
    """
    from .schema import ResourceLink

    def _uc_url(val: str) -> str:
        """Turn a dotted UC path (catalog.schema.object) into a URL path."""
        parts = val.split(".")
        return "/".join(parts) if len(parts) == 3 else val

    # Config key → (display prefix, URL builder)
    # URL builder receives (host, raw_value) and returns the full URL.
    RESOURCE_LINK_MAP: dict[str, tuple[str, callable]] = {
        "endpoint":      ("LLM",    lambda h, v: f"{h}/ml/ai-gateway/{v}"),
        "index_name":    ("VS",     lambda h, v: f"{h}/explore/data/{_uc_url(v)}"),
        "room_id":       ("Genie",  lambda h, v: f"{h}/genie/rooms/{v}"),
        "function_name": ("UC Fn",  lambda h, v: f"{h}/explore/data/{_uc_url(v)}"),
        "table_name":    ("Table",  lambda h, v: f"{h}/explore/data/{_uc_url(v)}"),
    }

    links: list[ResourceLink] = []
    seen: set[str] = set()

    def _scan(config: dict) -> None:
        for key, (prefix, url_fn) in RESOURCE_LINK_MAP.items():
            val = config.get(key)
            if val and val not in seen:
                seen.add(val)
                short = val.rsplit(".", 1)[-1] if "." in val else val
                links.append(ResourceLink(
                    label=f"{prefix}: {short}",
                    url=url_fn(host, val) if host else "",
                ))

    for node in graph.get("nodes", []):
        _scan(node.get("config", {}))
        tools_raw = node.get("config", {}).get("tools_json", "")
        if tools_raw and str(tools_raw).strip():
            try:
                for tc in json.loads(str(tools_raw)):
                    _scan(tc.get("config", {}))
            except (json.JSONDecodeError, TypeError):
                pass

    return links


def _collect_code_paths() -> list[str]:
    """Copy backend/ to a clean temp directory (no __pycache__, static, etc.) for MLflow code_paths.

    MLflow code_paths needs a directory to preserve the package structure
    so that `from backend.graph_builder import ...` works in the serving container.
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


app = FastAPI(title="AgentSweet", version="0.1.0")


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

from .discovery import router as discovery_router

app.include_router(setup_router, prefix="/api/setup", tags=["setup"])
app.include_router(discovery_router, prefix="/api/discover", tags=["discovery"])


# ── Preview session store (in-memory, per-process) ────────────────────────────

_preview_sessions: dict[str, InMemorySaver] = {}


def _is_conversational_graph(graph: GraphDef) -> bool:
    """A graph is conversational if any LLM node has ``conversational=true``."""
    return any(
        n.type == "llm" and str(n.config.get("conversational", "false")).lower() == "true"
        for n in graph.nodes
    )

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


@app.get("/api/test-vs")
def test_vector_search(index_name: str, request: FastAPIRequest, query: str = "test"):
    """Try every combination of auth_type and env masking for OBO Vector Search."""
    from databricks.sdk import WorkspaceClient

    token = request.headers.get("x-forwarded-access-token")
    host = os.environ.get("DATABRICKS_HOST", "")

    if not token:
        return {"error": "No OBO token (x-forwarded-access-token header missing)"}

    def _try_query(label: str, auth_type: str | None, mask: bool) -> dict:
        masked = {}
        if mask:
            for key in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
                if key in os.environ:
                    masked[key] = os.environ.pop(key)
        try:
            kwargs = {"host": host, "token": token}
            if auth_type:
                kwargs["auth_type"] = auth_type
            w = WorkspaceClient(**kwargs)
            resp = w.vector_search_indexes.query_index(
                index_name=index_name,
                columns=[],
                query_text=query,
                num_results=1,
            )
            return {"label": label, "success": True, "num_results": len(resp.as_dict().get("result", {}).get("data_array", []))}
        except Exception as exc:
            return {"label": label, "success": False, "error": str(exc)}
        finally:
            os.environ.update(masked)

    # Also test what OBO can actually do with catalog APIs
    obo_checks = {}
    masked = {}
    for key in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
        if key in os.environ:
            masked[key] = os.environ.pop(key)
    try:
        w = WorkspaceClient(host=host, token=token, auth_type="pat")

        # Can OBO read the table/index metadata?
        try:
            t = w.tables.get(full_name=index_name)
            obo_checks["tables.get"] = {"success": True, "table_type": str(t.table_type)}
        except Exception as exc:
            obo_checks["tables.get"] = {"success": False, "error": str(exc)}

        # Can OBO list grants?
        try:
            g = w.grants.get_effective(securable_type="TABLE", full_name=index_name)
            obo_checks["grants.get_effective"] = {"success": True, "count": len(g.privilege_assignments or [])}
        except Exception as exc:
            obo_checks["grants.get_effective"] = {"success": False, "error": str(exc)}

        # Can OBO get current user? (sanity check)
        try:
            me = w.current_user.me()
            obo_checks["current_user"] = {"success": True, "user": me.user_name}
        except Exception as exc:
            obo_checks["current_user"] = {"success": False, "error": str(exc)}
    finally:
        os.environ.update(masked)

    return {
        "token_length": len(token),
        "obo_checks": obo_checks,
        "vs_results": [
            _try_query("auth_type=pat, masked=yes", auth_type="pat", mask=True),
        ],
    }


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


def _sse(event: dict) -> str:
    """Format an SSE ``data:`` line, JSON-encoding the payload."""
    return f"data: {json.dumps(event, default=str)}\n\n"


def _turn_messages(all_messages: list) -> list[dict]:
    """Extract serialized messages from the most recent user turn.

    Walks backwards to find the last user message — anything after that
    boundary is "this turn".
    """
    turn_start = 0
    for i in range(len(all_messages) - 1, -1, -1):
        msg = all_messages[i]
        is_user = (isinstance(msg, dict) and msg.get("role") == "user") or (
            hasattr(msg, "type") and msg.type == "human"
        )
        if is_user:
            turn_start = i
            break
    return _serialize_messages(all_messages[turn_start:])


@app.post("/api/graph/preview")
def preview_graph(req: PreviewRequest, request: FastAPIRequest):
    """Stream the graph as an SSE feed of token deltas + a final result event.

    Mirrors the deployed model's ``predict_stream`` so the playground UX
    matches what the user will see from the served endpoint. Multi-turn via
    ``thread_id``; human-in-the-loop via ``resume_value``.

    Event types:
      - ``delta`` ``{text}`` — incremental LLM token
      - ``done`` — terminal: full output, state, execution_trace, mlflow_trace
      - ``interrupt`` — terminal: graph paused at a HumanInput
      - ``error`` — terminal: execution failed
    """
    # Non-conversational graphs should not carry state across user turns.
    # Force a fresh thread unless the graph opted in or we're resuming an
    # interrupt (resume needs the existing checkpoint).
    is_resume = req.resume_value is not None
    if is_resume or _is_conversational_graph(req.graph):
        thread_id = req.thread_id or str(uuid.uuid4())
    else:
        thread_id = str(uuid.uuid4())
    if thread_id not in _preview_sessions:
        _preview_sessions[thread_id] = InMemorySaver()

    # The SSE generator below runs in Starlette's threadpool, which has its
    # own ContextVar context — neither the OBOMiddleware's ``_user_token``
    # nor a route-handler ``set_user_pat`` would propagate. Capture both
    # here and re-set them at the top of ``_generate``.
    obo_token = request.headers.get("x-forwarded-access-token")
    user_pat = req.pat

    # Enable MLflow tracing — swap to the preview tracking DB for this request.
    prev_tracking_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(_PREVIEW_TRACKING_URI)
    mlflow.set_experiment("playground")
    mlflow.langchain.autolog(log_traces=True)

    def _generate():
        # Re-establish auth in the generator's context. Without this the
        # data-access tools fall back to SP credentials and 403 on the
        # user's own VS index / Genie space.
        set_user_token(obo_token)
        set_user_pat(user_pat)
        try:
            compiled = build_graph(req.graph, checkpointer=_preview_sessions[thread_id])
            invoke_input, config = prepare_invocation(
                compiled, req.graph, req.input_message, thread_id, req.resume_value,
            )

            # Drive the graph with stream_mode=["messages", "updates"] so we get
            # both token chunks (for live UX) and per-node state updates (so we
            # can build the final result without a second pass).
            try:
                # Track when a non-chunk message (e.g. iter-1's full
                # AIMessage with tool_calls, then a ToolMessage) appears
                # between streaming runs — without a separator, iter-2's
                # tokens get glued onto iter-1's text.
                streamed_any = False
                boundary_pending = False
                for chunk in compiled.stream(
                    invoke_input, config=config or None,
                    stream_mode=["messages", "updates"],
                ):
                    mode, data = chunk
                    if mode == "messages":
                        msg, _metadata = data
                        # Only AIMessageChunk represents an incremental token.
                        # Plain AIMessage is the final completed message that
                        # LangGraph yields at the end of each LLM node — emitting
                        # it would duplicate text already streamed.
                        if type(msg) is AIMessageChunk and msg.content and not getattr(msg, "tool_calls", None):
                            text = extract_visible_text(msg.content)
                            if not text:
                                # All-reasoning chunk (gpt-oss harmony) — skip.
                                continue
                            if boundary_pending:
                                text = "\n\n" + text
                                boundary_pending = False
                            yield _sse({"type": "delta", "text": text})
                            streamed_any = True
                        elif streamed_any:
                            # Non-streamable message between runs of chunks
                            # marks an iteration boundary.
                            boundary_pending = True
            except GraphInterrupt as gi:
                prompt = gi.interrupts[0].value if gi.interrupts else "Input needed"
                final = compiled.get_state(config).values if config else {}
                yield _sse({
                    "type": "interrupt",
                    "thread_id": thread_id,
                    "prompt": str(prompt),
                    "execution_trace": _turn_messages(final.get("messages", [])),
                    "state": {k: v for k, v in final.items() if k not in ("messages", "__interrupt__")},
                    "mlflow_trace": _extract_trace(),
                })
                return

            # Stream finished cleanly — pull the final state from the checkpoint.
            final = compiled.get_state(config).values if config else {}

            # ``stream_mode=["messages", "updates"]`` parks the interrupt on
            # ``snap.tasks[i].interrupts`` instead of raising, so we check
            # there via the shared helper.
            interrupts = pending_interrupts(compiled, config)
            if interrupts:
                yield _sse({
                    "type": "interrupt",
                    "thread_id": thread_id,
                    "prompt": interrupt_value(interrupts[0]) or "Input needed",
                    "execution_trace": _turn_messages(final.get("messages", [])),
                    "state": {k: v for k, v in final.items() if k not in ("messages", "__interrupt__")},
                    "mlflow_trace": _extract_trace(),
                })
                return

            output_text, state_snapshot = filter_output(final, req.graph)
            yield _sse({
                "type": "done",
                "thread_id": thread_id,
                "output": output_text,
                "execution_trace": _turn_messages(final.get("messages", [])),
                "state": state_snapshot,
                "mlflow_trace": _extract_trace(),
            })
        except Exception as e:
            logger.exception("Preview failed")
            yield _sse({"type": "error", "message": str(e)})
        finally:
            set_user_pat(None)
            set_user_token(None)
            mlflow.set_tracking_uri(prev_tracking_uri)

    return StreamingResponse(_generate(), media_type="text/event-stream")





def _register_model_with_pat(
    host: str, pat: str, model_uri: str, model_name: str,
) -> SimpleNamespace:
    """Run ``mlflow.register_model`` in a subprocess with clean PAT credentials.

    MLflow caches DatabricksConfig in-process, so env-var masking doesn't
    reliably override the SP credentials.  A subprocess starts fresh.

    Returns a ``SimpleNamespace(version=...)`` matching the mlflow ModelVersion
    interface that downstream code expects.
    """
    reg_env = {
        "DATABRICKS_HOST": host,
        "DATABRICKS_TOKEN": pat,
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", ""),
    }
    reg_script = (
        "import mlflow, json; "
        "mlflow.set_tracking_uri('databricks'); "
        "mlflow.set_registry_uri('databricks-uc'); "
        f"mv = mlflow.register_model(model_uri={model_uri!r}, name={model_name!r}); "
        "print(json.dumps({'version': mv.version}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", reg_script],
        capture_output=True, text=True, env=reg_env,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Model registration failed: {proc.stderr.strip()}")
    mv_data = json.loads(proc.stdout.strip())
    return SimpleNamespace(version=mv_data["version"])


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

        # ── Step 1.5: Provision or resolve Lakebase ───────────────────
        lb_config: LakebaseConfig | None = None

        # Determine which lakebase operation to run (if any).
        lb_project_id = req.lakebase_project_id or req.lakebase_existing_project_id
        lb_is_create = bool(req.lakebase_project_id)

        # Capture SP client_id early, before any create_pat_client() call
        # masks the env var.  Concurrent deploys share os.environ, so reading
        # DATABRICKS_CLIENT_ID after masking causes a race condition.
        sp_client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")

        if lb_project_id:
            action = "Provisioning" if lb_is_create else "Resolving"
            yield _emit("provision_lakebase", DeployStepStatus.RUNNING,
                        f"{action} Lakebase project '{lb_project_id}'...")
            try:
                if not req.pat:
                    raise ValueError("A PAT is required for Lakebase setup")
                w = create_pat_client(req.pat)
                lb_fn = provision_lakebase if lb_is_create else resolve_lakebase
                lb_config = lb_fn(
                    w, lb_project_id, req.model_name, sp_client_id,
                )
            except Exception as e:
                yield _emit("provision_lakebase", DeployStepStatus.ERROR,
                            f"Lakebase setup failed: {e}")
                return
            yield _emit("provision_lakebase", DeployStepStatus.DONE,
                        f"Lakebase ready (db: {lb_config.database})")

        elif req.lakebase_conn_string:
            yield _emit("provision_lakebase", DeployStepStatus.DONE,
                        "Using provided connection string")

        else:
            yield _emit("provision_lakebase", DeployStepStatus.SKIPPED,
                        "No Lakebase configuration provided")

        # ── Step 2: Log model to MLflow ───────────────────────────────
        yield _emit("log_model", DeployStepStatus.RUNNING,
                     f"Logging model to experiment {req.experiment_path}...")
        model_info = None
        try:
            # Ensure the parent directory is visible to the SP (Genesis
            # Workbench pattern: mkdirs on the folder the user shared).
            exp_parent = req.experiment_path.rsplit("/", 1)[0]
            try:
                get_sp_workspace_client().workspace.mkdirs(exp_parent)
            except Exception:
                pass  # best-effort; the folder may already exist

            mlflow.set_tracking_uri("databricks")
            mlflow.set_registry_uri("databricks-uc")
            try:
                experiment = mlflow.set_experiment(req.experiment_path)
            except Exception as mlflow_exc:
                # Most common cause: experiment_path points at a workspace
                # folder (e.g. the setup folder itself) rather than a new path
                # inside it. Databricks can't create an experiment at a node
                # that already exists as a folder.
                raise ValueError(
                    f"Could not create experiment at '{req.experiment_path}'. "
                    f"If this path is a workspace folder, pass a sub-path "
                    f"instead (e.g. '{req.experiment_path.rstrip('/')}/my-agent'). "
                    f"Underlying error: {mlflow_exc}"
                ) from mlflow_exc
            if experiment is None:
                raise ValueError(
                    f"'{req.experiment_path}' appears to be a workspace folder, "
                    f"not an experiment. Pass a sub-path inside it "
                    f"(e.g. '{req.experiment_path.rstrip('/')}/my-agent')."
                )
            result_data["experiment_id"] = experiment.experiment_id

            # Persist auth_mode into the graph_def artifact so the served
            # model knows which credential strategy to use at runtime.
            graph_for_artifact = req.graph.model_copy(deep=True)
            graph_for_artifact.auth_mode = req.auth_mode.value

            # Pre-discover MCP tools and persist their metadata so the
            # served model never needs to contact the MCP server for
            # tool discovery (only for actual tool calls).
            _persist_mcp_tool_metadata(graph_for_artifact, pat=req.pat)

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                f.write(graph_for_artifact.model_dump_json())
                graph_def_path = f.name

            requirements_path = _BACKEND_DIR.parent / "requirements-serving.txt"
            if not requirements_path.exists():
                raise FileNotFoundError(
                    "requirements-serving.txt not found. Run: "
                    "uv pip compile pyproject.toml -o requirements-serving.txt "
                    "--python-version 3.11"
                )

            # Build resource declarations based on auth mode.
            # Both paths need a PAT client to resolve Genie downstream
            # dependencies (tables + SQL warehouse) — the SP typically
            # lacks permission to read Genie room metadata.
            res_client = create_pat_client(req.pat) if req.pat else None
            if req.auth_mode == AuthMode.OBO:
                auth_policy = _build_auth_policy(req.graph, client=res_client)
                resource_kwargs = {"auth_policy": auth_policy}
            else:
                resources = _extract_resources(req.graph, client=res_client)
                resource_kwargs = {"resources": resources if resources else None}

            run = mlflow.start_run()
            # Persist metadata as run tags so the Models page can list them
            # without downloading artifacts (presigned URLs are unreachable
            # from Databricks Apps networking).
            mlflow.set_tag("graph_def_json", req.graph.model_dump_json())
            mlflow.set_tag("deploy_mode", req.deploy_mode.value)
            if req.model_name:
                mlflow.set_tag("registered_model_name", req.model_name)
                if needs_endpoint:
                    mlflow.set_tag("endpoint_name",
                                   req.model_name.split(".")[-1].replace("_", "-"))
            if lb_config:
                # Look up the project UUID from the Lakebase API
                lb_uuid = ""
                try:
                    w_lb = create_pat_client(req.pat) if req.pat else get_sp_workspace_client()
                    for proj in w_lb.postgres.list_projects():
                        if proj.name == f"projects/{lb_project_id}":
                            lb_uuid = proj.uid or ""
                            break
                except Exception:
                    pass
                mlflow.set_tag("lakebase_project", lb_project_id)
                mlflow.set_tag("lakebase_project_uuid", lb_uuid)
            mlflow.set_tag("agent_sweet", "true")
            try:
                model_info = mlflow.pyfunc.log_model(
                    artifact_path="agent",
                    python_model=str(_BACKEND_DIR / "mlflow_model.py"),
                    artifacts={"graph_def": graph_def_path},
                    code_paths=_collect_code_paths(),
                    pip_requirements=str(requirements_path),
                    **resource_kwargs,
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
            catalog, schema_name, _ = parts
            host = os.environ.get("DATABRICKS_HOST", "")

            # Build a client for UC operations — PAT (user identity) or SP.
            if req.pat:
                uc_client = create_pat_client(req.pat)
            else:
                uc_client = get_sp_workspace_client()

            # Pre-validate catalog access
            try:
                uc_client.catalogs.get(catalog)
            except Exception:
                raise ValueError(
                    f"Catalog '{catalog}' does not exist or you don't have "
                    f"access to it. Verify the catalog name and your permissions."
                )

            # Pre-validate or create schema
            try:
                uc_client.schemas.get(f"{catalog}.{schema_name}")
            except Exception:
                try:
                    uc_client.schemas.create(name=schema_name, catalog_name=catalog)
                    logger.info("Created schema %s.%s", catalog, schema_name)
                except Exception as schema_err:
                    raise ValueError(
                        f"Schema '{catalog}.{schema_name}' does not exist and "
                        f"could not be created: {schema_err}"
                    )

            # Register model. With a PAT we run in a subprocess to get a
            # clean credential context — MLflow caches DatabricksConfig
            # in-process, so env-var masking alone isn't reliable.
            if req.pat:
                mv = _register_model_with_pat(
                    host, req.pat, model_info.model_uri, req.model_name,
                )
            else:
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
        # Capture SP creds + host before masking — needed as env vars on the endpoint.
        sp_id_for_env = os.environ.get("DATABRICKS_CLIENT_ID", "")
        sp_secret_for_env = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
        sp_host_for_env = os.environ.get("DATABRICKS_HOST", "")
        try:
            if req.pat:
                w = create_pat_client(req.pat)
            else:
                w = get_sp_workspace_client()

            endpoint_name = req.model_name.split(".")[-1].replace("_", "-")

            env_vars = {
                "ENABLE_MLFLOW_TRACING": "true",
                "MLFLOW_EXPERIMENT_ID": result_data.get("experiment_id", ""),
            }
            if lb_config:
                env_vars["LAKEBASE_ENDPOINT"] = lb_config.endpoint
                env_vars["LAKEBASE_HOST"] = lb_config.host
                env_vars["LAKEBASE_DATABASE"] = lb_config.database
                env_vars["LAKEBASE_SP_CLIENT_ID"] = sp_id_for_env
                env_vars["LAKEBASE_SP_CLIENT_SECRET"] = sp_secret_for_env
                env_vars["LAKEBASE_SP_HOST"] = sp_host_for_env
            elif req.lakebase_conn_string:
                env_vars["LAKEBASE_CONN_STRING"] = req.lakebase_conn_string

            served_entity = ServedEntityInput(
                entity_name=req.model_name,
                entity_version=result_data["model_version"],
                environment_vars=env_vars if env_vars else None,
                scale_to_zero_enabled=True,
                workload_size="Small",
            )

            catalog, schema_name = parts[0], parts[1]
            ai_gateway = AiGatewayConfig(
                inference_table_config=AiGatewayInferenceTableConfig(
                    catalog_name=catalog,
                    schema_name=schema_name,
                    table_name_prefix=endpoint_name,
                    enabled=True,
                ),
            )

            # Fire-and-forget — endpoint provisioning can take 10+ minutes.
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
                try:
                    w.serving_endpoints.put_ai_gateway(
                        name=endpoint_name,
                        inference_table_config=AiGatewayInferenceTableConfig(
                            catalog_name=catalog,
                            schema_name=schema_name,
                            table_name_prefix=endpoint_name,
                            enabled=True,
                        ),
                    )
                except Exception:
                    pass  # non-critical

            ep_host = host or w.config.host.rstrip("/")
            result_data["endpoint_url"] = (
                f"{ep_host}/serving-endpoints/{endpoint_name}/invocations"
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


@app.post("/api/graph/deploy-app")
def deploy_app(req: AppDeployRequest):
    """Deploy a graph as a Databricks App (agents on apps).

    Alternative to Model Serving: generate a git-repo-shaped app project,
    upload it to a workspace folder, then create + deploy a Databricks App
    exposing ``/invocations``.  Streams SSE step progress.
    """

    def _emit(step: str, status: DeployStepStatus, message: str,
              data: dict[str, str] | None = None) -> str:
        event = DeployEvent(step=step, status=status, message=message, data=data)
        return f"data: {event.model_dump_json()}\n\n"

    def _generate():
        import tempfile
        from pathlib import Path as _Path

        from databricks.sdk.service.apps import (
            App,
            AppDeployment,
            AppDeploymentMode,
            EnvVar,
        )
        from databricks.sdk.service.workspace import ImportFormat

        result_data: dict[str, str] = {}

        # ── Step 1: Validate ──────────────────────────────────────────
        yield _emit("validate", DeployStepStatus.RUNNING, "Compiling graph...")
        try:
            build_graph(req.graph)
        except Exception as e:
            yield _emit("validate", DeployStepStatus.ERROR, f"Graph validation failed: {e}")
            return
        yield _emit("validate", DeployStepStatus.DONE, "Graph compiled successfully")

        # ── Step 1.5: Provision or resolve Lakebase ───────────────────
        lb_config: LakebaseConfig | None = None
        lb_project_id = req.lakebase_project_id or req.lakebase_existing_project_id
        lb_is_create = bool(req.lakebase_project_id)
        # Capture SP host before any PAT client masks env vars.
        sp_host_for_env = os.environ.get("DATABRICKS_HOST", "")
        sp_client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")

        if lb_project_id:
            action = "Provisioning" if lb_is_create else "Resolving"
            yield _emit("provision_lakebase", DeployStepStatus.RUNNING,
                        f"{action} Lakebase project '{lb_project_id}'...")
            try:
                if not req.pat:
                    raise ValueError("A PAT is required for Lakebase setup")
                w_lb = create_pat_client(req.pat)
                lb_fn = provision_lakebase if lb_is_create else resolve_lakebase
                lb_config = lb_fn(w_lb, lb_project_id, req.app_name, sp_client_id)
            except Exception as e:
                yield _emit("provision_lakebase", DeployStepStatus.ERROR,
                            f"Lakebase setup failed: {e}")
                return
            yield _emit("provision_lakebase", DeployStepStatus.DONE,
                        f"Lakebase ready (db: {lb_config.database})")
        elif req.lakebase_conn_string:
            yield _emit("provision_lakebase", DeployStepStatus.DONE,
                        "Using provided connection string")
        else:
            yield _emit("provision_lakebase", DeployStepStatus.SKIPPED,
                        "No Lakebase configuration provided")

        # ── Step 2: Generate the app project ──────────────────────────
        yield _emit("generate_project", DeployStepStatus.RUNNING,
                    "Generating app project...")
        try:
            # Resolve resources + scopes (PAT client so Genie/MCP metadata
            # resolves under the user's identity; falls back to SP inside).
            res_client = create_pat_client(req.pat) if req.pat else None
            app_resources = graph_to_app_resources(req.graph, client=res_client)
            user_scopes = graph_to_user_api_scopes(req.graph)
            if lb_config and lb_project_id:
                # Grant the app SP the Lakebase database role. instance_name is
                # the Lakebase project; database_name is the per-agent database.
                app_resources.append(
                    lakebase_app_resource(lb_project_id, lb_config.database)
                )

            cfg = AppDeployConfig(
                app_name=req.app_name,
                auth_mode=req.auth_mode.value,
                resources=app_resources,
                user_api_scopes=user_scopes,
            )
            project_dir = _Path(tempfile.mkdtemp()) / req.app_name
            # Pre-discover MCP tools under the user's PAT (same as serving).
            _persist_mcp_tool_metadata(req.graph, pat=req.pat)
            generate_app_project(req.graph, cfg, project_dir)
        except Exception as e:
            yield _emit("generate_project", DeployStepStatus.ERROR,
                        f"Project generation failed: {e}")
            return
        yield _emit("generate_project", DeployStepStatus.DONE,
                    "App project generated")

        # ── Step 3: Upload to workspace files ─────────────────────────
        remote_root = f"{req.workspace_path.rstrip('/')}/{req.app_name}"
        yield _emit("upload_workspace_files", DeployStepStatus.RUNNING,
                    f"Uploading files to {remote_root}...")
        try:
            # SP has Can Manage on the setup folder (same as setup-file writes).
            w_sp = get_sp_workspace_client()
            for local_file in sorted(project_dir.rglob("*")):
                if not local_file.is_file():
                    continue
                rel = local_file.relative_to(project_dir)
                remote_path = f"{remote_root}/{rel.as_posix()}"
                w_sp.workspace.mkdirs(remote_path.rsplit("/", 1)[0])
                w_sp.workspace.upload(
                    remote_path,
                    local_file.read_bytes(),
                    format=ImportFormat.RAW,
                    overwrite=True,
                )
        except Exception as e:
            yield _emit("upload_workspace_files", DeployStepStatus.ERROR,
                        f"Workspace upload failed: {e}")
            return
        yield _emit("upload_workspace_files", DeployStepStatus.DONE,
                    "Files uploaded to workspace")

        # ── Step 4: Create the app ────────────────────────────────────
        yield _emit("create_app", DeployStepStatus.RUNNING,
                    f"Creating app '{req.app_name}'...")
        try:
            # App creation is privileged and user-attributed → prefer PAT.
            w_app = create_pat_client(req.pat) if req.pat else get_sp_workspace_client()
            try:
                app = w_app.apps.create_and_wait(App(
                    name=req.app_name,
                    description=f"AgentSweet agent: {req.app_name}",
                    default_source_code_path=remote_root,
                    user_api_scopes=user_scopes,
                    resources=app_resources,
                ))
            except ResourceAlreadyExists:
                app = w_app.apps.get(req.app_name)
        except Exception as e:
            yield _emit("create_app", DeployStepStatus.ERROR,
                        f"App creation failed (check you have permission to create "
                        f"apps, and that user authorization + these scopes are "
                        f"allowed by your workspace admin): {e}")
            return
        yield _emit("create_app", DeployStepStatus.DONE, "App created")

        # ── Step 5: Deploy the app ────────────────────────────────────
        yield _emit("deploy_app", DeployStepStatus.RUNNING, "Deploying app...")
        try:
            env_vars = []
            if lb_config:
                env_vars = [
                    EnvVar(name="LAKEBASE_ENDPOINT", value=lb_config.endpoint),
                    EnvVar(name="LAKEBASE_HOST", value=lb_config.host),
                    EnvVar(name="LAKEBASE_DATABASE", value=lb_config.database),
                    EnvVar(name="LAKEBASE_SP_HOST", value=sp_host_for_env),
                ]
            elif req.lakebase_conn_string:
                env_vars = [EnvVar(name="LAKEBASE_CONN_STRING",
                                   value=req.lakebase_conn_string)]

            deployment = AppDeployment(
                source_code_path=remote_root,
                mode=AppDeploymentMode.SNAPSHOT,
                env_vars=env_vars or None,
            )
            w_app.apps.deploy_and_wait(app_name=req.app_name, app_deployment=deployment)
        except Exception as e:
            yield _emit("deploy_app", DeployStepStatus.ERROR,
                        f"App deployment failed: {e}")
            return
        yield _emit("deploy_app", DeployStepStatus.DONE, "App deployed")

        # ── Done ──────────────────────────────────────────────────────
        app_url = getattr(app, "url", "") or ""
        result_data["app_name"] = req.app_name
        result_data["workspace_path"] = remote_root
        if app_url:
            result_data["app_url"] = app_url
            result_data["invocations_url"] = f"{app_url.rstrip('/')}/invocations"
        yield _emit("complete", DeployStepStatus.DONE,
                    "App deployed successfully!", result_data)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Models listing ────────────────────────────────────────────────────────────


@app.get("/api/models", response_model=ModelsResponse)
def list_models():
    """List deployed models from the user's MLflow experiment folder."""
    from .setup import setup_status

    status = setup_status()
    if not status.setup_complete or not status.experiment_path:
        return ModelsResponse(models=[])

    base_path = status.experiment_path
    # DATABRICKS_HOST on Apps points to the app's own URL, not the workspace.
    # Use the SP client's config to get the real workspace host.
    try:
        host = get_sp_workspace_client().config.host.rstrip("/")
    except Exception:
        host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    if host and not host.startswith("http"):
        host = f"https://{host}"

    prev_uri = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri("databricks")
        experiments = mlflow.search_experiments(
            filter_string=f"name LIKE '{base_path}/%'",
        )

        models: list[ModelInfo] = []
        for exp in experiments:
            name = exp.name.rsplit("/", 1)[-1]
            exp_url = f"{host}/ml/experiments/{exp.experiment_id}" if host else ""

            info = ModelInfo(
                name=name,
                experiment_id=exp.experiment_id,
                experiment_url=exp_url,
            )

            # Get latest run
            runs = mlflow.search_runs(
                experiment_ids=[exp.experiment_id],
                max_results=1,
                order_by=["start_time DESC"],
            )
            if not runs.empty:
                row = runs.iloc[0]
                info.latest_run_id = row.get("run_id")
                start_time = row.get("start_time")
                if start_time is not None:
                    info.latest_run_time = str(start_time)

                # Read tags
                info.deploy_mode = row.get("tags.deploy_mode")
                info.registered_model_name = row.get("tags.registered_model_name")
                info.endpoint_name = row.get("tags.endpoint_name")
                info.has_graph_def = bool(row.get("tags.graph_def_json"))

                # Parse graph_def for resource summary with links
                graph_json = row.get("tags.graph_def_json")
                if graph_json:
                    try:
                        graph = json.loads(graph_json)
                        info.resources = _extract_resource_links(graph, host)
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Lakebase
                lb_project = row.get("tags.lakebase_project")
                if lb_project:
                    from .schema import ResourceLink
                    lb_uuid = row.get("tags.lakebase_project_uuid", "")
                    lb_url = f"{host}/lakebase/projects/{lb_uuid}" if host and lb_uuid else ""
                    info.resources.append(ResourceLink(
                        label=f"Lakebase: {lb_project}",
                        url=lb_url,
                    ))

            models.append(info)

        models.sort(key=lambda m: m.latest_run_time or "", reverse=True)
        return ModelsResponse(models=models, workspace_url=host)
    finally:
        mlflow.set_tracking_uri(prev_uri)


@app.get("/api/models/{run_id}/graph")
def get_model_graph(run_id: str):
    """Return the graph definition from a run's tags."""
    prev_uri = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri("databricks")
        run = mlflow.get_run(run_id)
        graph_json = run.data.tags.get("graph_def_json")
        if not graph_json:
            raise HTTPException(
                status_code=404,
                detail="No graph definition found for this run. "
                       "Only models deployed after this update include the graph tag.",
            )
        return json.loads(graph_json)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        mlflow.set_tracking_uri(prev_uri)


# ── Serve frontend build ──────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
