"""MLflow ResponsesAgent wrapper for deploying Agent Builder graphs via Model Serving.

This file is used as a "models from code" entry point by MLflow.
It must be importable standalone (no relative imports) because MLflow
loads it directly via the file path.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres import PostgresSaver

# Ensure the backend package is importable when MLflow loads this file
# from the code/ directory in the serving container.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from backend.graph_builder import build_graph, filter_output
from backend.schema import GraphDef

logger = logging.getLogger(__name__)


def _create_lakebase_checkpointer(
    endpoint: str, host: str, database: str,
) -> PostgresSaver:
    """Create a PostgresSaver backed by a ConnectionPool with automatic OAuth
    token refresh.

    Each time the pool opens a new connection it calls
    ``WorkspaceClient().postgres.generate_database_credential()`` to mint a
    fresh 1-hour token.  Existing connections remain valid even after the token
    that opened them expires (Lakebase enforces expiry only at login).
    """
    import psycopg
    from databricks.sdk import WorkspaceClient
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # Use the app's SP (which has a Lakebase role) rather than the
    # serving endpoint's auto-generated SP (which does not).
    sp_client_id = os.environ.get("LAKEBASE_SP_CLIENT_ID", "")
    sp_client_secret = os.environ.get("LAKEBASE_SP_CLIENT_SECRET", "")
    sp_host = os.environ.get("LAKEBASE_SP_HOST", "") or os.environ.get("DATABRICKS_HOST", "")

    if sp_client_id and sp_client_secret and sp_host:
        w = WorkspaceClient(
            host=sp_host,
            client_id=sp_client_id,
            client_secret=sp_client_secret,
        )
    else:
        w = WorkspaceClient()

    username = w.current_user.me().user_name

    class _LakebaseConnection(psycopg.Connection):
        """Custom connection class that generates a fresh OAuth token on
        every ``connect()`` call so the pool never uses a stale password."""

        @classmethod
        def connect(cls, conninfo="", **kwargs):
            cred = w.postgres.generate_database_credential(endpoint=endpoint)
            kwargs["password"] = cred.token
            return super().connect(
                conninfo, autocommit=True, prepare_threshold=0,
                row_factory=dict_row, **kwargs,
            )

    conninfo = f"host={host} port=5432 dbname={database} user={username} sslmode=require"
    pool = ConnectionPool(
        conninfo=conninfo,
        connection_class=_LakebaseConnection,
        min_size=1,
        max_size=5,
        open=True,
    )

    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    logger.info("Lakebase checkpointer initialized (pool, endpoint=%s)", endpoint)
    return checkpointer


def _create_connstring_checkpointer(conn_string: str) -> PostgresSaver:
    """Fallback: create a PostgresSaver from a static connection string.

    Use this only for non-Lakebase Postgres instances or manual overrides.
    Note that Lakebase OAuth tokens embedded in the URI expire after 1 hour.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=conn_string,
        min_size=1,
        max_size=5,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )

    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    logger.info("Lakebase checkpointer initialized (static conn string)")
    return checkpointer


def _extract_user_message(request: ResponsesAgentRequest) -> str:
    """Extract the last user message from the request input."""
    for item in reversed(request.input):
        if hasattr(item, "role") and item.role == "user":
            content = item.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
    return ""


def _build_initial_state(graph_def: GraphDef, user_message: str) -> dict:
    """Build the initial state dict for the first graph invocation."""
    state = {f.name: "" for f in graph_def.state_fields}
    state["input"] = user_message
    state["messages"] = [{"role": "user", "content": user_message}]
    return state


def _build_continuation_state(user_message: str) -> dict:
    """Build state for a follow-up turn (checkpointer restores prior state)."""
    return {
        "input": user_message,
        "messages": [{"role": "user", "content": user_message}],
    }


def _build_config(checkpointer, thread_id: str | None) -> dict | None:
    """Build the LangGraph config dict."""
    if checkpointer and thread_id:
        return {"configurable": {"thread_id": thread_id}}
    return None


def _get_thread_id(request: ResponsesAgentRequest) -> str:
    """Extract or generate a thread_id from the request."""
    if request.custom_inputs:
        tid = request.custom_inputs.get("thread_id")
        if tid:
            return tid
    return str(uuid.uuid4())


def _make_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


