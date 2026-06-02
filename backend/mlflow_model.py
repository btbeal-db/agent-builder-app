"""MLflow ResponsesAgent wrapper for deploying AgentSweet graphs via Model Serving.

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

import mlflow
import psycopg
from databricks.sdk import WorkspaceClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    create_text_delta,
    create_text_output_item,
)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Ensure the backend package is importable when MLflow loads this file
# from the code/ directory in the serving container.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from backend.auth import set_auth_mode, set_serving
from backend.graph_builder import build_graph, filter_output, interrupt_value, pending_interrupts
from backend.nodes.llm_node import extract_visible_text
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
    _safe_setup(checkpointer)
    logger.info("Lakebase checkpointer initialized (pool, endpoint=%s)", endpoint)
    return checkpointer


def _create_connstring_checkpointer(conn_string: str) -> PostgresSaver:
    """Fallback: create a PostgresSaver from a static connection string.

    Use this only for non-Lakebase Postgres instances or manual overrides.
    Note that Lakebase OAuth tokens embedded in the URI expire after 1 hour.
    """
    pool = ConnectionPool(
        conninfo=conn_string,
        min_size=1,
        max_size=5,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )

    checkpointer = PostgresSaver(pool)
    _safe_setup(checkpointer)
    logger.info("Lakebase checkpointer initialized (static conn string)")
    return checkpointer


def _safe_setup(checkpointer: PostgresSaver) -> None:
    """Run checkpointer.setup(), tolerating tables that already exist.

    PostgresSaver.setup() creates checkpoint tables and inserts migration
    records.  It is not fully idempotent — re-running it on an already-
    migrated database raises UniqueViolation.  This is harmless; the
    tables are ready to use.
    """
    try:
        checkpointer.setup()
    except Exception as e:
        if "unique" in str(e).lower() or "already exists" in str(e).lower():
            logger.info("Checkpoint tables already set up, skipping: %s", e)
        else:
            raise


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
    """Extract or generate a thread_id from the request.

    Checks (in order):
    1. ``context.conversation_id`` — sent by the Databricks AI Playground
    2. ``custom_inputs.thread_id`` — sent by the Agent Sweet playground
    3. Falls back to a random UUID for one-off requests.
    """
    if request.context and request.context.conversation_id:
        return request.context.conversation_id
    if request.custom_inputs:
        tid = request.custom_inputs.get("thread_id")
        if tid:
            return tid
    return str(uuid.uuid4())


def _make_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _make_resp_id() -> str:
    return f"resp_{uuid.uuid4().hex[:24]}"


def _last_ai_content(messages: list) -> str:
    """Walk the message list backwards and return the last assistant content."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return m.get("content", "")
        if isinstance(m, AIMessage) and m.content:
            return m.content
        if hasattr(m, "type") and m.type == "ai":
            return getattr(m, "content", "")
    return ""


def _text_response(text: str) -> ResponsesAgentResponse:
    """Wrap plain text as a single-message ResponsesAgentResponse."""
    return ResponsesAgentResponse(
        id=_make_resp_id(),
        output=[{
            "id": _make_msg_id(),
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": str(text)}],
        }],
    )


