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

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

# Ensure the backend package is importable when MLflow loads this file
# from the code/ directory in the serving container.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from backend.graph_builder import build_graph
from backend.schema import GraphDef

logger = logging.getLogger(__name__)


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
    """Build the initial state dict for graph invocation."""
    state = {f.name: "" for f in graph_def.state_fields}
    state["user_input"] = user_message
    state["messages"] = [
        {"role": "user", "content": user_message, "node": "_start"},
    ]
    return state


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
        graph_def_path = context.artifacts["graph_def"]
        with open(graph_def_path) as f:
            raw = json.load(f)
        self.graph_def = GraphDef(**raw)

        self.checkpointer = None
        conn_string = os.environ.get("LAKEBASE_CONN_STRING")
        if conn_string:
            from langgraph.checkpoint.postgres import PostgresSaver

            self.checkpointer = PostgresSaver.from_conn_string(conn_string)
            self.checkpointer.setup()
            logger.info("Lakebase checkpointer initialized")

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
        initial_state = _build_initial_state(self.graph_def, user_message)
        config = _build_config(self.checkpointer, thread_id)

        result = self.compiled_graph.invoke(initial_state, config=config)

        output = result.get("output", result.get("user_input", ""))
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                output = msg.get("content", output)
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
        initial_state = _build_initial_state(self.graph_def, user_message)
        config = _build_config(self.checkpointer, thread_id)

        msg_id = _make_msg_id()
        output_index = 0
        content_index = 0
        full_text = ""

        # Stream node-by-node from the compiled graph
        for chunk in self.compiled_graph.stream(initial_state, config=config):
            # Each chunk is {node_name: state_update_dict}
            for node_name, state_update in chunk.items():
                if not isinstance(state_update, dict):
                    continue

                # Extract any assistant messages produced by this node
                messages = state_update.get("messages", [])
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        text = msg.get("content", "")
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