class AgentGraphModel(ResponsesAgent):
    """Wraps a compiled LangGraph agent as an MLflow ResponsesAgent for serving."""

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        """Load the graph definition and compile with optional checkpointer."""
        mlflow.langchain.autolog(log_traces=True)

        graph_def_path = context.artifacts["graph_def"]
        with open(graph_def_path) as f:
            raw = json.load(f)
        self.graph_def = GraphDef(**raw)

        # Prefer dynamic token refresh (LAKEBASE_ENDPOINT/HOST/DATABASE),
        # fall back to static connection string (LAKEBASE_CONN_STRING).
        self.checkpointer = None
        lb_endpoint = os.environ.get("LAKEBASE_ENDPOINT")
        lb_host = os.environ.get("LAKEBASE_HOST")
        lb_database = os.environ.get("LAKEBASE_DATABASE")

        if lb_endpoint and lb_host and lb_database:
            self.checkpointer = _create_lakebase_checkpointer(
                lb_endpoint, lb_host, lb_database,
            )
        else:
            conn_string = os.environ.get("LAKEBASE_CONN_STRING")
            if conn_string:
                self.checkpointer = _create_connstring_checkpointer(conn_string)

        self.compiled_graph = build_graph(
            self.graph_def, checkpointer=self.checkpointer
        )

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """Run the agent graph synchronously and return the full response."""
        user_message = _extract_user_message(request)
        if not user_message:
            return ResponsesAgentResponse(
                output=[
                    {
                        "id": _make_msg_id(),
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "No user message provided."}],
                    }
                ],
            )

        thread_id = _get_thread_id(request)
        config = _build_config(self.checkpointer, thread_id)

        # Check if this thread already has conversation history
        has_history = False
        if self.checkpointer and config:
            existing = self.compiled_graph.get_state(config)
            if existing and existing.values:
                has_history = True

        invoke_state = (
            _build_continuation_state(user_message)
            if has_history
            else _build_initial_state(self.graph_def, user_message)
        )

        result = self.compiled_graph.invoke(invoke_state, config=config)

        output, _ = filter_output(result, self.graph_def)

        # Fallback to last assistant message if filter returned empty
        if not output:
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    output = msg.get("content", "")
                    break
                elif hasattr(msg, "type") and msg.type == "ai":
                    output = msg.content
                    break

        return ResponsesAgentResponse(
            output=[
                {
                    "id": _make_msg_id(),
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": str(output)}],
                }
            ],
        )

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """Stream the agent graph execution, yielding events as each node completes."""
        user_message = _extract_user_message(request)
        if not user_message:
            yield ResponsesAgentStreamEvent(
                type="response.output_text.delta",
                delta="No user message provided.",
            )
            return

        thread_id = _get_thread_id(request)
        config = _build_config(self.checkpointer, thread_id)

        # Check if this thread already has conversation history
        has_history = False
        if self.checkpointer and config:
            existing = self.compiled_graph.get_state(config)
            if existing and existing.values:
                has_history = True

        invoke_state = (
            _build_continuation_state(user_message)
            if has_history
            else _build_initial_state(self.graph_def, user_message)
        )

        msg_id = _make_msg_id()
        output_index = 0
        content_index = 0
        full_text = ""

        # Stream node-by-node from the compiled graph
        for chunk in self.compiled_graph.stream(invoke_state, config=config):
            # Each chunk is {node_name: state_update_dict}
            for node_name, state_update in chunk.items():
                if not isinstance(state_update, dict):
                    continue

                # Extract any assistant messages produced by this node
                messages = state_update.get("messages", [])
                for msg in messages:
                    text = ""
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        text = msg.get("content", "")
                    elif hasattr(msg, "type") and msg.type == "ai":
                        text = msg.content
                    if text:
                            yield ResponsesAgentStreamEvent(
                                type="response.output_text.delta",
                                delta=str(text),
                                item_id=msg_id,
                                output_index=output_index,
                                content_index=content_index,
                            )
                            full_text += str(text)

        # Emit the completed output item
        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item={
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": full_text}],
            },
        )

        # Emit the final response.completed event
        yield ResponsesAgentStreamEvent(
            type="response.completed",
            response=ResponsesAgentResponse(
                output=[
                    {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": full_text}],
                    }
                ],
            ).model_dump(),
        )


# Register this model for MLflow "models from code" loading
mlflow.models.set_model(AgentGraphModel())