class AgentGraphModel(ResponsesAgent):
    """Wraps a compiled LangGraph agent as an MLflow ResponsesAgent for serving."""

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        """Load the graph definition and compile with optional checkpointer."""
        mlflow.langchain.autolog(log_traces=True)

        graph_def_path = context.artifacts["graph_def"]
        with open(graph_def_path) as f:
            raw = json.load(f)
        self.graph_def = GraphDef(**raw)

        # Set auth mode so get_data_client() returns the right client type.
        # set_serving() tells tool factories to use direct SDK calls instead
        # of MCP routing — serving credentials work with the SDK directly,
        # avoiding ~1-2s MCP protocol overhead per tool call.
        set_auth_mode(self.graph_def.auth_mode)
        set_serving(True)

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

    def _resolve_invoke_input(self, user_message: str, config: dict | None):
        """Determine the correct invoke input based on checkpoint state.

        Returns the input to pass to ``invoke()`` or ``stream()``:
        - ``Command(resume=...)`` if the thread has a pending interrupt
          (``state.next`` is non-empty after a prior interrupt)
        - Continuation state if the thread has history but no interrupt
        - Full initial state for a brand-new thread
        """
        if self.checkpointer and config:
            existing = self.compiled_graph.get_state(config)
            if existing and existing.values:
                if existing.next:
                    # Pending interrupt — resume with the user's message
                    return Command(resume=user_message)
                # Normal continuation — send new message
                return _build_continuation_state(user_message)
        return _build_initial_state(self.graph_def, user_message)

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """Run the agent graph synchronously and return the full response."""
        user_message = _extract_user_message(request)
        if not user_message:
            return _text_response("No user message provided.")

        thread_id = _get_thread_id(request)
        config = _build_config(self.checkpointer, thread_id)
        invoke_input = self._resolve_invoke_input(user_message, config)

        result = self.compiled_graph.invoke(invoke_input, config=config)

        # Human-in-the-loop interrupt. ``invoke()`` returns normally with the
        # interrupt parked on the result; ``stream_mode=["messages",…]`` keeps
        # it on the checkpoint state instead. ``pending_interrupts`` covers both.
        interrupts = result.get("__interrupt__") or pending_interrupts(self.compiled_graph, config)
        if interrupts:
            return _text_response(interrupt_value(interrupts[0]) or "Input needed")

        output, _ = filter_output(result, self.graph_def)
        if not output:
            output = _last_ai_content(result.get("messages", []))
        return _text_response(output)

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """Stream the agent graph, yielding token-level deltas from LLM nodes.

        Uses stream_mode=["messages", "updates"] so we get both:
        - AIMessageChunk tokens for real-time streaming
        - Node updates to build the final result (for non-LLM nodes)

        Follows the MLflow ResponsesAgent streaming pattern:
        https://mlflow.org/docs/latest/genai/serving/responses-agent#basic-text-streaming
        """
        user_message = _extract_user_message(request)
        if not user_message:
            msg_id = _make_msg_id()
            yield ResponsesAgentStreamEvent(
                **create_text_delta("No user message provided.", msg_id)
            )
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=create_text_output_item("No user message provided.", msg_id),
            )
            return

        thread_id = _get_thread_id(request)
        config = _build_config(self.checkpointer, thread_id)
        invoke_input = self._resolve_invoke_input(user_message, config)

        msg_id = _make_msg_id()
        streamed_parts: list[str] = []
        final_state: dict = {}

        # Track iteration boundaries: a non-chunk message between streaming
        # runs (e.g. iter-1's tool-call AIMessage, then a ToolMessage)
        # signals that the next chunk starts a new LLM iteration. Without
        # a separator iter-2's tokens get glued onto iter-1's text.
        boundary_pending = False
        for chunk in self.compiled_graph.stream(
            invoke_input, config=config,
            stream_mode=["messages", "updates"],
        ):
            mode, data = chunk

            if mode == "messages":
                msg, metadata = data
                # Only stream AIMessageChunk (incremental tokens).
                # AIMessageChunk is a subclass of AIMessage, so
                # isinstance(chunk, AIMessage) is True — but we must
                # skip plain AIMessage instances because LangGraph
                # yields the full completed message at the end of each
                # node, which would duplicate the already-streamed text.
                if type(msg) is AIMessageChunk and msg.content and not getattr(msg, "tool_calls", None):
                    text = extract_visible_text(msg.content)
                    if not text:
                        # All-reasoning chunk (gpt-oss harmony) — skip.
                        continue
                    if boundary_pending:
                        text = "\n\n" + text
                        boundary_pending = False
                    streamed_parts.append(text)
                    yield ResponsesAgentStreamEvent(
                        **create_text_delta(text, msg_id)
                    )
                elif streamed_parts:
                    boundary_pending = True

            elif mode == "updates" and isinstance(data, dict):
                # Accumulate plain state updates so non-streaming-LLM nodes
                # (structured output, Genie, VS, …) can populate the response.
                # The ``__interrupt__`` key is read separately from the
                # checkpoint state below — it arrives as a tuple, not a dict.
                for node_output in data.values():
                    if isinstance(node_output, dict):
                        final_state.update(node_output)

        # If the graph paused at a Human Input, surface the prompt.
        interrupts = pending_interrupts(self.compiled_graph, config)

        if streamed_parts:
            # LLM tokens already streamed. If we then hit an interrupt, append
            # only the *new* suffix of its prompt — the trailing question.
            # Human-input prompts often embed resolved state (e.g.
            # ``{draft_email}``) equal to what the LLM just streamed, so
            # naively appending the whole prompt would duplicate text.
            if interrupts:
                prompt = interrupt_value(interrupts[0])
                streamed_text = "".join(streamed_parts)
                if prompt and streamed_text in prompt:
                    suffix = prompt.split(streamed_text, 1)[1]
                elif prompt:
                    suffix = "\n\n" + prompt
                else:
                    suffix = ""
                if suffix.strip():
                    yield ResponsesAgentStreamEvent(**create_text_delta(suffix, msg_id))
        else:
            # Nothing streamed — non-LLM graph, structured output, or an
            # interrupt that fired before any LLM ran. Emit a single delta.
            if interrupts:
                full_text = interrupt_value(interrupts[0])
            else:
                full_text, _ = filter_output(final_state, self.graph_def)
                if not full_text:
                    full_text = _last_ai_content(final_state.get("messages", []))
            yield ResponsesAgentStreamEvent(**create_text_delta(str(full_text or ""), msg_id))

        # Do NOT emit ``response.completed`` or ``response.output_item.done`` —
        # the Databricks AI Playground previously rendered their content as
        # additional text, duplicating everything already streamed via deltas.
        # The serving layer's ``[DONE]`` sentinel is enough to end the stream.


# Register this model for MLflow "models from code" loading
mlflow.models.set_model(AgentGraphModel())
