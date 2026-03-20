"""MLflow ResponsesAgent wrapper for deploying Agent Builder graphs via Model Serving."""

from __future__ import annotations

import json
import logging
import os
import uuid

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from .graph_builder import build_graph
from .schema import GraphDef

logger = logging.getLogger(__name__)


class AgentGraphModel(ResponsesAgent):
    """Wraps a compiled LangGraph agent as an MLflow ResponsesAgent for serving."""

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        """Load the graph definition and compile with optional checkpointer."""
        graph_def_path = context.artifacts["graph_def"]
        with open(graph_def_path) as f:
            raw = json.load(f)
        self.graph_def = GraphDef(**raw)

        # Set up Lakebase checkpointer if connection string is provided
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
        """Run the agent graph and return the response.

        Databricks clients are initialized here (not in load_context) so that
        OBO (on-behalf-of) user identity is available at invocation time.
        """
        # Extract the last user message from the input list
        user_message = ""
        for item in reversed(request.input):
            if hasattr(item, "role") and item.role == "user":
                content = item.content
                # content may be a string or a list of content parts
                if isinstance(content, str):
                    user_message = content
                elif isinstance(content, list):
                    user_message = " ".join(
                        p.get("text", "") if isinstance(p, dict) else str(p)
                        for p in content
                    )
                break

        if not user_message:
            return ResponsesAgentResponse(
                output=[
                    {
                        "id": f"msg_{uuid.uuid4().hex[:24]}",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "No user message provided."}],
                    }
                ],
            )

        # Extract or generate thread_id for multi-turn memory
        thread_id = None
        if request.custom_inputs:
            thread_id = request.custom_inputs.get("thread_id")
        if thread_id is None:
            thread_id = str(uuid.uuid4())

        # Build initial state
        initial_state = {f.name: "" for f in self.graph_def.state_fields}
        initial_state["user_input"] = user_message
        initial_state["messages"] = [
            {"role": "user", "content": user_message, "node": "_start"},
        ]

        config = {}
        if self.checkpointer and thread_id:
            config["configurable"] = {"thread_id": thread_id}

        result = self.compiled_graph.invoke(
            initial_state, config=config if config else None
        )

        # Extract assistant response from messages
        output = result.get("output", result.get("user_input", ""))
        agent_messages = result.get("messages", [])
        for msg in reversed(agent_messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                output = msg.get("content", output)
                break

        return ResponsesAgentResponse(
            output=[
                {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": str(output)}],
                }
            ],
        )
